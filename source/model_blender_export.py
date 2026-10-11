"""Read-only Blender glTF 2.0 (.glb) export for supported CrChF v7 meshes.

Only geometry and available UVs are exported; no rig, weights, animation, or
CryEngine material decoding is inferred. Both the native model and game files
are left unchanged. This deliberately needs no Blender or external GLB library.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import struct

from model_asset import MAX_MODEL_BYTES, is_model
from model_preview import read_preview_mesh

MAX_FOLDER_FILES = 500


def _pack_vertices(mesh):
    data = bytearray()
    mins = [float('inf')] * 3
    maxs = [float('-inf')] * 3
    for vertex in mesh.vertices:
        x, y, z = vertex
        # CrChF here is Z-up; glTF uses Y-up. Blender restores Z-up on import.
        row = (float(x), float(z), -float(y))
        if any(not math.isfinite(v) for v in row):
            raise ValueError('Non-finite vertex coordinates.')
        for axis, value in enumerate(row):
            mins[axis] = min(mins[axis], value)
            maxs[axis] = max(maxs[axis], value)
        data.extend(struct.pack('<3f', *row))
    return bytes(data), mins, maxs


def glb_bytes(mesh, *, name='CryEngine Mesh'):
    """Build spec-conforming GLB with POSITION, indices and optional TEXCOORD_0."""
    vertices = len(mesh.vertices)
    triangles = len(mesh.triangles)
    if not 3 <= vertices <= 250000 or not 1 <= triangles <= 500000:
        raise ValueError('Mesh exceeds supported GLB safety bounds.')
    binary = bytearray()
    views = []
    accessors = []

    def buffer_view(payload, *, target=None):
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(payload)
        obj = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(payload)}
        if target is not None:
            obj['target'] = target
        views.append(obj)
        return len(views) - 1

    position_data, mins, maxs = _pack_vertices(mesh)
    pv = buffer_view(position_data, target=34962)
    accessors.append({'bufferView': pv, 'componentType': 5126, 'count': vertices,
                      'type': 'VEC3', 'min': mins, 'max': maxs})
    attribs = {'POSITION': 0}
    if len(mesh.uvs) == vertices:
        uv = bytearray()
        for u, v in mesh.uvs:
            if not math.isfinite(u) or not math.isfinite(v):
                raise ValueError('Non-finite texture coordinates.')
            uv.extend(struct.pack('<2f', float(u), float(v)))
        bv = buffer_view(uv, target=34962)
        accessors.append({'bufferView': bv, 'componentType': 5126,
                          'count': vertices, 'type': 'VEC2'})
        attribs['TEXCOORD_0'] = len(accessors) - 1

    all_indices = [index for face in mesh.triangles for index in face]
    if not all(0 <= idx < vertices for idx in all_indices):
        raise ValueError('Invalid mesh indices.')

    subsets = mesh.subsets or ((0, len(all_indices), 0),)
    if sum(x[1] for x in subsets) != len(all_indices) or any(
            first % 3 or count % 3 or first+count > len(all_indices)
            for first, count, _ in subsets):
        raise ValueError('Invalid render-mesh subset table.')
    primitives = []
    materials = []
    material_index = {}
    for first, count, slot in subsets:
        if not count:
            continue
        raw_indices = struct.pack('<%dI' % count, *all_indices[first:first+count])
        idx_view = buffer_view(raw_indices, target=34963)
        accessors.append({'bufferView': idx_view, 'componentType': 5125,
                          'count': count, 'type': 'SCALAR',
                          'min': [min(all_indices[first:first+count])],
                          'max': [max(all_indices[first:first+count])]})
        if slot not in material_index:
            material_index[slot] = len(materials)
            materials.append({
                'name': 'CryEngine material slot %s (texture not embedded)' % slot,
                'doubleSided': True,
                'pbrMetallicRoughness': {
                    'baseColorFactor': [0.72, 0.72, 0.75, 1.0],
                    'metallicFactor': 0.0, 'roughnessFactor': 0.9},
            })
        primitives.append({'attributes': attribs, 'indices': len(accessors)-1,
                           'material': material_index[slot], 'mode': 4})

    if not primitives:
        raise ValueError('No drawable triangles in model.')
    document = {
        'asset': {'version': '2.0', 'generator': 'Evolve Mod Manager Blender Export'},
        'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0, 'name': name}],
        'meshes': [{'name': name, 'primitives': primitives}],
        'materials': materials,
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': views, 'accessors': accessors,
        'extras': {'note': 'Render-mesh geometry and UVs only; no skeleton, animation, DDS or game re-import support.'}
    }
    payload = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    while len(payload) % 4:
        payload += b' '
    while len(binary) % 4:
        binary.append(0)
    total = 12 + 8 + len(payload) + 8 + len(binary)
    return (struct.pack('<4sII', b'glTF', 2, total)
            + struct.pack('<I4s', len(payload), b'JSON') + payload
            + struct.pack('<I4s', len(binary), b'BIN\x00') + binary)


def convert_model(source: Path, destination: Path):
    """Export a single supported model without touching its source file."""
    source, destination = Path(source).resolve(), Path(destination)
    if not source.is_file() or source.is_symlink() or not is_model(source):
        raise ValueError('Select a regular CryEngine model file (.skinm, .chrm, .cgf, etc.).')
    if source.stat().st_size > MAX_MODEL_BYTES:
        raise ValueError('Model exceeds 256 MiB safety limit.')
    if destination.suffix.lower() != '.glb':
        raise ValueError('Export filename must end with .glb.')
    if destination.resolve() == source:
        raise ValueError('Cannot overwrite the source CryEngine model.')
    mesh = read_preview_mesh(source.read_bytes(), source.name)
    data = glb_bytes(mesh, name=source.stem)
    if destination.parent.is_dir():
        with destination.open('wb') as output:
            output.write(data)
    else:
        raise FileNotFoundError('Export destination folder does not exist.')
    return (len(mesh.vertices), len(mesh.triangles))


def convert_folder(folder: Path, destination: Path):
    """Export supported render meshes from an existing folder tree.

    Unsupported types (.skin skeletons, variant stream encodings) are reported
    and skipped, never exported as misleading empty models. Source unchanged.
    """
    folder, destination = Path(folder).resolve(), Path(destination).resolve()
    if not folder.is_dir() or not destination.is_dir():
        raise FileNotFoundError('Choose existing input and output folders.')
    if folder == destination or destination.is_relative_to(folder):
        raise ValueError('Output folder must be outside the original model folder.')
    candidates = sorted(
        (p for p in folder.rglob('*') if p.is_file() and not p.is_symlink()
         and p.suffix.lower() in ('.skinm', '.chrm', '.cgfm', '.cgam', '.cgf', '.cga')),
        key=lambda p: str(p).casefold())
    if len(candidates) > MAX_FOLDER_FILES:
        raise ValueError('Folder contains more than 500 model files.')
    results = []
    skipped = []
    for source in candidates:
        relative = source.relative_to(folder)
        out = destination / relative.with_suffix('.glb')
        if source.stat().st_size > MAX_MODEL_BYTES:
            skipped.append((str(relative), 'Model exceeds 256 MiB'))
            continue
        try:
            # A .chrm and .skinm with the same name are not equivalent. If both
            # exist, keep .skinm's rendered mesh as the preferred export.
            if out.exists() and relative.suffix.lower() == '.chrm' and \
                    relative.with_suffix('.skinm').exists():
                continue
            if relative.suffix.lower() == '.chrm' and \
                    relative.with_suffix('.skinm').is_file():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            count = convert_model(source, out)
            results.append((str(relative), str(out.relative_to(destination)), count))
        except (OSError, ValueError) as error:
            skipped.append((str(relative), str(error)))
    return results, skipped
