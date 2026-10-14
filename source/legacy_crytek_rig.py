"""Conservative skeletal parsing for the 2012 CryTek 0x745 .chr family.

The 2012 prototype is neither retail Legacy nor Stage 2. This module never
changes source files, guesses palette mappings, normalizes broken weights or
interprets a DBA container as a playable CAF.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

import numpy as np
from legacy_crytek_mesh import parse_crytek_chunks, SUPPORTED_VERSION

BONE_CHUNK = 0xACDC0000
STREAM_CHUNK = 0xCCCC0016
SUBSETS_CHUNK = 0xCCCC0017
BONE_RECORD = 584
MAX_BONES = 1024
MAX_VERTICES = 250_000


@dataclass(frozen=True)
class LegacyRig:
    names: tuple
    parents: tuple
    rest: tuple
    inverse: tuple
    joints: np.ndarray  # four u16 master-skeleton IDs per vertex
    weights: np.ndarray  # four u8 weights per vertex; exact sum 255
    subset_count: int
    body_bones: tuple

    @property
    def active_joints(self):
        return tuple(sorted(set(int(n) for n in self.joints[self.weights > 0])))


def _one_chunk(data, kind):
    filetype, version, _, _, entries = parse_crytek_chunks(data)
    if filetype != 0xffff0000 or version != SUPPORTED_VERSION:
        raise ValueError('2012 skeletal support needs CryTek geometry version 0x745.')
    matches = [(ver, offset, ident, size) for typ, ver, offset, ident, size
               in entries if typ == kind]
    if len(matches) != 1:
        raise ValueError('Expected one historical chunk %08X, found %d.' %
                         (kind, len(matches)))
    ver, offset, ident, size = matches[0]
    if ver != 0x800 or size < 32:
        raise ValueError('Unknown historical chunk version/size for %08X.' % kind)
    if struct.unpack_from('<4I', data, offset) != (kind, ver, offset, ident):
        raise ValueError('Historical embedded chunk header does not match directory.')
    return offset, size


def read_legacy_bones(raw):
    """Validate native 48+N*584 chunk and return named parent and bind tables.

    The 32-byte payload prefix remains opaque and is not silently discarded:
    the embedded 16-byte chunk header, declared size, and complete record
    layout must all match. Bind transforms require both finite, inverse pairs.
    """
    offset, size = _one_chunk(raw, BONE_CHUNK)
    if size < 48 or (size - 48) % BONE_RECORD:
        raise ValueError('Historical bone records are not 48 + N*584 bytes.')
    n = (size - 48) // BONE_RECORD
    if not 1 <= n <= MAX_BONES:
        raise ValueError('Historical bone table exceeds safety limit.')
    prefix = raw[offset + 16:offset + 48]
    if len(prefix) != 32:
        raise ValueError('Truncated historical bone table prefix.')
    names, parents, rest, inverse = [], [], [], []
    seen = set()
    for i in range(n):
        record = offset + 48 + i * BONE_RECORD
        raw_name = raw[record + 312:record + 568]
        if b'\0' not in raw_name:
            raise ValueError('Unterminated historical bone name.')
        name_bytes = raw_name.split(b'\0', 1)[0]
        try:
            name = name_bytes.decode('utf-8', errors='strict')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid UTF-8 historical bone name.') from exc
        name = name.strip()
        key = name.casefold()
        if not name or key in seen:
            raise ValueError('Blank or duplicate historical bone name.')
        seen.add(key)
        a = np.asarray(struct.unpack_from('<12f', raw, record + 216),
                       dtype=np.float64).reshape(3, 4)
        b = np.asarray(struct.unpack_from('<12f', raw, record + 264),
                       dtype=np.float64).reshape(3, 4)
        inv = np.vstack((a, (0., 0., 0., 1.)))
        world = np.vstack((b, (0., 0., 0., 1.)))
        if (not np.isfinite(inv).all() or not np.isfinite(world).all()
                or np.max(np.abs(inv)) > 1e7 or np.max(np.abs(world)) > 1e7
                or np.max(np.abs(inv @ world - np.eye(4))) > 0.001):
            raise ValueError('Invalid inverse/rest matrices for historical bone: ' + name)
        _, parent_delta, _, _ = struct.unpack_from('<4i', raw, record + 568)
        parent = i + parent_delta if parent_delta < 0 else -1
        if parent < -1 or parent >= i:
            raise ValueError('Invalid parent offset for historical bone: ' + name)
        names.append(name)
        parents.append(parent)
        rest.append(world)
        inverse.append(inv)
    if parents.count(-1) != 1:
        raise ValueError('Historical skeleton must have one root.')
    return tuple(names), tuple(parents), tuple(rest), tuple(inverse)


def _legacy_streams(raw):
    filetype, version, _, _, entries = parse_crytek_chunks(raw)
    if filetype != 0xffff0000 or version != SUPPORTED_VERSION:
        raise ValueError('Unsupported historical geometry container.')
    streams = {}
    for kind, ver, offset, ident, size in entries:
        if kind != STREAM_CHUNK:
            continue
        if (ver != 0x800 or size < 40 or
                struct.unpack_from('<4I', raw, offset) != (kind, ver, offset, ident)):
            raise ValueError('Invalid historical vertex stream header.')
        flags, typ, count, stride, a, b = struct.unpack_from('<6I', raw, offset + 16)
        if flags or a or b or count == 0 or stride == 0 or 40 + count * stride != size:
            raise ValueError('Inconsistent historical vertex stream layout.')
        if typ in streams:
            raise ValueError('Duplicate historical stream type %d.' % typ)
        streams[typ] = (offset + 40, count, stride)
    return streams


def _remap_body_bones(body_names, body_parents, body_rest,
                      master_names, master_parents, master_rest):
    lookup = {name.casefold(): i for i, name in enumerate(master_names)}
    ids = []
    body_keys = {name.casefold() for name in body_names}
    for i, name in enumerate(body_names):
        key = name.casefold()
        if key not in lookup:
            raise ValueError('Body bone absent from master skeleton: ' + name)
        j = lookup[key]
        parent = body_parents[i]
        ancestor = master_parents[j]
        while ancestor != -1 and master_names[ancestor].casefold() not in body_keys:
            ancestor = master_parents[ancestor]
        actual = master_names[ancestor].casefold() if ancestor != -1 else None
        expected = body_names[parent].casefold() if parent != -1 else None
        if actual != expected:
            raise ValueError('Body/master bone hierarchy differs at ' + name)
        if not np.allclose(body_rest[i], master_rest[j], atol=0.005, rtol=0.0001):
            raise ValueError('Body/master bone rest transform differs at ' + name)
        ids.append(j)
    return np.asarray(ids, dtype=np.uint16)


def read_legacy_rig(body, skeleton, vertex_count):
    """Validate palette, weights, identities, topology and transforms together.

    Subset palette entries are first resolved into *body-table* indices, and
    then by verified name, ancestry and bind pose into master-skeleton IDs.
    If this assumption is wrong for a real build, fail and report; don't
    silently use the raw palette values as master IDs.
    """
    if not 3 <= vertex_count <= MAX_VERTICES:
        raise ValueError('Invalid historical vertex count.')
    body_names, body_parents, body_rest, _ = read_legacy_bones(body)
    names, parents, rest, inverse = read_legacy_bones(skeleton)
    body_to_master = _remap_body_bones(
        body_names, body_parents, body_rest, names, parents, rest)
    streams = _legacy_streams(body)
    if 9 not in streams or 5 not in streams or 0 not in streams:
        raise ValueError('Historical mesh lacks position, skin or index stream.')
    _, vcount, _ = streams[0]
    at, count, stride = streams[9]
    if vcount != vertex_count or count != vertex_count or stride != 8:
        raise ValueError('Historical weights do not match mesh vertex count.')
    packed = np.frombuffer(body, dtype=np.uint8, offset=at, count=count * 8).reshape(count, 8)
    local_ids = packed[:, :4].copy()
    weights = packed[:, 4:].copy()
    if np.any(weights.astype(np.uint16).sum(axis=1) != 255):
        raise ValueError('Historical vertex influences do not sum to 255.')
    index_at, n_indices, index_stride = streams[5]
    if index_stride != 2 or n_indices % 3 or n_indices > 1_500_000:
        raise ValueError('Unsupported historical index stream.')
    indices = np.frombuffer(body, dtype='<u2', count=n_indices, offset=index_at)
    if np.any(indices >= vertex_count):
        raise ValueError('Historical subset indices escape vertex array.')
    offset, size = _one_chunk(body, SUBSETS_CHUNK)
    flags, count_subsets, a, b = struct.unpack_from('<4I', body, offset + 16)
    if (flags not in (0, 2) or a or b or not 1 <= count_subsets <= 128
            or size != 32 + count_subsets * (36 + 260)):
        raise ValueError('Historical subset palette layout is unverified.')
    joints = np.zeros((vertex_count, 4), dtype=np.uint16)
    covered = np.zeros(vertex_count, dtype=bool)
    cursor = 0
    for subset in range(count_subsets):
        first, length, base, n_vertices, _ = struct.unpack_from(
            '<5I', body, offset + 32 + subset * 36)
        if (first != cursor or not length or length % 3 or
                first + length > n_indices or base + n_vertices > vertex_count):
            raise ValueError('Historical subset geometry bounds invalid.')
        cursor += length
        palette_at = offset + 32 + count_subsets * 36 + subset * 260
        palette_count, = struct.unpack_from('<I', body, palette_at)
        if not 1 <= palette_count <= 128:
            raise ValueError('Invalid historical subset bone palette length.')
        palette = np.frombuffer(body, dtype='<u2', count=128, offset=palette_at + 4)
        if np.any(palette[:palette_count] >= len(body_names)):
            raise ValueError('Historical palette contains invalid body bone ID.')
        used = np.unique(indices[first:first + length]).astype(np.intp)
        if (not len(used) or used[0] < base or used[-1] >= base + n_vertices):
            raise ValueError('Subset bone palette mismatches vertex range.')
        relevant_ids = local_ids[used]
        relevant_weights = weights[used]
        active = relevant_weights > 0
        if np.any(relevant_ids[active] >= palette_count):
            raise ValueError('Active local joint exceeds historical palette.')
        body_indices = palette[np.where(active, relevant_ids, 0)]
        converted = body_to_master[body_indices].astype(np.uint16)
        converted[~active] = 0
        shared = covered[used]
        if shared.any() and not np.array_equal(joints[used[shared]], converted[shared]):
            raise ValueError('Historical subset palettes disagree for shared vertices.')
        joints[used] = converted
        covered[used] = True
    if cursor != n_indices or not covered.all():
        raise ValueError('Historical subset palettes do not cover all mesh vertices.')
    if not np.any(weights > 0):
        raise ValueError('No active historical vertex influences.')
    return LegacyRig(names, parents, rest, inverse, joints, weights,
                     count_subsets, body_names)


def probe_legacy_dba(raw):
    """Metadata only. A chunk is NOT an animation or a decoded clip."""
    filetype, version, _, _, entries = parse_crytek_chunks(raw)
    if filetype != 0xffff0000 or version != SUPPORTED_VERSION:
        raise ValueError('Unknown 2012 CryTek DBA container.')
    if len(entries) != 1 or entries[0][0] != 0xCCCC000D or entries[0][1] != 0x905:
        raise ValueError('Unrecognized 2012 DBA database chunk structure.')
    return {'version': hex(version), 'database_chunk_count': 1,
            'database_chunk_version': hex(entries[0][1]),
            'clips_decoded': 0, 'playback_available': False,
            'reason': 'Historical 0x905 DBA clip/controller encoding remains unverified.'}


def skin_glb_legacy(glb, body_raw, skeleton_raw):
    """Export validated native .chr geometry as a *real* glTF skeleton/skin.

    No placeholder/rigid-to-root influences are permitted. The existing
    common exporter validates the complete rest pose again after writing.
    """
    from model_textured_export import _unpack_glb
    from model_rigging import skin_glb_verified
    document, _ = _unpack_glb(glb)
    meshes = document.get('meshes', ())
    if len(meshes) != 1 or not meshes[0].get('primitives'):
        raise ValueError('Expected one full historical character GLB mesh.')
    positions = {p['attributes'].get('POSITION')
                 for p in meshes[0]['primitives']}
    if len(positions) != 1 or None in positions:
        raise ValueError('Historical GLB has no shared POSITION accessor.')
    vertex_count = document['accessors'][positions.pop()]['count']
    rig = read_legacy_rig(body_raw, skeleton_raw, vertex_count)
    payload, summary = skin_glb_verified(
        glb, rig.names, rig.parents, rig.rest, rig.inverse,
        rig.joints, rig.weights, rig.subset_count)
    summary['source_format'] = 'CryTek 0x745'
    summary['body_bones'] = len(rig.body_bones)
    summary['master_bones'] = len(rig.names)
    return payload, summary
