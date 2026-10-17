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
# Material isolation is deliberately *not* automatic UV unwrapping.
# Large original texture atlases can look like densely overlapping UVs when
# every part is selected, but their islands must retain source coordinates
# to line up with CryEngine diffuse/normal masks. Separate mesh objects
# by existing material IDs, preserving their original UV loops and vertex
# groups so each material is independently selectable for painting.
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
        os.replace(output, target)
    return {'format':'FBX Binary', 'profile':'static' if static else 'character',
            'mesh_count':verified['meshes'], 'armatures':verified['armatures'],
            'actions':verified['actions'],
            'material_groups':verified.get('material_groups', []),
            'material_uv_verification':uv_report}
