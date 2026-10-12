"""v1.1.17 studio polish: bounded preparation cache, preflight, themes and sessions.

Never stores source PAK bytes or a live game memory address in user preferences.
Cache entries are ephemeral and invalidated when dependency fingerprints change.
"""
from __future__ import annotations
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
import json
import math
import os
import threading
import time

from qt_theme_116 import PALETTES, ROLES, palette

MODEL_CACHE_LIMIT = 3
MODEL_CACHE_TTL = 30
MAX_CACHE_BYTES = 384 * 1024 * 1024
MAX_THEME_BYTES = 16384
MAX_SESSION_COUNT = 80
_cache = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.RLock()


def model_cache_key(workspace, relative, *, projects_root=None, stage_root=None,
                    texture_folder=None, equipment=None, animation_index=None,
                    index=None, require_assembly=False):
    """Cheap signature of the project, source asset and optional loose texture tree.

    Related extracted workspaces can be edited independently; TTL bounds
    staleness, and manual refresh explicitly bypasses the cache.
    """
    root = Path(workspace).resolve()
    path = (root / 'files' / relative).resolve()
    if not path.is_relative_to((root/'files').resolve()):
        raise ValueError('Unsafe model path')
    stat = path.stat()
    # Editable projects use the signing workspace manifest; imported
    # read-only Asset Library PAKs use their own distinct manifest.
    manifest_path = root / '.evolve-pak-workspace.json'
    if not manifest_path.is_file():
        manifest_path = root / 'asset-library.json'
    manifest = manifest_path.stat()
    def folder_marker(folder):
        if not folder:
            return None
        p=Path(folder)
        try:
            s=p.stat()
            return (str(p.resolve()),s.st_mtime_ns)
        except OSError:
            return (str(p),None)
    # An explicitly opened external animation index is not cached; it can
    # change without corresponding workspace/manifest updates.
    return (str(root),relative,stat.st_size,stat.st_mtime_ns,
            manifest.st_size,manifest.st_mtime_ns,str(equipment),id(index) if index is not None else None,
            folder_marker(texture_folder),folder_marker(projects_root),
            folder_marker(stage_root),animation_index is None,
            bool(require_assembly))


def _result_size(result):
    total=len(result.get('raw',b''))
    geo=result.get('geometry')
    if geo is not None:
        for name in ('vertices','normals','uvs','indices'):
            item=getattr(geo,name,None)
            total+=int(getattr(item,'nbytes',0))
    mats=result.get('materials')
    if mats is not None:
        for group in ('textures','normal_maps','specular_maps'):
            for im in getattr(mats,group,()):
                if im is not None:
                    total+=im.width*im.height*len(im.getbands())
    return total


def _detached(result):
    """Animation playback writes to vertices/normals; never mutate cache arrays."""
    out=dict(result)
    geometry=out.get('geometry')
    if geometry is not None:
        from dataclasses import replace
        out['geometry']=replace(geometry,vertices=geometry.vertices.copy(),
                                normals=geometry.normals.copy())
    return out


def prepare_model_cached(workspace,relative,*,force=False,**kwargs):
    """Return a detached result mapping, cache hit and wall-clock duration."""
    global _cache_bytes
    from async_model_resolve import resolve_model
    start=time.perf_counter()
    key=model_cache_key(workspace,relative,**kwargs)
    eligible=kwargs.get('animation_index') is None
    # Caller-provided indexes may change without detectable file modification.
    if not force and eligible:
        with _cache_lock:
            hit=_cache.get(key)
            if hit is not None and time.monotonic()-hit[0] < MODEL_CACHE_TTL:
                _cache.move_to_end(key)
                return _detached(hit[1]),True,(time.perf_counter()-start)*1000
    result=resolve_model(workspace,relative,**kwargs)
    size=_result_size(result)
    if eligible and size<=MAX_CACHE_BYTES//2:
        with _cache_lock:
            old=_cache.pop(key,None)
            if old is not None:
                _cache_bytes-=old[2]
            _cache[key]=(time.monotonic(),_detached(result),size)
            _cache_bytes+=size
            while len(_cache)>MODEL_CACHE_LIMIT or _cache_bytes>MAX_CACHE_BYTES:
                _,entry=_cache.popitem(last=False)
                _cache_bytes-=entry[2]
    return result,False,(time.perf_counter()-start)*1000


def clear_model_cache(workspace=None):
    global _cache_bytes
    with _cache_lock:
        if workspace is None:
            _cache.clear()
            _cache_bytes=0
        else:
            root=str(Path(workspace).resolve())
            for key in list(_cache):
                if key[0]==root:
                    _cache_bytes-=_cache.pop(key)[2]


def model_cache_stats():
    with _cache_lock:
        return {'entries':len(_cache),'retained_mib':round(_cache_bytes/1048576,2),
                'limit_mib':round(MAX_CACHE_BYTES/1048576,2),
                'max_age_seconds':MODEL_CACHE_TTL}


def export_preflight(mesh,workspace,relative,*,texture_folder=None,
                     projects_root=None,stage_root=None,animation_refs=()):
    """Actually encode GLB and inspect supported rig/material features.

    Animation availability is a candidate count, NOT a promise CAF decoding
    succeeds. Final animated export still validates controllers and keyframes.
    """
    from model_textured_export import textured_model_from_workspace, _unpack_glb
    from model_rigging import skin_companion_path
    from multi_pak_assets import model_material_links
    payload,report=textured_model_from_workspace(
        mesh,workspace,relative,texture_folder=texture_folder,
        projects_root=projects_root,stage_root=stage_root)
    doc,_=_unpack_glb(payload)
    animations=[r for r in animation_refs
                if getattr(r,'available',False) and getattr(r,'kind',None)=='caf'
                and not any(word in r.name.casefold() for word in ('additive','aimpose'))]
    warnings=[]
    if report['texture_count']==0:
        warnings.append('No diffuse textures were embedded: '+report['texture_status'])
    if not doc.get('skins'):
        warnings.append('This GLB is not rigged: '+report['rig_status'])
    if not animations:
        warnings.append('No independently playable native CAF candidates were found.')
    dependencies=None
    try:
        dependencies=model_material_links(workspace,relative,projects_root,stage_root)
    except (OSError,ValueError):
        pass
    if dependencies and dependencies.get('status') in ('missing','ambiguous'):
        warnings.append('Material dependency: '+dependencies['status'])
    return {'format':'evolve-export-preflight-v1',
            'model':relative,'vertices':len(mesh.vertices),
            'triangles':len(mesh.triangles),'glb_bytes':len(payload),
            'textures':report['texture_count'],
            'rig':report['rig_status'],'bones':report.get('bone_count',0),
            'animation_candidates':len(animations),
            'animation_note':'Candidates only; actual animated export performs CAF decode and compatibility checks.',
            'texture_note':report['texture_status'],
            'material_dependency':dependencies.get('status') if dependencies else 'not resolved',
            'warnings':warnings}


def theme_preset(name,mode,colors):
    if not isinstance(name,str) or not 1<=len(name.strip())<=45 or any(ord(x)<32 for x in name):
        raise ValueError('Theme name must be 1–45 printable characters.')
    validated=palette(mode,colors)
    return {'format':'evolve-theme-preset','version':1,'name':name.strip(),
            'mode':mode,'colors':validated}


def validate_theme_preset(raw):
    if not isinstance(raw,dict) or raw.get('format')!='evolve-theme-preset' or raw.get('version')!=1:
        raise ValueError('Not an Evolve theme preset.')
    if not isinstance(raw.get('colors'),dict) or set(raw['colors'])!=set(ROLES):
        raise ValueError('Theme needs every editable color.')
    return theme_preset(raw.get('name'),raw.get('mode'),raw['colors'])


def read_theme_file(path):
    source=Path(path)
    if source.is_symlink() or source.stat().st_size>MAX_THEME_BYTES:
        raise ValueError('Theme file is a symlink or exceeds 16 KiB.')
    return validate_theme_preset(json.loads(source.read_text('utf-8')))


def project_session_key(workspace):
    return sha256(str(Path(workspace).resolve()).casefold().encode('utf-8')).hexdigest()[:24]


def sanitized_project_session(session, records):
    """Validate stored settings before reopening any editor file or camera state."""
    if not isinstance(session,dict):
        return {}
    allowed=set(records)
    selected=session.get('selected',{})
    result={'tab':int(session['tab']) if type(session.get('tab')) is int and 0<=session['tab']<=2 else 0,
            'selected':{k:v for k,v in (selected.items() if isinstance(selected,dict) else [])
                        if k in ('files','images','models') and isinstance(v,str) and v in allowed}}
    sizes=session.get('sizes')
    if isinstance(sizes,dict):
        result['sizes']={name:[int(a),int(b)] for name,pair in sizes.items()
                         if name in ('files','images','models')
                         and isinstance(pair,list) and len(pair)==2
                         for a,b in [pair] if type(a) is int and type(b) is int
                         and 0<a<100000 and 0<b<100000}
    camera=session.get('camera')
    if isinstance(camera,dict):
        numeric=('rotation','pan')
        safe={}
        for name in numeric:
            pair=camera.get(name)
            if (isinstance(pair,list) and len(pair)==2
                    and all(type(v) in (int,float) and math.isfinite(v) and -360<=v<=360 for v in pair)):
                safe[name]=[float(v) for v in pair]
        zoom=camera.get('zoom')
        if type(zoom) in (int,float) and math.isfinite(zoom) and 0.18<=zoom<=5.0:
            safe['zoom']=float(zoom)
        result['camera']=safe
    return result
