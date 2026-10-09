"""Batch extraction with isolated single-PAK workspaces and a read-only asset catalog.

Never merges PAK contents, changes the game, or changes how an individual PAK
is built/signed. The batch manifest only points to verified workspaces.
"""
import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import json
import re
from types import SimpleNamespace

from evolve_pak_workspace import cmd_extract, load_workspace, normalized_name
from universal_stage import allowed_paks

BATCH_FILE = '.evolve-pak-batch.json'
BATCH_VERSION = 1
MAX_BATCH_PAKS = 30
MAX_MATERIAL_BYTES = 5 * 1024 * 1024


def _safe_workspace(batch, relative):
    directory = (batch / relative).resolve()
    if not directory.is_relative_to(batch.resolve()) or not directory.is_dir() or directory.is_symlink():
        raise ValueError('Batch workspace path is invalid or missing.')
    return directory


def _stage_file(stage, relative):
    rel = normalized_name(relative)
    root = (stage / 'paks').resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.is_symlink():
        raise ValueError('Missing or unsafe staged archive: ' + rel)
    return rel, target


def _collection_file(batch):
    return Path(batch) / BATCH_FILE


def load_collection(batch, *, require_complete=True):
    batch = Path(batch).resolve()
    data = json.loads(_collection_file(batch).read_text(encoding='utf-8'))
    if data.get('version') != BATCH_VERSION or not isinstance(data.get('archives'), list):
        raise ValueError('Invalid multi-PAK collection manifest.')
    if require_complete and data.get('state') != 'complete':
        raise ValueError('Incomplete multi-PAK extraction; do not use the collection until extraction succeeds.')
    archives = data['archives']
    if len(archives) > MAX_BATCH_PAKS:
        raise ValueError('Too many archives in collection.')
    for item in archives:
        normalized_name(item['archive'])
        ws = _safe_workspace(batch, item['workspace'])
        manifest = load_workspace(ws)
        if manifest.get('source_sha256') != item.get('source_sha256'):
            raise ValueError('Source archive fingerprint differs for ' + item['archive'])
    return data


def create_batch(stage, public_key, destination, selected, *, filters=()):
    stage = Path(stage).resolve()
    public_key = Path(public_key).resolve()
    destination = Path(destination).resolve()
    selected = [normalized_name(s) for s in selected]
    if len(selected) < 2 or len(selected) > MAX_BATCH_PAKS:
        raise ValueError('Select between 2 and 30 PAKs for a batch.')
    if len({s.casefold() for s in selected}) != len(selected):
        raise ValueError('Duplicate PAK selection.')
    if destination.exists():
        raise FileExistsError('Batch destination exists. Choose a fresh directory.')
    if destination.is_relative_to(stage) or stage.is_relative_to(destination):
        raise ValueError('Batch workspace cannot be inside the signing stage.')
    if not public_key.is_file():
        raise FileNotFoundError('Signing public key is missing.')
    permitted = allowed_paks(stage)
    names = {v.casefold(): v for v in permitted.values()}
    sources=[]
    for name in selected:
        if name.casefold() not in names:
            raise ValueError('PAK is not in the verified signed staging plan: ' + name)
        sources.append(_stage_file(stage, names[name.casefold()]))
    if filters:
        raise ValueError('Batch asset linking needs complete file inventories; clear the extraction filter.')
    destination.mkdir(parents=True)
    status = {'version': BATCH_VERSION, 'state': 'extracting',
              'created_at': datetime.now().isoformat(timespec='seconds'),
              'stage': str(stage), 'archives': []}
    _collection_file(destination).write_text(json.dumps(status, indent=2), encoding='utf-8')
    try:
        for index, (rel, source) in enumerate(sources, 1):
            # A stable number and relative PAK path keep collisions impossible.
            # Each workspace retains its own original signed source/manifest.
            safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', rel)[:90]
            child = f'archives/{index:02d}_{safe_name}'
            ws = destination / child
            print(f'[{index}/{len(sources)}] Extracting {rel}', flush=True)
            cmd_extract(SimpleNamespace(pak=source, public_key=public_key,
                                        workspace=ws, skip_unsupported=True, filter=[]))
            manifest = load_workspace(ws)
            if Path(manifest['source_pak']).resolve() != source:
                raise ValueError('Batch workspace source does not match the selected archive.')
            status['archives'].append({'archive': rel, 'workspace': child,
                                       'source_sha256': manifest['source_sha256'],
                                       'file_count': len(manifest['entries'])})
            _collection_file(destination).write_text(json.dumps(status, indent=2), encoding='utf-8')
        status['state'] = 'complete'
        _collection_file(destination).write_text(json.dumps(status, indent=2), encoding='utf-8')
    except Exception:
        # Do not erase partial workspaces; they are useful for diagnostics.
        print('Batch extraction incomplete. Original staged PAKs are unchanged.', flush=True)
        raise
    print(f'EXTRACTED {len(sources)} independent PAK workspaces to {destination}', flush=True)
    print('Each PAK must still be reviewed, rebuilt and signed independently.', flush=True)
    return status


def collection_for_workspace(workspace):
    workspace = Path(workspace).resolve()
    batch = workspace.parent.parent
    if not _collection_file(batch).is_file():
        return None
    data = load_collection(batch)
    if not any(_safe_workspace(batch, item['workspace']) == workspace for item in data['archives']):
        return None
    return batch, data


def _asset_index(batch, data):
    result = defaultdict(list)
    for item in data['archives']:
        workspace = _safe_workspace(batch, item['workspace'])
        for entry in load_workspace(workspace)['entries']:
            rel = normalized_name(entry['path'])
            path = (workspace/'files'/rel).resolve()
            if not path.is_relative_to((workspace/'files').resolve()) or not path.is_file() or path.is_symlink():
                continue
            result[rel.casefold()].append({'archive': item['archive'], 'workspace': workspace,
                                           'relative': rel, 'file': path})
    return result


def _candidates(ref):
    """CryEngine .tif material references generally point to cooked DDS assets."""
    rel = normalized_name(ref.strip().replace('\\', '/'))
    opts=[rel]
    if rel.casefold().endswith('.tif'):
        opts += [rel[:-4]+'.dds', rel[:-4]+'.dds.0']
    elif rel.casefold().endswith('.dds'):
        opts += [rel+'.0']
    return opts


def _resolve(index, ref):
    try:
        candidates = _candidates(ref)
    except ValueError:
        return [], 'unsafe-path'
    for name in candidates:
        hits = index.get(name.casefold(), [])
        if len(hits) > 1:
            return hits, 'ambiguous'
        if hits:
            return hits, 'found'
    return [], 'missing'


def locate_batch_asset(workspace, virtual_path):
    """Read-only lookup for a uniquely named asset from another batch workspace.

    Never guesses between multiple PAKs containing the same virtual path.
    The returned path is contained inside a checked extracted workspace.
    """
    lookup = collection_for_workspace(workspace)
    if lookup is None:
        return None
    batch, data = lookup
    hits, state = _resolve(_asset_index(batch, data), virtual_path)
    if state == 'ambiguous':
        raise ValueError('Companion asset exists in multiple PAKs. Choose one explicitly: ' + virtual_path)
    return hits[0]['file'] if state == 'found' else None


def model_material_links(workspace, model_relative):
    """Locate material texture references across a batch, WITHOUT applying shaders.

    Results are for the UI and manual selection only. Ambiguous paths are never
    silently assigned to a mesh and source assets are not modified.
    """
    from xml.etree import ElementTree as ET
    workspace = Path(workspace).resolve()
    normalized_name(model_relative)
    lookup = collection_for_workspace(workspace)
    if lookup:
        batch, data = lookup
        index = _asset_index(batch, data)
    else:
        index = defaultdict(list)
        for item in load_workspace(workspace)['entries']:
            rel = normalized_name(item['path'])
            candidate = (workspace/'files'/rel).resolve()
            if candidate.is_relative_to((workspace/'files').resolve()) and candidate.is_file():
                index[rel.casefold()].append({'archive': '(current PAK)', 'workspace': workspace,
                                             'relative': rel, 'file': candidate})
    stem=Path(model_relative).with_suffix('').as_posix()
    if stem.endswith('m') and Path(model_relative).suffix.lower() in ('.skinm','.chrm','.cgfm','.cgam'):
        pass  # suffix removal already handled by Path.with_suffix
    bases = [stem]
    # LOD model files often use the base model's material.
    clean = re.sub(r'_lod\d+$', '', stem, flags=re.IGNORECASE)
    if clean not in bases:
        bases.append(clean)
    possible=[]
    for base in bases:
        possible.append(base+'.mtl')
    material=None; state='missing'
    for path in possible:
        hits,state=_resolve(index,path)
        if state == 'found':
            material=hits[0]
            break
        if state=='ambiguous':
            return {'material':path,'status':'ambiguous','textures':[],'collection':bool(lookup)}
    if material is None:
        return {'material':', '.join(possible), 'status':'missing', 'textures':[], 'collection':bool(lookup)}
    raw=material['file'].read_bytes()
    if len(raw)>MAX_MATERIAL_BYTES or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        return {'material':material['relative'],'status':'invalid-material','textures':[], 'collection':bool(lookup)}
    try:
        root=ET.fromstring(raw)
    except ET.ParseError:
        return {'material':material['relative'],'status':'invalid-material','textures':[], 'collection':bool(lookup)}
    textures=[]
    seen=set()
    for node in root.iter():
        if node.tag.casefold() != 'texture':
            continue
        ref=node.get('File') or node.get('file')
        if not ref:
            continue
        kind=node.get('Map') or node.get('map') or 'Unknown'
        signature=(kind.casefold(),ref.casefold())
        if signature in seen:
            continue
        seen.add(signature)
        matches,resolution = _resolve(index, ref)
        textures.append({'map':kind, 'reference':ref, 'status':resolution,
                         'archives':[m['archive'] for m in matches],
                         'paths':[m['relative'] for m in matches]})
    return {'material':material['relative'],'material_archive':material['archive'],
            'status':'found','textures':textures,'collection':bool(lookup)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--public-key',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--pak',action='append',required=True)
    args=p.parse_args()
    create_batch(args.stage,args.public_key,args.destination,args.pak)


if __name__=='__main__':
    main()
