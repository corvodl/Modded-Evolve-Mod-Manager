"""CryEngine CrChF v7 skeletal GLB export with verified native bind poses.

Supports 584-byte compiled bone records and material-subset-local bone
palettes used by Evolve Stage 2 .skinm meshes. Never invents a skeleton when
required source data is missing. Read-only; cannot import edited GLBs to Evolve.
"""
from __future__ import annotations

import math
from pathlib import Path
import struct

import numpy as np

COMPILED_BONES = 0x08002000
DATA_STREAM = 0x08001016
MESH_SUBSETS = 0x08001017
BONE_RECORD = 584
BASIS = np.array([[1, 0, 0, 0], [0, 0, 1, 0],
                  [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
INVERSE_BASIS = BASIS.T


def _chunks(raw):
    if len(raw) < 32 or raw[:8] != b'CrChF\x07\x00\x00':
        raise ValueError('Rigging requires a supported CrChF version 7 file.')
    count, start = struct.unpack_from('<II', raw, 8)
    if not (1 <= count <= 10000) or start < 16 or start + count*16 > len(raw):
        raise ValueError('Invalid CryEngine rigging chunk table.')
    out = {}
    ranges = []
    for i in range(count):
        kind, cid, size, offset = struct.unpack_from('<4I', raw, start+i*16)
        if not size or offset < start+count*16 or offset+size > len(raw):
            raise ValueError('Invalid or truncated rigging chunk.')
        if (kind, cid) in out:
            raise ValueError('Duplicate rigging chunk identifier.')
        ranges.append((offset, offset+size))
        out[(kind, cid)] = memoryview(raw)[offset:offset+size]
    ranges.sort()
    if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
        raise ValueError('Overlapping rigging chunks.')
    return out


def parse_compiled_bones(raw):
    """Return names, parent indices, world-rest and inverse-bind matrices."""
    sources = [data for (kind, _), data in _chunks(raw).items()
               if kind == COMPILED_BONES]
    if len(sources) != 1 or len(sources[0]) < 32 or (
            len(sources[0]) - 32) % BONE_RECORD:
        raise ValueError('Expected a single compatible 584-byte compiled bone table.')
    data = sources[0]
    count = (len(data) - 32) // BONE_RECORD
    if not 1 <= count <= 1024:
        raise ValueError('Unsupported bone count.')
    names, parents, world, inverse = [], [], [], []
    for i in range(count):
        offset = 32 + i * BONE_RECORD
        name = bytes(data[offset+312:offset+568]).split(b'\x00', 1)[0].decode(
            'utf-8', errors='replace')
        if not name or name in names:
            raise ValueError('Empty or duplicate bone name.')
        a = np.array(struct.unpack_from('<12f', data, offset+216),
                     dtype=np.float64).reshape(3, 4)
        b = np.array(struct.unpack_from('<12f', data, offset+264),
                     dtype=np.float64).reshape(3, 4)
        inv = np.vstack((a, [0, 0, 0, 1]))
        rest = np.vstack((b, [0, 0, 0, 1]))
        if (not np.isfinite(inv).all() or not np.isfinite(rest).all() or
                np.max(np.abs(inv @ rest - np.eye(4))) > 0.001):
            raise ValueError('Inconsistent CryEngine inverse bind pose: ' + name)
        _limb, parent_offset, _children, _child_offset = struct.unpack_from(
            '<4i', data, offset+568)
        parent = i+parent_offset if parent_offset < 0 else -1
        if parent >= i or parent < -1:
            raise ValueError('Invalid bone hierarchy at ' + name)
        names.append(name)
        parents.append(parent)
        world.append(rest)
        inverse.append(inv)
    if parents.count(-1) != 1:
        raise ValueError('Expected one verified root bone.')
    return names, parents, world, inverse


def remap_skin_weights(mesh_raw, vertex_count, bone_count):
    """Resolve each vertex's 4 local influences through every mesh-subset palette."""
    sources = _chunks(mesh_raw)
    streams = {}
    for (kind, _), data in sources.items():
        if kind != DATA_STREAM or len(data) < 24:
            continue
        _, stream, count, stride, _, _ = struct.unpack_from('<6I', data)
        if stream in streams:
            raise ValueError('Duplicate bone/index data stream.')
        streams[stream] = (data, count, stride)
    if 9 not in streams or 5 not in streams:
        raise ValueError('Mesh has no compatible bone map and index stream.')
    bone_raw, count, stride = streams[9]
    if (stride != 8 or count != vertex_count or
            len(bone_raw) != 24 + count * stride):
        raise ValueError('Vertex count differs from the bone mapping stream.')
    packed = np.frombuffer(bone_raw, dtype=np.uint8, offset=24).reshape(
        count, 8)
    if np.any(packed[:, 4:].astype(np.uint16).sum(axis=1) != 255):
        raise ValueError('Skin weights do not sum to 255.')
    index_raw, index_count, index_stride = streams[5]
    if (index_stride not in (2, 4) or len(index_raw) != 24 +
            index_count * index_stride):
        raise ValueError('Unsupported triangle index stream.')
    indices = np.frombuffer(
        index_raw, dtype='<u2' if index_stride == 2 else '<u4', offset=24)
    tables = [data for (kind, _), data in sources.items()
              if kind == MESH_SUBSETS]
    if len(tables) != 1 or len(tables[0]) < 16:
        raise ValueError('Missing compatible mesh subset palette table.')
    table = tables[0]
    _material_groups, subsets = struct.unpack_from('<II', table)
    if not 1 <= subsets <= 128 or len(table) < 16 + subsets*(36+260):
        raise ValueError('Unsupported mesh subset or bone palette layout.')
    mapped = np.zeros((vertex_count, 4), dtype=np.uint16)
    covered = np.zeros(vertex_count, dtype=bool)
    for i in range(subsets):
        first, number, first_vertex, vertices, _material = struct.unpack_from(
            '<5I', table, 16+i*36)
        if (not number or first+number > index_count or
                first_vertex+vertices > vertex_count):
            raise ValueError('Invalid subset index or vertex range.')
        palette_at = 16 + subsets*36 + i*260
        palette_count = struct.unpack_from('<I', table, palette_at)[0]
        if not 1 <= palette_count <= 128:
            raise ValueError('Invalid bone palette length.')
        palette = np.frombuffer(table, dtype='<u2', count=128,
                                offset=palette_at+4)
        if np.any(palette[:palette_count] >= bone_count):
            raise ValueError('Bone palette points beyond the skeleton.')
        used_vertices = np.unique(indices[first:first+number])
        if (not len(used_vertices) or
                used_vertices.min() < first_vertex or
                used_vertices.max() >= first_vertex + vertices):
            raise ValueError('Subset indices disagree with declared vertex range.')
        bones = packed[used_vertices, :4]
        active = packed[used_vertices, 4:] > 0
        if np.any(bones[active] >= palette_count):
            raise ValueError('Weighted vertex exceeds its local bone palette.')
        converted = np.where(active, palette[bones], 0).astype(np.uint16)
        shared = covered[used_vertices]
        if shared.any() and not np.array_equal(
                mapped[used_vertices[shared]], converted[shared]):
            raise ValueError('Conflicting palettes for shared mesh vertices.')
        mapped[used_vertices] = converted
        covered[used_vertices] = True
    if not covered.all():
        raise ValueError('Mesh contains vertices without any validated palette.')
    return mapped, packed[:, 4:].copy(), subsets


def skin_glb(glb, mesh_raw, skeleton_raw):
    """Add real glTF joints, weights, parent hierarchy and inverse bind matrices.

    Expects the manager's existing Blender GLB: one mesh and common POSITION
    accessor for all material primitives. Optional diffuse images remain intact.
    """
    from model_textured_export import _unpack_glb, _pack_glb
    document, binary = _unpack_glb(glb)
    if document.get('skins') or len(document.get('meshes', [])) != 1:
        raise ValueError('Expected one previously unskinned manager GLB mesh.')
    primitives = document['meshes'][0].get('primitives', [])
    if not primitives:
        raise ValueError('No GLB primitives to rig.')
    positions = {p['attributes'].get('POSITION') for p in primitives}
    if len(positions) != 1 or None in positions:
        raise ValueError('All material groups must share the original position stream.')
    vertex_count = document['accessors'][positions.pop()]['count']
    names, parents, world, inverse = parse_compiled_bones(skeleton_raw)
    bone_ids, weights, subsets = remap_skin_weights(
        mesh_raw, vertex_count, len(names))
    mesh_nodes = [i for i, node in enumerate(document.get('nodes', []))
                  if node.get('mesh') == 0]
    if len(mesh_nodes) != 1:
        raise ValueError('Expected exactly one GLB node for the skinned mesh.')
    first = len(document['nodes'])
    nodes = [{'name': name, 'children': []} for name in names]
    for i, parent in enumerate(parents):
        local = world[i] if parent == -1 else np.linalg.inv(world[parent]) @ world[i]
        local = BASIS @ local @ INVERSE_BASIS
        nodes[i]['matrix'] = [float(x) for x in local.reshape(-1, order='F')]
        if parent != -1:
            nodes[parent]['children'].append(first+i)
    for node in nodes:
        if not node['children']:
            del node['children']
    document['nodes'].extend(nodes)
    root = parents.index(-1)
    document['scenes'][document.get('scene', 0)]['nodes'].append(first+root)

    def add_view(data, target=None):
        binary.extend(b'\x00' * (-len(binary) % 4))
        position = len(binary)
        binary.extend(data)
        view = {'buffer': 0, 'byteOffset': position, 'byteLength': len(data)}
        if target is not None:
            view['target'] = target
        document['bufferViews'].append(view)
        return len(document['bufferViews']) - 1

    joints_view = add_view(bone_ids.astype('<u2').tobytes(), 34962)
    weights_view = add_view(weights.tobytes(), 34962)
    inverse_matrices = [BASIS @ matrix @ INVERSE_BASIS for matrix in inverse]
    inverse_data = np.array(
        [matrix.reshape(-1, order='F') for matrix in inverse_matrices],
        dtype='<f4').tobytes()
    inverse_view = add_view(inverse_data)
    joints_acc = len(document['accessors'])
    document['accessors'].append({
        'bufferView': joints_view, 'componentType': 5123,
        'count': vertex_count, 'type': 'VEC4'})
    weight_acc = len(document['accessors'])
    document['accessors'].append({
        'bufferView': weights_view, 'componentType': 5121,
        'count': vertex_count, 'type': 'VEC4', 'normalized': True})
    inverse_acc = len(document['accessors'])
    document['accessors'].append({
        'bufferView': inverse_view, 'componentType': 5126,
        'count': len(names), 'type': 'MAT4'})
    for primitive in primitives:
        primitive['attributes']['JOINTS_0'] = joints_acc
        primitive['attributes']['WEIGHTS_0'] = weight_acc
    document['skins'] = [{
        'name': 'CryEngine ' + names[root],
        'joints': [first+i for i in range(len(names))],
        'inverseBindMatrices': inverse_acc,
        'skeleton': first+root,
    }]
    document['nodes'][mesh_nodes[0]]['skin'] = 0
    document.setdefault('extras', {})['rigging'] = (
        'Original CryEngine bone transforms and subset bone palettes. '
        'No CAF/DBA animation keyframes included.')
    payload = _pack_glb(document, binary)
    return payload, {
        'bone_count': len(names), 'vertex_count': vertex_count,
        'subset_count': subsets,
        'active_bones': len(np.unique(bone_ids[weights > 0])),
    }


def skin_companion_path(source):
    """Use the corresponding native .skin file; never guess alternate skeletons."""
    path = Path(source)
    if path.suffix.casefold() == '.skinm':
        return path.with_suffix('.skin')
    if path.suffix.casefold() == '.chrm':
        return path.with_suffix('.chr')
    return None
