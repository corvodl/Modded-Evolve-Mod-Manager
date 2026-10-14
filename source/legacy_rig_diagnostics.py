"""Local-only 2012 CryTek rig diagnostic.

Examples (PowerShell):
  py -3.11 source/legacy_rig_diagnostics.py --body C:\...\ParasiteHost3.chr --skeleton C:\...\ParasiteHost3_Skeleton.chr --dba C:\...\ParasiteHost3.dba --report C:\Temp\parasitehost-rig.json
  py -3.11 source/legacy_rig_diagnostics.py --body ... --skeleton ... --export-glb C:\Temp\ParasiteHost3-rig.glb

The report contains only derived metadata and SHA-256 digests, not source bytes.
A validated rig is NOT proof of working DBA playback or in-game compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

import numpy as np

from legacy_crytek_mesh import read_crytek_geometry
from legacy_crytek_rig import (_one_chunk, SUBSETS_CHUNK, read_legacy_rig,
                                probe_legacy_dba)
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from legacy_crytek_rig import skin_glb_legacy
from model_rigging import validate_skin_rest_pose


def _bytes(path, maximum=256*1024*1024):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > maximum:
        raise ValueError('Unsafe, missing or oversized input: ' + str(path))
    return p.read_bytes()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _synthetic_deformation(mesh, rig):
    # This is an artificial 15-degree joint motion to test weight effects,
    # NOT a clip decoded from the user's animation database.
    active = rig.active_joints
    candidate = next((i for i in active if rig.parents[i] >= 0), None)
    if candidate is None:
        return {'status': 'not sampled', 'reason': 'No non-root weighted joint'}
    import math
    ang = math.radians(15.0)
    motion = np.eye(4)
    motion[:2,:2] = [[math.cos(ang), -math.sin(ang)],
                      [math.sin(ang), math.cos(ang)]]
    chosen = rig.rest[candidate] @ motion @ rig.inverse[candidate]
    positions = np.asarray(mesh.vertices, dtype=np.float64)
    sampled = []
    for index in np.flatnonzero((rig.joints == candidate).any(axis=1))[:16]:
        point = np.append(positions[index],1.)
        after = np.zeros(4)
        for bone,weight in zip(rig.joints[index],rig.weights[index]):
            if weight == 0: continue
            transform = chosen if bone == candidate else np.eye(4)
            after += transform @ point * (int(weight)/255.)
        sampled.append(float(np.linalg.norm(after[:3]-point[:3])))
    return {'status': 'synthetic sample only',
            'bone': rig.names[candidate], 'degrees': 15.0,
            'vertices_tested': len(sampled),
            'minimum_displacement': min(sampled) if sampled else None,
            'maximum_displacement': max(sampled) if sampled else None}


def _palette_report(body, rig):
    at, size = _one_chunk(body, SUBSETS_CHUNK)
    count, = struct.unpack_from('<I',body,at+20)
    result = []
    by_name = {name.casefold():i for i,name in enumerate(rig.names)}
    for n in range(count):
        first, length, _, _, material = struct.unpack_from('<5I',body,at+32+n*36)
        palette_at = at+32+count*36+n*260
        num, = struct.unpack_from('<I',body,palette_at)
        values = struct.unpack_from('<'+str(num)+'H',body,palette_at+4)
        mappings = []
        for body_id in values:
            name = rig.body_bones[body_id]
            mappings.append({'body_index':body_id,'bone_name':name,
                             'master_index':by_name[name.casefold()]})
        result.append({'subset':n,'first_index':first,'index_count':length,
                       'material_slot':material,'palette':mappings})
    return result


def _animation_config(data):
    """Metadata-only 2012 chrparams: never execute game-supplied scripts."""
    from xml.etree import ElementTree as ET
    if (len(data) > 2 * 1024 * 1024 or
            b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper()):
        raise ValueError('Unsafe or oversized historical animation configuration.')
    try:
        root=ET.fromstring(data)
    except ET.ParseError as exc:
        return {'status':'cannot parse as XML','reason':str(exc)[:180]}
    entries=root.findall('.//AnimationList/Animation')
    if len(entries)>8192:
        raise ValueError('Historical animation alias table too large.')
    databases=[]
    paths=[]
    aliases=[]
    for row in entries:
        name=(row.get('name') or '').strip()
        path=(row.get('path') or '').strip()
        if name.casefold()=='$tracksdatabase':
            databases.append(path)
        elif name.casefold()=='#filepath':
            paths.append(path)
        elif name and path:
            aliases.append({'name':name[:96],'path':path[:256]})
    return {'status':'metadata only',
            'database_declarations':databases[:128],
            'path_rules':paths[:128],
            'alias_count':len(aliases),
            'aliases_sample':aliases[:128],
            'controller_coverage':None,
            'reason':'Controller identities cannot be inferred from alias names alone.'}


def inspect(body_path, skeleton_path, dba_path=None, export_glb=None,
            chrparams_path=None):
    body, skeleton = _bytes(body_path), _bytes(skeleton_path)
    vertices, triangles, uvs, materials = read_crytek_geometry(body)
    rig = read_legacy_rig(body,skeleton,len(vertices))
    mesh = read_preview_mesh(body,Path(body_path).name)
    glb, glb_report = skin_glb_legacy(glb_bytes(mesh),body,skeleton)
    active = validate_skin_rest_pose(glb)
    report = {
        'schema': 1, 'format': 'CryTek 0x745 (2012 prototype)',
        'game_runtime_verified': False,
        'source': {'body_sha256':_sha(body),
                   'skeleton_sha256':_sha(skeleton)},
        'geometry': {'vertices':len(vertices),'triangles':len(triangles),
                     'uv_count':len(uvs),'material_subsets':len(materials)},
        'rig': {
            'status': 'decoder validated; local Blender import still required',
            'body_bones':len(rig.body_bones),
            'master_bones':len(rig.names),
            'names_and_parents':[
                {'index':i,'name':name,'parent':parent}
                for i,(name,parent) in enumerate(zip(rig.names,rig.parents))],
            'active_bones':len(rig.active_joints),
            'influence_count_per_vertex':4,
            'weight_sum_min':int(rig.weights.sum(axis=1).min()),
            'weight_sum_max':int(rig.weights.sum(axis=1).max()),
            'glb_bind_pose_active_joints_checked':active,
            'glb_bones':glb_report['bone_count'],
            'subset_palettes':_palette_report(body,rig),
            'synthetic_deformation':_synthetic_deformation(mesh,rig),
        },
        'animation': {'database_found':False,'clips_decoded':0,
                      'playback_available':False,
                      'reason':'No validated historical 0x905 clip decoder.'}
    }
    if chrparams_path is not None:
        configuration = _bytes(chrparams_path, maximum=2*1024*1024)
        report['animation']['configuration'] = _animation_config(configuration)
        report['source']['chrparams_sha256'] = _sha(configuration)
    if dba_path is not None:
        raw = _bytes(dba_path)
        report['animation'].update(probe_legacy_dba(raw))
        report['source']['dba_sha256'] = _sha(raw)
        report['animation']['database_found'] = True
    if export_glb is not None:
        output = Path(export_glb).absolute()
        if (output.suffix.casefold() != '.glb'
                or output.exists() or output.is_symlink()
                or output in (Path(body_path).resolve(),Path(skeleton_path).resolve())):
            raise ValueError('Choose a new .glb path outside the source files.')
        with output.open('xb') as dest:
            dest.write(glb)
        report['rig']['glb_file'] = str(output)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--body',type=Path,required=True)
    parser.add_argument('--skeleton',type=Path,required=True)
    parser.add_argument('--dba',type=Path)
    parser.add_argument('--chrparams',type=Path)
    parser.add_argument('--report',type=Path)
    parser.add_argument('--export-glb',type=Path)
    args=parser.parse_args(argv)
    try:
        result=inspect(args.body,args.skeleton,args.dba,args.export_glb,
                       args.chrparams)
        output=json.dumps(result,indent=2)
        if args.report:
            with args.report.open('x',encoding='utf-8') as dest:
                dest.write(output+'\n')
            print('Metadata report saved to',args.report)
        else:
            print(output)
        return 0
    except (OSError,ValueError,IndexError,struct.error) as exc:
        print('Historical rig validation failed:',exc,file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
