"""Verified binary FBX conversion using Blender 4.2+ as an external exporter.

Internal GLB is ephemeral: exported models are FBX. Skinned FBX retains bones,
deform weights and imported Actions; static FBX has only cleaned model geometry.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from model_export_readiness import audit_material_uvs
from model_textured_export import _unpack_glb

FBX_SIGNATURE = b'Kaydara FBX Binary  \x00\x1a\x00'

BLENDER_FBX_SCRIPT = r'''
import bpy
import json
import math
import sys
from pathlib import Path

source, output, receipt, profile = sys.argv[sys.argv.index('--')+1:]
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=source)
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
armatures = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']
if not meshes or any(not m.data.polygons for m in meshes):
    raise RuntimeError('Cannot export an empty or non-drawable mesh to FBX')
if profile not in ('static', 'character'):
    raise RuntimeError('Unknown FBX export profile')
keep_rig = profile == 'character' and bool(armatures)
bpy.context.scene.frame_set(0)
bpy.context.view_layer.update()
for obj in meshes:
    mesh = obj.data
    if any(poly.material_index >= len(mesh.materials) for poly in mesh.polygons):
        raise RuntimeError('Invalid FBX material assignment: ' + obj.name)
    has_texture = any(mat and mat.use_nodes and
                      any(node.type == 'TEX_IMAGE' for node in mat.node_tree.nodes)
                      for mat in mesh.materials)
    if has_texture and not mesh.uv_layers.active:
        raise RuntimeError('Material lacks a complete UV set: ' + obj.name)
    if mesh.uv_layers.active and any(
            not math.isfinite(k) for loop in mesh.uv_layers.active.data for k in loop.uv):
        raise RuntimeError('Invalid nonfinite UV coordinate: ' + obj.name)
    if not keep_rig:
        world = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = world
        for modifier in tuple(obj.modifiers):
            if modifier.type == 'ARMATURE':
                obj.modifiers.remove(modifier)
        obj.animation_data_clear()
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
# Never unwrap the ORIGINAL texture UV0. Dense, overlapping CryEngine
# atlases and intentional mirrored islands must retain their source coordinates
# for the existing DDS maps. Material separation retains vertex groups; a
# second UV1 is unwrapped for painting, without changing UV0 or rig weights.
for obj in tuple(meshes):
    ids = {polygon.material_index for polygon in obj.data.polygons}
    if len(ids) <= 1:
        continue
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='MATERIAL')
    bpy.ops.object.mode_set(mode='OBJECT')
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
if not meshes or any(not obj.data.polygons for obj in meshes):
    raise RuntimeError('Splitting FBX material groups produced an empty mesh.')
# UV_TOPOLOGY_PLANNER_START
from collections import Counter, defaultdict

def plan_split_vertex_reconnections(positions, triangles, *, weights=(), normals=(), materials=()):
    """Reconnect only renderer-split geometric edges with matching rig data.

    The plan changes vertex connectivity, never per-corner UV values.
    UV seams may remain UV seams after the coincident mesh vertices reconnect.
    """
    count = len(positions)
    if not 3 <= count <= 250000 or len(triangles) > 500000:
        raise ValueError('Original UV topology exceeds safe model limits.')
    if weights and len(weights) != count:
        raise ValueError('Skin weights disagree with vertex count.')
    if normals and len(normals) != count:
        raise ValueError('Normals disagree with vertex count.')
    if materials and len(materials) != len(triangles):
        raise ValueError('Material groups disagree with triangle count.')
    position_keys = []
    for point in positions:
        if len(point) != 3 or not all(math.isfinite(float(v)) for v in point):
            raise ValueError('Original mesh contains invalid coordinates.')
        position_keys.append(tuple(round(float(v), 7) for v in point))
    weight_keys = [tuple((int(index), round(float(value), 7))
                         for index, value in row) for row in weights] if weights else None
    normal_keys = [tuple(round(float(v), 5) for v in row)
                   for row in normals] if normals else None
    edges = defaultdict(list)
    uses = [set() for _ in range(count)]
    for face_id, face in enumerate(triangles):
        if (len(face) != 3 or len(set(face)) != 3
                or any(not isinstance(i, int) or i < 0 or i >= count for i in face)):
            raise ValueError('Invalid original triangle index.')
        for i in face:
            uses[i].add(face_id)
        for a, c in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            if position_keys[a] != position_keys[c]:
                edges[tuple(sorted((position_keys[a], position_keys[c])))].append(
                    (face_id, a, c))
    candidate = []
    renderer_splits = 0
    nonmanifold = 0
    for edge, pair in edges.items():
        if len(pair) > 2:
            nonmanifold += 1
            continue
        if len(pair) != 2:
            continue
        (fa, a, c), (fb, b, d) = pair
        if fa == fb or (materials and materials[fa] != materials[fb]):
            continue
        left = {position_keys[a]: a, position_keys[c]: c}
        right = {position_keys[b]: b, position_keys[d]: d}
        if set(left.values()) == set(right.values()):
            continue
        renderer_splits += 1
        for point in edge:
            x, y = left[point], right[point]
            if x == y or uses[x] & uses[y]:
                continue
            if weight_keys is not None and weight_keys[x] != weight_keys[y]:
                continue
            if normal_keys is not None and normal_keys[x] != normal_keys[y]:
                continue
            candidate.append((min(x, y), max(x, y)))
    parent = list(range(count))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for x, y in sorted(set(candidate)):
        a, c = root(x), root(y)
        if a == c or uses[a] & uses[c]:
            continue
        low, high = sorted((a, c))
        parent[high] = low
        uses[low].update(uses[high])
    mapping = {i: root(i) for i in range(count) if root(i) != i}
    return mapping, {
        'render_vertex_split_edges': renderer_splits,
        'nonmanifold_edges_skipped': nonmanifold,
        'safe_vertex_pairs': len(mapping),
    }
# UV_TOPOLOGY_PLANNER_END

def _corner_signature(mesh):
    """Snapshot face-corner UV, position, skin influences and normals."""
    layers = list(mesh.uv_layers)
    def fingerprint(i, loop_id):
        vertex = mesh.vertices[i]
        weight = tuple(sorted((g.group, round(g.weight, 6)) for g in vertex.groups))
        uvs = tuple(tuple(round(float(v), 6) for v in layer.data[loop_id].uv)
                    for layer in layers)
        return (tuple(round(float(v), 6) for v in vertex.co), uvs,
                weight, tuple(round(float(v), 5) for v in vertex.normal))
    return Counter((poly.material_index, tuple(sorted(
        fingerprint(mesh.loops[li].vertex_index, li)
        for li in range(poly.loop_start, poly.loop_start + poly.loop_total))))
        for poly in mesh.polygons)

def _rebuild_original_uv_topology(obj):
    mesh = obj.data
    source_uvs = len(mesh.uv_layers)
    if source_uvs:
        uv0 = mesh.uv_layers[0]
        prior = uv0.name
        collision = mesh.uv_layers.get('UV0_Original')
        if collision is not None and collision != uv0:
            collision.name = 'SourceUV_Additional'
        uv0.name = 'UV0_Original'
        uv0.active_render = True
        mesh.uv_layers.active = uv0
        for material in mesh.materials:
            if material and material.use_nodes:
                for node in material.node_tree.nodes:
                    if node.type == 'UVMAP' and node.uv_map == prior:
                        node.uv_map = 'UV0_Original'
    if not mesh.uv_layers and any(
            mat and mat.use_nodes and any(n.type == 'TEX_IMAGE'
                                          for n in mat.node_tree.nodes)
            for mat in mesh.materials):
        raise RuntimeError('Textured mesh lacks original UV coordinates.')
    if any(not math.isfinite(v)
           for layer in mesh.uv_layers
           for loop in layer.data for v in loop.uv):
        raise RuntimeError('Original UV stream contains nonfinite values.')
    summary = {'object': obj.name, 'uv_sets': [x.name for x in mesh.uv_layers],
               'source_uv_sets': source_uvs, 'generated_uv_sets': 0,
               'source_uvs_preserved': True, 'weights_preserved': True,
               'reconnected_vertices': 0, 'reconnect_status': 'none needed'}
    if not source_uvs or mesh.shape_keys or any(len(p.vertices) != 3 for p in mesh.polygons):
        summary['reconnect_status'] = 'unsupported shape keys, nontriangles, or missing UV'
        return summary
    # Never merge across independently exported material objects or nonmanifold
    # edges. Extra vertex attributes may depend on their original vertex IDs.
    if any(a.domain == 'POINT' and a.name != 'position' for a in mesh.attributes):
        summary['reconnect_status'] = 'custom point attributes preserved without welding'
        return summary
    positions = [tuple(v.co) for v in mesh.vertices]
    triangles = [tuple(int(i) for i in p.vertices) for p in mesh.polygons]
    weights = [tuple(sorted((g.group, g.weight) for g in v.groups))
               for v in mesh.vertices]
    normals = [tuple(v.normal) for v in mesh.vertices]
    mapping, diagnostic = plan_split_vertex_reconnections(
        positions, triangles, weights=weights, normals=normals,
        materials=[p.material_index for p in mesh.polygons])
    summary.update(diagnostic)
    if not mapping:
        summary['reconnect_status'] = 'no safely reconnectable renderer seams'
        return summary
    original_fingerprint = _corner_signature(mesh)
    saved = mesh.copy()
    try:
        import bmesh
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()
            bmesh.ops.weld_verts(bm, targetmap={
                bm.verts[source]: bm.verts[target]
                for source, target in mapping.items()})
            bm.to_mesh(mesh)
        finally:
            bm.free()
        mesh.update()
        if (len(mesh.uv_layers) != source_uvs
                or len(mesh.polygons) != len(triangles)
                or _corner_signature(mesh) != original_fingerprint):
            raise ValueError('UV loop, rig influences or surface normals changed')
        summary['reconnected_vertices'] = len(mapping)
        summary['reconnect_status'] = 'validated per-corner original UV and skin weights'
        bpy.data.meshes.remove(saved)
    except (ValueError, RuntimeError, TypeError) as exc:
        # Conserve the original imported mesh when Blender cannot guarantee
        # exact UV, weight, normal and triangle preservation.
        damaged = obj.data
        obj.data = saved
        bpy.data.meshes.remove(damaged)
        summary['reconnect_status'] = 'safe fallback: ' + str(exc)[:160]
    return summary

uv_maps = []
material_groups = []
for obj in meshes:
    used = {polygon.material_index for polygon in obj.data.polygons}
    if len(used) != 1:
        raise RuntimeError('FBX mesh still mixes several texture/material IDs.')
    slot = next(iter(used))
    if slot >= len(obj.data.materials):
        raise RuntimeError('FBX material ID does not have a corresponding material.')
    material = obj.data.materials[slot]
    if material:
        # Give the artist a readable, material-specific object in the Outliner.
        obj.name = (obj.name[:48] + '__' + material.name[:42])[:100]
    if obj.data.uv_layers.active:
        uv = obj.data.uv_layers.active
        if len(uv.data) != len(obj.data.loops):
            raise RuntimeError('Material-specific UV set is incomplete.')
    uv_maps.append(_rebuild_original_uv_topology(obj))
    material_groups.append({
        'object':obj.name,
        'material':material.name if material else 'No material',
        'uv_loops':len(obj.data.uv_layers.active.data)
                    if obj.data.uv_layers.active else 0,
        'triangles':len(obj.data.polygons)})
# Never weld or bake individual mesh transforms on rigged models: source weight
# indices and bind matrices would no longer describe their vertices.
for obj in tuple(bpy.context.scene.objects):
    if obj.type != 'MESH' and not (keep_rig and obj.type == 'ARMATURE'):
        bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.object.select_all(action='DESELECT')
for obj in meshes + (armatures if keep_rig else []):
    obj.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
actions = [action for action in bpy.data.actions if action.users > 0] if keep_rig else []
bpy.ops.export_scene.fbx(
    filepath=output, use_selection=True,
    object_types={'MESH', 'ARMATURE'} if keep_rig else {'MESH'},
    apply_unit_scale=True, apply_scale_options='FBX_SCALE_NONE',
    bake_space_transform=False, use_mesh_modifiers=True,
    mesh_smooth_type='FACE', use_tspace=False, add_leaf_bones=False,
    bake_anim=bool(actions), bake_anim_use_all_bones=True,
    bake_anim_use_nla_strips=False, bake_anim_use_all_actions=True,
    path_mode='COPY', embed_textures=True, use_custom_props=True)
if not Path(output).is_file() or Path(output).stat().st_size < 100:
    raise RuntimeError('Blender failed to create binary FBX')
Path(receipt).write_text(json.dumps({
    'meshes':len(meshes), 'armatures':len(armatures) if keep_rig else 0,
    'actions':sorted(a.name for a in actions),
    'materials':sum(len(obj.data.materials) for obj in meshes),
    'uv_meshes':sum(bool(obj.data.uv_layers) for obj in meshes),
    'material_groups':material_groups,
    'uv_maps':uv_maps,
}), encoding='utf-8')
'''


def convert_glb_to_fbx(blob, destination, *, static=False, blender=None, timeout=1800):
    """Write only verified FBX, never a renamed GLB or a partial output."""
    target = Path(destination).resolve()
    if target.suffix.casefold() != '.fbx' or not target.parent.is_dir():
        raise ValueError('FBX output requires an existing folder and an .fbx filename.')
    if target.exists() or target.is_symlink():
        raise FileExistsError('FBX output already exists; refusing overwrite.')
    if not isinstance(blob, bytes) or not blob.startswith(b'glTF'):
        raise ValueError('Expected verified internal GLB data.')
    gltf, _ = _unpack_glb(blob)
    uv_report = audit_material_uvs(blob)
    tracks = tuple(str(t.get('name','')) for t in gltf.get('animations', ()))
    if tracks and not gltf.get('skins', ()):
        raise ValueError('Animated FBX requires a verified skinned armature.')
    if static and tracks:
        raise ValueError('Use character FBX export for models containing animations.')
    executable = str(blender or shutil.which('blender') or '')
    if not executable or not Path(executable).is_file():
        raise FileNotFoundError('Blender 4.2+ must be installed and blender.exe must '
                                'be available on PATH for genuine FBX export.')
    if not 30 <= timeout <= 7200:
        raise ValueError('FBX export timeout must be 30–7200 seconds.')
    with tempfile.TemporaryDirectory(prefix='evolve-fbx-') as directory:
        root = Path(directory)
        source = root/'model.glb'
        source.write_bytes(blob)
        script = root/'convert.py'
        script.write_text(BLENDER_FBX_SCRIPT, encoding='utf-8')
        output = root/'converted.fbx'
        receipt = root/'validation.json'
        process = subprocess.run(
            [executable, '-b', '--factory-startup', '--python', str(script),
             '--', str(source), str(output), str(receipt),
             'static' if static else 'character'],
            check=False, capture_output=True, text=True, errors='replace', timeout=timeout)
        if process.returncode:
            raise RuntimeError('Blender FBX conversion failed: ' +
                               (process.stderr or process.stdout)[-1600:])
        if not output.is_file() or not output.open('rb').read(23).startswith(FBX_SIGNATURE):
            raise ValueError('Blender did not produce a valid binary FBX.')
        if not receipt.is_file():
            raise ValueError('Missing FBX conversion verification receipt.')
        verified = json.loads(receipt.read_text('utf-8'))
        if not verified.get('meshes'):
            raise ValueError('Converted FBX has no meshes.')
        if not static and gltf.get('skins') and not verified.get('armatures'):
            raise ValueError('FBX conversion dropped the source character skeleton.')
        if tracks and (not verified.get('armatures')
                       or len(verified.get('actions', ())) < len(tracks)):
            raise ValueError('FBX conversion dropped verified skin or animations.')
        if static and verified.get('armatures'):
            raise ValueError('Static FBX must not include an armature.')
        uv_maps = verified.get('uv_maps')
        if (not isinstance(uv_maps, list) or
                len(uv_maps) != verified['meshes'] or
                any(not isinstance(row, dict)
                    or row.get('generated_uv_sets') != 0
                    or not row.get('source_uvs_preserved')
                    or not row.get('weights_preserved')
                    or (row.get('source_uv_sets') and
                        'UV0_Original' not in row.get('uv_sets', ()))
                    for row in uv_maps)):
            raise ValueError('FBX original UV or skinned topology validation failed.')
        os.replace(output, target)
    return {'format':'FBX Binary', 'profile':'static' if static else 'character',
            'mesh_count':verified['meshes'], 'armatures':verified['armatures'],
            'actions':verified['actions'],
            'material_groups':verified.get('material_groups', []),
            'uv_maps':verified['uv_maps'],
            'material_uv_verification':uv_report}
