"""Read-only asset intelligence and guarded project revision operations."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat

from evolve_pak_workspace import load_workspace, get_changes, normalized_name
from workspace_file_actions import checked_path, sha_file, replace_raw_file
from workspace_editor import save_text, validate_xml, digest, MAX_TEXT
from dds_texture import is_dds, is_split_dds, parse_dds
from model_asset import is_model, inspect_model


def compare_projects(left, right, *, changed_only=True, limit=4000):
    """Compare two original PAK extraction manifests and their current bytes."""
    old, new = load_workspace(Path(left)), load_workspace(Path(right))
    a={r['path'].casefold():r for r in old['entries']}
    b={r['path'].casefold():r for r in new['entries']}
    results=[]
    for key in sorted(a.keys()|b.keys()):
        first,second=a.get(key),b.get(key)
        if first is None:
            state='Added'
        elif second is None:
            state='Removed'
        else:
            file1=checked_path(left,first['path'])
            file2=checked_path(right,second['path'])
            state='Unchanged' if sha_file(file1)==sha_file(file2) else 'Modified'
        if changed_only and state=='Unchanged':
            continue
        results.append((state,(second or first)['path']))
        if len(results)>=limit:
            break
    return tuple(results)


def text_difference(left, right, relative, *, max_bytes=MAX_TEXT):
    rel=normalized_name(relative)
    a,b=checked_path(left,rel),checked_path(right,rel)
    if a.stat().st_size>max_bytes or b.stat().st_size>max_bytes:
        raise ValueError('Text diff limited to 5 MiB per file')
    before=a.read_bytes().decode('utf-8-sig')
    after=b.read_bytes().decode('utf-8-sig')
    if '\0' in before or '\0' in after:
        raise ValueError('File is binary; view its changed checksum instead')
    return '\n'.join(difflib.unified_diff(
        before.splitlines(),after.splitlines(),
        fromfile=str(left),tofile=str(right),lineterm=''))


def dependency_report(workspace, model, projects_root=None, stage_root=None):
    """Create an edge list for verified material/texture/animation paths."""
    from multi_pak_assets import model_material_links
    rel=normalized_name(model)
    report=model_material_links(workspace,rel,
        projects_root=projects_root,stage_root=stage_root)
    edges=[]
    material=report.get('material','')
    if report.get('status')=='found':
        edges.append((rel,material,'material'))
        for item in report.get('textures',[])[:200]:
            edges.append((material,item['reference'],item['status']))
    from material_preview import _workspace_index
    from character_animation_catalog import discover_animations
    index=_workspace_index(workspace,projects_root,stage_root)
    refs,idle,_=discover_animations(
        rel,index,preferred_workspace=workspace)
    for item in refs[:120]:
        edges.append((rel,item.virtual_path,
                      'CAF available' if item.available else 'animation missing'))
    return tuple(edges), report


def texture_suggestions(workspace, model, projects_root=None, stage_root=None):
    """Suggest possible DDS replacements; never relink a material implicitly."""
    from material_preview import _workspace_index
    from multi_pak_assets import model_material_links
    index=_workspace_index(workspace,projects_root,stage_root)
    links=model_material_links(workspace,model,projects_root,stage_root)
    suggestions=[]
    for texture in links.get('textures',[])[:150]:
        if texture['status']=='found':
            continue
        ref=texture['reference'].replace('\\','/')
        name=Path(ref).stem.casefold()
        expected={name+'.dds',name+'.dds.0'}
        candidates=[]
        for key in index:
            if Path(key).name.casefold() in expected:
                candidates.append(key)
                if len(candidates)>15:
                    break
        suggestions.append((ref,texture['status'],tuple(candidates)))
    return tuple(suggestions)


def revisions(workspace, relative, *, limit=100):
    """List only path-matching immutable snapshots inside EditorBackups."""
    rel=normalized_name(relative)
    root=(Path(workspace)/'EditorBackups').resolve()
    if not root.is_dir():
        return ()
    found=[]
    for entry in root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        original=(entry/rel).resolve()
        if original.is_relative_to(root) and original.is_file() and not original.is_symlink():
            stat=original.stat()
            found.append((original,stat.st_mtime,stat.st_size))
    return tuple(sorted(found,key=lambda v:v[1],reverse=True)[:limit])


def restore_revision(workspace, relative, snapshot):
    """Explicit restore of backed up XML or format-compatible binary.

    All writes go through the existing checksum/backup paths; DDS streaming
    requires its dedicated image editor rather than a partial restore.
    """
    rel=normalized_name(relative)
    options={str(p.resolve()):p for p,_,_ in revisions(workspace,rel,limit=300)}
    snapshot=Path(snapshot).resolve()
    if str(snapshot) not in options:
        raise ValueError('Revision is not a verified editor backup for this file')
    current=checked_path(workspace,rel)
    original=current.read_bytes()
    if is_split_dds(rel):
        raise ValueError('Split DDS requires a whole-stream restore in the texture editor')
    if Path(rel).suffix.casefold() in ('.xml','.mtl','.cdf','.chrparams'):
        text=snapshot.read_bytes().decode('utf-8-sig')
        if len(text.encode('utf-8'))>MAX_TEXT:
            raise ValueError('Revision too large for text editor')
        save_text(workspace,rel,text,digest(original),original,
                  Path(rel).suffix.casefold()=='.xml')
    elif is_dds(rel):
        from dds_texture import replace_dds
        replace_dds(workspace,rel,snapshot,sha_file(current))
    else:
        replace_raw_file(workspace,rel,snapshot,sha_file(current))
    return True


def validate_project(workspace, *, max_changed=500):
    """Conservative mod preflight: modified files and parseable original types."""
    ws=Path(workspace)
    manifest=load_workspace(ws)
    problems=[]
    modified=[]
    try:
        changed=get_changes(ws,manifest)
    except (OSError,ValueError) as exc:
        return (), ('Extraction damaged: '+str(exc),)
    if len(changed)>max_changed:
        problems.append('Too many modified files for one preflight run')
        changed=changed[:max_changed]
    for entry,path in changed:
        rel=entry['path']
        modified.append(rel)
        try:
            if path.stat().st_size>512*1024*1024:
                raise ValueError('Modified asset is over 512 MiB')
            if entry.get('format')=='cryxml':
                validate_xml(path.read_bytes(),
                             original=None)
            elif rel.casefold().endswith(('.xml','.mtl','.cdf','.chrparams')):
                validate_xml(path.read_bytes())
            elif is_dds(rel) and not is_split_dds(rel):
                parse_dds(path.read_bytes())
            elif is_model(rel):
                inspect_model(path.read_bytes())
        except (OSError,ValueError,ET.ParseError,expat.ExpatError) as exc:
            problems.append(rel+': '+str(exc)[:150])
    return tuple(modified),tuple(problems)
