"""Mod Manager 1.1.15 workflow primitives: no direct installed-game writes.

Project inventory, safe conflict analysis, self-invalidating asset inventory,
validated XML presets, global replacement previews and recovery snapshots.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from xml.etree import ElementTree
from evolve_pak_workspace import load_workspace, get_changes, normalized_name
from workspace_editor import save_text, digest, MAX_TEXT, validate_xml
from workspace_file_actions import checked_path, sha_file
from studio_tools import numeric_fields, apply_numeric_edits

MAX_FILES=3000
MAX_REPLACE=100
MAX_PRESET=200


def workspace_summary(workspace):
    root=Path(workspace).resolve()
    manifest=load_workspace(root)
    records=manifest['entries']
    changes=get_changes(root,manifest)
    used=0
    for folder,dirs,files in os.walk(root,followlinks=False):
        for name in files:
            file=Path(folder)/name
            if file.is_symlink():
                continue
            used+=file.stat().st_size
    return {'workspace':str(root),'source':Path(manifest['source_pak']).name,
            'files':len(records),'modified':len(changes),'disk_bytes':used,
            'last_activity':max((p.stat().st_mtime for _,p in changes),default=root.stat().st_mtime)}


def project_conflicts(first,second,*,limit=MAX_FILES):
    """Path collisions distinguish equal bytes, differing assets and XML attrs."""
    lroot,rroot=Path(first),Path(second)
    left={x['path'].casefold():x for x in load_workspace(lroot)['entries']}
    right={x['path'].casefold():x for x in load_workspace(rroot)['entries']}
    overlaps=sorted(set(left)&set(right))
    result=[]
    for key in overlaps[:limit]:
        a,b=left[key],right[key]
        source=checked_path(lroot,a['path'])
        target=checked_path(rroot,b['path'])
        if sha_file(source)==sha_file(target):
            continue
        keys=()
        if a['path'].lower().endswith(('.xml','.mtl','.cdf','.chrparams')):
            try:
                x=numeric_fields(source.read_text('utf-8-sig'))
                y=numeric_fields(target.read_text('utf-8-sig'))
                mapping={(f.tag,f.attribute,f.element.split('[')[0]):f.value for f in x}
                keys=tuple(sorted(set((f.tag,f.attribute) for f in y
                    if mapping.get((f.tag,f.attribute,f.tag)) not in (None,f.value))))[:50]
            except (ValueError,UnicodeError,OSError):
                pass
        result.append({'path':b['path'],'kind':'XML values' if keys else 'Binary/file difference',
                       'properties':keys})
    return tuple(result)


def _atomic_json(path, data):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.studio-',suffix='.tmp',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(data,out,ensure_ascii=False,indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp,target)
    finally:
        Path(temp).unlink(missing_ok=True)


def cache_asset_manifest(workspace, cache_root):
    """JSON cache is safe, portable and invalidated by manifest timestamp/hash."""
    root=Path(workspace).resolve()
    manifest_path=root/'.evolve-pak-workspace.json'
    manifest=load_workspace(root)
    fingerprint=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    location=Path(cache_root)/('assets-'+hashlib.sha256(str(root).encode()).hexdigest()[:28]+'.json')
    if location.is_file() and location.stat().st_size<15*1024*1024:
        try:
            prior=json.loads(location.read_text('utf-8'))
            if (prior.get('hash')==fingerprint and prior.get('root')==str(root)
                    and isinstance(prior.get('assets'),list)):
                return tuple(prior['assets']),True
        except (OSError,ValueError):
            pass
    assets=[{'path':row['path'],'type':Path(row['path']).suffix.casefold()}
            for row in manifest['entries']]
    if len(assets)>150000:
        raise ValueError('Archive inventory too large for caching')
    _atomic_json(location,{'hash':fingerprint,'root':str(root),'assets':assets})
    return tuple(assets),False


def preset_from_xml(xml_text, edits):
    fields=numeric_fields(xml_text)
    replacements={int(key):str(value) for key,value in edits.items()}
    # Validating edits here prevents exporting a preset that could not apply.
    apply_numeric_edits(xml_text,replacements)
    return {'version':1,'format':'evolve-gameplay-preset',
            'source_sha256':hashlib.sha256(xml_text.encode('utf-8')).hexdigest(),
            'values':[{'tag':fields[idx].tag,'attribute':fields[idx].attribute,
                       'ordinal':idx,'old':fields[idx].value,'new':value}
                      for idx,value in replacements.items()]}


def apply_preset(xml_text,preset):
    if preset.get('format')!='evolve-gameplay-preset' or preset.get('version')!=1:
        raise ValueError('Not an Evolve gameplay preset')
    values=preset.get('values')
    if not isinstance(values,list) or len(values)>MAX_PRESET:
        raise ValueError('Malformed or too large gameplay preset')
    if hashlib.sha256(xml_text.encode()).hexdigest()!=preset.get('source_sha256'):
        raise ValueError('Source XML differs from preset baseline; compare manually')
    fields=numeric_fields(xml_text)
    edited={}
    for item in values:
        idx=item.get('ordinal')
        if (not isinstance(idx,int) or idx<0 or idx>=len(fields)
                or fields[idx].tag!=item.get('tag')
                or fields[idx].attribute!=item.get('attribute')
                or fields[idx].value!=item.get('old')):
            raise ValueError('Gameplay property mismatch; cannot apply preset')
        edited[idx]=str(item['new'])
    return apply_numeric_edits(xml_text,edited)


def search_text(workspace,needle,*,case_sensitive=False,limit=2000):
    if not needle or len(needle)>256:
        raise ValueError('Choose search text up to 256 characters')
    root=Path(workspace)
    results=[]
    records=load_workspace(root)['entries']
    for row in records[:150000]:
        path=row['path']
        if not path.casefold().endswith(('.xml','.mtl','.chrparams','.cdf','.txt','.json','.cfg')):
            continue
        file=checked_path(root,path)
        if file.stat().st_size>MAX_TEXT:
            continue
        try:
            lines=file.read_text('utf-8-sig').splitlines()
        except UnicodeError:
            continue
        for i,line in enumerate(lines,1):
            match=needle in line if case_sensitive else needle.casefold() in line.casefold()
            if match:
                results.append((path,i,line[:240]))
                if len(results)>=limit:
                    return tuple(results)
    return tuple(results)


def replace_text_preview(workspace,old,new,selected):
    """Preview only; apply is separately confirmed and backs up each file."""
    if not old or len(old)>256 or len(new)>256:
        raise ValueError('Search and replacement length limits exceeded')
    if len(selected)>MAX_REPLACE:
        raise ValueError('Replace at most 100 files per operation')
    planned=[]
    root=Path(workspace)
    allowed={row['path'] for row in load_workspace(root)['entries']}
    for rel in selected:
        rel=normalized_name(rel)
        if rel not in allowed or not rel.lower().endswith(('.xml','.mtl','.txt','.cfg')):
            raise ValueError('Unsupported replacement target: '+rel)
        file=checked_path(root,rel)
        if file.stat().st_size>MAX_TEXT:
            raise ValueError('File exceeds text editing limits')
        content=file.read_text('utf-8-sig')
        updated=content.replace(old,new)
        if content==updated:
            continue
        if rel.lower().endswith(('.xml','.mtl')):
            validate_xml(updated.encode(),content.encode())
        planned.append((rel,content,updated,hashlib.sha256(file.read_bytes()).hexdigest()))
    return tuple(planned)


def apply_text_preview(workspace,plan):
    completed=[]
    for rel,old,new,expected in plan:
        source=checked_path(workspace,rel)
        original=source.read_bytes()
        if hashlib.sha256(original).hexdigest()!=expected:
            raise ValueError('File changed since preview: '+rel)
        save_text(workspace,rel,new,digest(original),original,False)
        completed.append(rel)
    return tuple(completed)


def recovery_path(workspace,relative):
    """Unsaved recovery data stays outside extracted asset files."""
    rel=normalized_name(relative)
    root=Path(workspace).resolve()
    slug=hashlib.sha256(rel.encode()).hexdigest()
    return root/'EditorRecovery'/ (slug+'.json')


def save_recovery(workspace,relative,text,baseline):
    if len(text.encode('utf-8'))>MAX_TEXT:
        raise ValueError('Autosave text exceeds editor limit')
    path=recovery_path(workspace,relative)
    _atomic_json(path,{'version':1,'asset':relative,'text':text,
                       'original_sha256':hashlib.sha256(baseline).hexdigest(),
                       'saved_at':time.time()})
    return path


def read_recovery(workspace,relative,current):
    path=recovery_path(workspace,relative)
    if not path.is_file() or path.stat().st_size>MAX_TEXT*2:
        return None
    obj=json.loads(path.read_text('utf-8'))
    if (obj.get('version')!=1 or obj.get('asset')!=relative
            or obj.get('original_sha256')!=hashlib.sha256(current).hexdigest()):
        return None
    return obj.get('text')


def clear_recovery(workspace,relative):
    recovery_path(workspace,relative).unlink(missing_ok=True)
