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
    if (stride != 8 or count not in (vertex_count, vertex_count * 2) or
            len(bone_raw) != 24 + count * stride):
        raise ValueError('Vertex count differs from the bone mapping stream.')
    packed = np.frombuffer(bone_raw, dtype=np.uint8, offset=24).reshape(
        count, 8)
    # Emet stores a second block of four influences after the first block.
    blocks = packed.reshape(-1, vertex_count, 8)
    local_ids = np.concatenate([block[:, :4] for block in blocks], axis=1)
    local_weights = np.concatenate([block[:, 4:] for block in blocks], axis=1)
    if np.any(local_weights.astype(np.uint16).sum(axis=1) != 255):
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
    mapped = np.zeros(local_ids.shape, dtype=np.uint16)
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
        bones = local_ids[used_vertices]
        active = local_weights[used_vertices] > 0
        if np.any(bones[active] >= palette_count):
            raise ValueError('Weighted vertex exceeds its local bone palette.')
        converted = np.where(active, palette[np.where(active, bones, 0)], 0).astype(np.uint16)
        shared = covered[used_vertices]
        if shared.any() and not np.array_equal(
                mapped[used_vertices[shared]], converted[shared]):
            raise ValueError('Conflicting palettes for shared mesh vertices.')
        mapped[used_vertices] = converted
        covered[used_vertices] = True
    if not covered.all():
        raise ValueError('Mesh contains vertices without any validated palette.')
    return mapped, local_weights.copy(), subsets


def skin_glb(glb, mesh_raw, skeleton_raw):
    """Rig a modern CrChF v7 mesh, preserving its original validation."""
    from model_textured_export import _unpack_glb
    document, _ = _unpack_glb(glb)
    primitives = document['meshes'][0].get('primitives', []) if len(document.get('meshes', [])) == 1 else []
    if not primitives or len({p['attributes'].get('POSITION') for p in primitives}) != 1:
        raise ValueError('Expected one mesh and one shared POSITION stream.')
    pos = primitives[0]['attributes']['POSITION']
    vertex_count = document['accessors'][pos]['count']
    names, parents, world, inverse = parse_compiled_bones(skeleton_raw)
    ids, weights, subsets = remap_skin_weights(mesh_raw, vertex_count, len(names))
    return skin_glb_verified(glb, names, parents, world, inverse, ids, weights, subsets)


def skin_glb_verified(glb, names, parents, world, inverse, bone_ids, weights, subsets,
                      *, allow_multiple_roots=False):
    """Write shared glTF skin accessors from a separately validated rig.

    The 2012 parser and modern parser feed this same construction path,
    and both must pass the exported GLB rest-pose verification. Never call
    this with guessed IDs, unnormalized influences or unverified bind data.
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
        raise ValueError('All material groups must share one position stream.')
    vertex_count = document['accessors'][positions.pop()]['count']
    bone_ids = np.asarray(bone_ids, dtype=np.uint16)
    weights = np.asarray(weights, dtype=np.uint8)
    if (bone_ids.shape != weights.shape or bone_ids.shape[0] != vertex_count
            or bone_ids.shape[1] not in (4, 8)
            or len(names) != len(parents) or len(names) != len(world)
            or len(names) != len(inverse) or not 1 <= len(names) <= 1024):
        raise ValueError('Verified skin data does not match GLB mesh or skeleton.')
    if (np.any(weights.astype(np.uint16).sum(axis=1) != 255)
            or np.any(bone_ids[weights > 0] >= len(names))
            or parents.count(-1) < 1
            or (parents.count(-1) != 1 and not allow_multiple_roots)
            or any(parent >= i or parent < -1
                   for i, parent in enumerate(parents))):
        raise ValueError('Invalid verified bone weights or root hierarchy.')
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
    roots = [i for i, parent in enumerate(parents) if parent == -1]
    document['scenes'][document.get('scene', 0)]['nodes'].extend(
        first+i for i in roots)

    def add_view(data, target=None):
        binary.extend(b'\x00' * (-len(binary) % 4))
        position = len(binary)
        binary.extend(data)
        view = {'buffer': 0, 'byteOffset': position, 'byteLength': len(data)}
        if target is not None:
            view['target'] = target
        document['bufferViews'].append(view)
        return len(document['bufferViews']) - 1

    joints_view = add_view(bone_ids[:, :4].astype('<u2').tobytes(), 34962)
    weights_view = add_view(weights[:, :4].tobytes(), 34962)
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
    _extra_influences(document, add_view, primitives, bone_ids, weights)
    skin = {
        'name': 'CryEngine ' + names[roots[0]],
        'joints': [first+i for i in range(len(names))],
        'inverseBindMatrices': inverse_acc,
    }
    # glTF's optional skeleton field must identify one common skeleton root.
    # Early accessory files can contain two independent bone roots, so omit
    # that field instead of selecting a misleading single root. Both trees
    # remain in the scene; no game bone is invented or dropped.
    if len(roots) == 1:
        skin['skeleton'] = first+roots[0]
    document['skins'] = [skin]
    document['nodes'][mesh_nodes[0]]['skin'] = 0
    document.setdefault('extras', {})['rigging'] = (
        'Original CryEngine bone transforms and subset bone palettes. '
        'No CAF/DBA animation keyframes included.')
    payload = _pack_glb(document, binary)
    validate_skin_rest_pose(payload)
    return payload, {
        'bone_count': len(names), 'vertex_count': vertex_count,
        'subset_count': subsets,
        'active_bones': len(np.unique(bone_ids[weights > 0])),
    }



def validate_skin_rest_pose(glb, tolerance=0.02):
    """Require every actively weighted glTF bone to preserve the rest mesh.

    For each weighted joint, inverse(meshWorld) @ jointWorld @ inverseBind
    must be the identity at rest. A partial name match or mismatched monster
    stage may pass basic bone-name validation while producing twisted meshes.
    Fail before loading its animation instead of presenting bad geometry as a
    successful motion.
    """
    from model_textured_export import _unpack_glb
    from model_animation import _accessor, _local_matrix
    doc, binary = _unpack_glb(glb)
    instances = [(i, node) for i, node in enumerate(doc.get('nodes', ()))
                 if 'mesh' in node and 'skin' in node]
    if not instances:
        raise ValueError('Skin rest validation needs a skinned mesh instance.')
    parents = {}
    for i,node in enumerate(doc['nodes']):
        for child in node.get('children',()):
            if child in parents:
                raise ValueError('Ambiguous joint parentage in rig.')
            parents[child] = i
    world = {}
    visiting = set()
    def matrix(i):
        if i in world:
            return world[i]
        if i in visiting:
            raise ValueError('Cyclic rig hierarchy.')
        visiting.add(i)
        value = _local_matrix(doc['nodes'][i])
        if i in parents:
            value = matrix(parents[i]) @ value
        visiting.remove(i)
        world[i] = value
        return value

    weighted_total = 0
    for mesh_node_id, instance in instances:
        skin = doc['skins'][instance['skin']]
        joints = [int(i) for i in skin['joints']]
        matrices = _accessor(doc, binary, skin['inverseBindMatrices'])
        if len(matrices) != len(joints):
            raise ValueError('Bone inverse-bind count does not match the skin.')
        inverse = matrices.reshape(-1,4,4).transpose(0,2,1)
        active = set()
        primitives = doc['meshes'][instance['mesh']]['primitives']
        for primitive in primitives:
            attrs = primitive['attributes']
            for suffix in ('0','1'):
                key, weight_key = 'JOINTS_'+suffix, 'WEIGHTS_'+suffix
                if key not in attrs or weight_key not in attrs:
                    continue
                ids = _accessor(doc,binary,attrs[key]).astype(np.int32)
                weights = _accessor(doc,binary,attrs[weight_key]).astype(np.float64)
                if ids.shape != weights.shape:
                    raise ValueError('Skin joint and weight accessor shapes differ.')
                active.update(int(i) for i in ids[weights > 0.0001])
        if not active or min(active) < 0 or max(active) >= len(joints):
            raise ValueError('Rig has no valid weighted joints.')
        mesh_inverse = np.linalg.inv(matrix(mesh_node_id))
        for local_joint in sorted(active):
            node_id = joints[local_joint]
            rest = mesh_inverse @ matrix(node_id) @ inverse[local_joint]
            if not np.all(np.isfinite(rest)) or np.max(np.abs(rest-np.eye(4))) > tolerance:
                name = doc['nodes'][node_id].get('name','joint '+str(local_joint))
                raise ValueError(
                    'Incompatible character bind/rest pose for ' + name +
                    '; try the correct stage CDF, skin, and skeleton.')
        weighted_total += len(active)
    return weighted_total

def skin_companion_path(source):
    """Use the corresponding native .skin file; never guess alternate skeletons."""
    path = Path(source)
    if path.suffix.casefold() == '.skinm':
        return path.with_suffix('.skin')
    if path.suffix.casefold() == '.chrm':
        return path.with_suffix('.chr')
    return None


def skin_glb_composite(glb, rig_parts, skeleton_raw):
    """Attach one verified skeleton to multiple CDF skin meshes in one GLB.

    rig_parts are (native render bytes, native skin bytes, vertex_count).
    Each part has a local bone palette; indices are mapped by bone name to
    the master CDF skeleton, never assumed to share integer bone indices.
    """
    from model_textured_export import _unpack_glb, _pack_glb
    doc, binary = _unpack_glb(glb)
    if doc.get('skins') or len(doc.get('meshes', [])) != 1:
        raise ValueError('Expected one unskinned composite GLB')
    names, parents, world, inverse = parse_compiled_bones(skeleton_raw)
    name_to_id = {n.casefold():i for i,n in enumerate(names)}
    arrays, weights = [], []
    # The CDF supplies the hierarchy; each skin supplies its actual bind pose.
    # Stage-specific skins can legitimately differ from the animation CHR.
    skin_rest = {}
    for render_raw, skin_raw, count in rig_parts:
        part_names, part_parents, part_world, part_inverse = parse_compiled_bones(skin_raw)
        ids, influence, _subsets = remap_skin_weights(render_raw, count, len(part_names))
        active = set(np.unique(ids[influence > 0]).tolist())
        mapped_names = []
        for i, name in enumerate(part_names):
            master = name_to_id.get(name.casefold())
            if master is None:
                raise ValueError('CDF skin bone is absent: ' + name)
            expected_parent = names[parents[master]].casefold() if parents[master] >= 0 else None
            actual_parent = part_names[part_parents[i]].casefold() if part_parents[i] >= 0 else None
            # Accessory skin hierarchies may be flattened (e.g. Lennox's ammo belt);
            # attachment skinning maps world bind matrices by joint name.
            # The primary rig must still match the CDF hierarchy.
            if not arrays and expected_parent != actual_parent:
                raise ValueError('CDF skin bone hierarchy differs: ' + name)
            if i in active and master in skin_rest and not np.allclose(
                    skin_rest[master][0], part_world[i], atol=.001, rtol=0):
                raise ValueError('CDF skins have conflicting bind poses: ' + name)
            # Unweighted accessory bones often contain stale rest poses.
            # The primary skin establishes the animation rest hierarchy;
            # only weighted accessory joints can override an unused joint.
            if not arrays or (i in active and master not in skin_rest):
                world[master], inverse[master] = part_world[i], part_inverse[i]
            if i in active:
                skin_rest[master] = (part_world[i], part_inverse[i])
            mapped_names.append(master)
        ids = np.take(np.asarray(mapped_names, dtype='<u2'), ids)
        arrays.append(ids.astype('<u2'))
        weights.append(influence.astype('u1'))
    if not arrays:
        raise ValueError('No compatible skinned render parts')
    for master, (rest, bind) in skin_rest.items():
        world[master], inverse[master] = rest, bind
    width = max(a.shape[1] for a in arrays)
    bone_ids = np.concatenate([np.pad(a, ((0,0),(0,width-a.shape[1]))) for a in arrays])
    packed_weights = np.concatenate([np.pad(a, ((0,0),(0,width-a.shape[1]))) for a in weights])
    primitives = doc['meshes'][0].get('primitives', [])
    positions = {p.get('attributes',{}).get('POSITION') for p in primitives}
    if len(positions) != 1 or None in positions:
        raise ValueError('Composite primitives must share one vertex accessor')
    count = doc['accessors'][positions.pop()]['count']
    if len(bone_ids) != count or len(packed_weights) != count:
        raise ValueError('Composite mesh vertex count differs from combined weights')
    first = len(doc['nodes'])
    nodes = [{'name':name,'children':[]} for name in names]
    for i,parent in enumerate(parents):
        local = world[i] if parent == -1 else np.linalg.inv(world[parent]) @ world[i]
        local = BASIS @ local @ INVERSE_BASIS
        nodes[i]['matrix'] = [float(x) for x in local.reshape(-1,order='F')]
        if parent != -1:
            nodes[parent]['children'].append(first+i)
    for node in nodes:
        if not node['children']:
            del node['children']
    doc['nodes'].extend(nodes)
    root = parents.index(-1)
    doc['scenes'][doc.get('scene',0)]['nodes'].append(first+root)
    def add_view(payload, target=None):
        binary.extend(b'\0'*(-len(binary)%4))
        pos = len(binary)
        binary.extend(payload)
        obj = {'buffer':0,'byteOffset':pos,'byteLength':len(payload)}
        if target is not None: obj['target']=target
        doc['bufferViews'].append(obj)
        return len(doc['bufferViews'])-1
    joint_view = add_view(bone_ids[:, :4].tobytes(),34962)
    weight_view = add_view(packed_weights[:, :4].tobytes(),34962)
    inv = np.asarray([(BASIS@m@INVERSE_BASIS).reshape(-1,order='F')
                      for m in inverse],dtype='<f4')
    inv_view = add_view(inv.tobytes())
    def accessor(view, kind, n, component):
        i=len(doc['accessors'])
        doc['accessors'].append({'bufferView':view,'componentType':component,
                                'count':n,'type':kind})
        return i
    ji=accessor(joint_view,'VEC4',count,5123)
    wi=accessor(weight_view,'VEC4',count,5121)
    doc['accessors'][wi]['normalized']=True
    mi=accessor(inv_view,'MAT4',len(names),5126)
    for primitive in primitives:
        primitive['attributes']['JOINTS_0'] = ji
        primitive['attributes']['WEIGHTS_0'] = wi
    _extra_influences(doc, add_view, primitives, bone_ids, packed_weights)
    doc['skins']=[{'name':'CryEngine CDF '+names[root],
                   'joints':[first+i for i in range(len(names))],
                   'inverseBindMatrices':mi,'skeleton':first+root}]
    instances=[node for node in doc['nodes'] if node.get('mesh')==0]
    if len(instances)!=1:
        raise ValueError('Composite GLB needs exactly one mesh instance')
    instances[0]['skin']=0
    output = _pack_glb(doc,binary)
    weighted = validate_skin_rest_pose(output)
    return output, {
        'bone_count':len(names), 'vertex_count':count,
        'part_count':len(rig_parts), 'verified_weighted_bones':weighted}


def _extra_influences(doc, add_view, primitives, ids, weights):
    """Preserve all eight source influences in standard glTF attributes."""
    if ids.shape[1] <= 4:
        return
    for name, values, component in (('JOINTS_1', ids[:, 4:].astype('<u2'), 5123),
                                    ('WEIGHTS_1', weights[:, 4:], 5121)):
        view = add_view(values.tobytes(), 34962)
        acc = {'bufferView': view, 'componentType': component,
               'count': len(values), 'type': 'VEC4'}
        if name == 'WEIGHTS_1':
            acc['normalized'] = True
        position = len(doc['accessors'])
        doc['accessors'].append(acc)
        for primitive in primitives:
            primitive['attributes'][name] = position



def skin_glb_composite_separate(glb, rig_parts, *, part_names=None):
    """Export incompatible CDF skin bind poses as *independent* Blender armatures.

    Each source skin has its own verified rest hierarchy and inverse bindings.
    glTF allows one skin per mesh *node*, not per mesh primitive, so the
    composite is split into contiguous source parts. The original material
    indices, UVs, normals, tangents and texture payloads are retained.
    This is deliberately not
    presented as one retargetable skeleton or as a synchronized CAF animation.
    """
    from model_textured_export import _unpack_glb, _pack_glb
    from model_animation import _accessor
    doc, binary = _unpack_glb(glb)
    if doc.get('skins') or len(doc.get('meshes', [])) != 1:
        raise ValueError('Expected one unskinned composite GLB.')
    source_primitives = doc['meshes'][0].get('primitives', ())
    if not source_primitives or not rig_parts or len(rig_parts) > 32:
        raise ValueError('Missing or excessive native render parts.')
    vertex_attrs = source_primitives[0]['attributes']
    if any(p['attributes'] != vertex_attrs for p in source_primitives):
        raise ValueError('Composite attributes differ between material subsets.')
    if 'POSITION' not in vertex_attrs:
        raise ValueError('Composite lacks vertex positions.')
    position = _accessor(doc, binary, vertex_attrs['POSITION']).astype('<f4')
    if position.shape[1] != 3:
        raise ValueError('Composite POSITION must be a float3 accessor.')
    uvs = (_accessor(doc, binary, vertex_attrs['TEXCOORD_0']).astype('<f4')
           if 'TEXCOORD_0' in vertex_attrs else None)
    if uvs is not None and (uvs.shape != (len(position),2)):
        raise ValueError('Composite UV count differs from vertex count.')
    if len(position) != sum(int(part[2]) for part in rig_parts):
        raise ValueError('Source parts do not match composite vertex count.')
    # The single-mesh exporter already generates per-vertex NORMAL and
    # TANGENT. Splitting incompatible CDF bind poses must NOT discard them:
    # doing so changes Blender lighting and breaks tangent-space normal maps.
    shading = {}
    for semantic, width in (('NORMAL', 3), ('TANGENT', 4)):
        if semantic not in vertex_attrs:
            continue
        source_accessor = doc['accessors'][vertex_attrs[semantic]]
        if (source_accessor.get('componentType') != 5126 or
                source_accessor.get('type') != 'VEC%d' % width):
            raise ValueError('Unsupported CDF composite ' + semantic + ' accessor.')
        values = _accessor(doc, binary, vertex_attrs[semantic]).astype('<f4')
        if values.shape != (len(position), width):
            raise ValueError('CDF composite ' + semantic + ' count differs from positions.')
        shading[semantic] = values
    if 'TANGENT' in shading and ('NORMAL' not in shading or uvs is None):
        raise ValueError('Composite tangents require normals and UV coordinates.')
    label_names = tuple(part_names or ())
    if label_names and len(label_names) != len(rig_parts):
        raise ValueError('Part name count must match source skins.')
    original_node = [node for node in doc.get('nodes', ()) if node.get('mesh') == 0]
    if len(original_node) != 1 or 'skin' in original_node[0]:
        raise ValueError('Expected one originally unskinned mesh node.')
    if any(key in original_node[0] for key in ('matrix','translation','rotation','scale')):
        raise ValueError('Cannot split transformed composite without baking its node transform.')
    original_node[0].pop('mesh')
    doc['meshes'] = []
    doc['skins'] = []
    first = 0
    attributed = set()

    def add_view(payload, target=None):
        binary.extend(b'\x00' * (-len(binary) % 4))
        offset = len(binary)
        binary.extend(payload)
        view = {'buffer':0,'byteOffset':offset,'byteLength':len(payload)}
        if target is not None:
            view['target'] = target
        doc['bufferViews'].append(view)
        return len(doc['bufferViews'])-1

    def add_accessor(view, kind, count, component, **kwargs):
        accession = {'bufferView':view,'componentType':component,
                     'count':int(count),'type':kind}
        accession.update(kwargs)
        doc['accessors'].append(accession)
        return len(doc['accessors'])-1

    scene = doc['scenes'][doc.get('scene',0)]['nodes']
    for part_index,(render_raw,skin_raw,count) in enumerate(rig_parts):
        count = int(count)
        if count <= 0 or first+count > len(position):
            raise ValueError('Invalid CDF part vertex interval.')
        part_label = (str(label_names[part_index]) if label_names else
                      'Part_%d' % (part_index+1))
        names, parents, world, inverse = parse_compiled_bones(skin_raw)
        ids, weights, subsets = remap_skin_weights(render_raw,count,len(names))
        local_positions = np.ascontiguousarray(position[first:first+count],dtype='<f4')
        pos_view = add_view(local_positions.tobytes(),34962)
        bounds_lo = [float(x) for x in local_positions.min(axis=0)]
        bounds_hi = [float(x) for x in local_positions.max(axis=0)]
        attributes = {'POSITION':add_accessor(
            pos_view,'VEC3',count,5126,min=bounds_lo,max=bounds_hi)}
        if uvs is not None:
            uv_view = add_view(np.ascontiguousarray(
                uvs[first:first+count],dtype='<f4').tobytes(),34962)
            attributes['TEXCOORD_0'] = add_accessor(uv_view,'VEC2',count,5126)
        for semantic, values in shading.items():
            # Preserve source-normalized directions and tangent handedness.
            # Recomputing after subdivision can change the visible seams.
            fragment = np.ascontiguousarray(values[first:first+count], dtype='<f4')
            view = add_view(fragment.tobytes(), 34962)
            attributes[semantic] = add_accessor(
                view, 'VEC3' if semantic == 'NORMAL' else 'VEC4', count, 5126)
        j0 = add_view(ids[:,:4].astype('<u2').tobytes(),34962)
        w0 = add_view(weights[:,:4].tobytes(),34962)
        attributes['JOINTS_0'] = add_accessor(j0,'VEC4',count,5123)
        attributes['WEIGHTS_0'] = add_accessor(w0,'VEC4',count,5121,normalized=True)
        if ids.shape[1] > 4:
            j1 = add_view(ids[:,4:8].astype('<u2').tobytes(),34962)
            w1 = add_view(weights[:,4:8].tobytes(),34962)
            attributes['JOINTS_1'] = add_accessor(j1,'VEC4',count,5123)
            attributes['WEIGHTS_1'] = add_accessor(
                w1,'VEC4',count,5121,normalized=True)

        primitives = []
        for primitive_id,primitive in enumerate(source_primitives):
            indices = _accessor(doc,binary,primitive['indices']).reshape(-1)
            if len(indices) == 0:
                continue
            min_idx, max_idx = int(indices.min()),int(indices.max())
            if min_idx < first or max_idx >= first+count:
                continue
            if primitive_id in attributed:
                raise ValueError('Composite primitive assigned to multiple parts.')
            attributed.add(primitive_id)
            local_idx = np.ascontiguousarray(indices.astype('<u4')-first)
            view = add_view(local_idx.tobytes(),34963)
            idx_acc = add_accessor(view,'SCALAR',len(local_idx),5125,
                                   min=[int(local_idx.min())],
                                   max=[int(local_idx.max())])
            primitives.append({'attributes':dict(attributes),
                               'indices':idx_acc,
                               'material':primitive['material'],
                               'mode':primitive.get('mode',4)})
        if not primitives:
            raise ValueError('CDF part has no material subset triangles.')
        mesh_id = len(doc['meshes'])
        doc['meshes'].append({'name':part_label, 'primitives':primitives})
        node_id = len(doc['nodes'])
        doc['nodes'].append({'name':part_label+'_Mesh',
                             'mesh':mesh_id,'skin':len(doc['skins'])})
        scene.append(node_id)
        first_joint = len(doc['nodes'])
        bones = [{'name':part_label+' :: '+name,'children':[]} for name in names]
        for index,parent in enumerate(parents):
            local = world[index] if parent < 0 else np.linalg.inv(world[parent]) @ world[index]
            matrix = BASIS @ local @ INVERSE_BASIS
            bones[index]['matrix'] = [float(x) for x in matrix.reshape(-1,order='F')]
            if parent >= 0:
                bones[parent]['children'].append(first_joint+index)
        for bone in bones:
            if not bone['children']:
                bone.pop('children')
        doc['nodes'].extend(bones)
        root = first_joint+parents.index(-1)
        scene.append(root)
        bind = np.array(
            [(BASIS@m@INVERSE_BASIS).reshape(-1,order='F') for m in inverse],
            dtype='<f4')
        view = add_view(bind.tobytes())
        mat_acc = add_accessor(view,'MAT4',len(names),5126)
        doc['skins'].append({
            'name':part_label+'_Armature',
            'joints':[first_joint+i for i in range(len(names))],
            'inverseBindMatrices':mat_acc,'skeleton':root})
        first += count
    if len(attributed) != len(source_primitives):
        raise ValueError('Some composite material primitives span multiple native skins.')
    doc.setdefault('extras',{})['rigging'] = (
        'Independent verified native armatures per CDF part; '
        'pose each in Blender. Shared CAF retargeting is not included.')
    output = _pack_glb(doc,binary)
    weighted = validate_skin_rest_pose(output)
    return output, {'bone_count':sum(len(s['joints']) for s in doc['skins']),
                    'part_count':len(rig_parts),
                    'armatures':len(doc['skins']),
                    'verified_weighted_bones':weighted}
