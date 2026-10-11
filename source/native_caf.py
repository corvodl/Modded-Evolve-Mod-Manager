"""CryEngine compressed CAF controller decoder and glTF animation bridge.

Pure Python; never modifies native game files. Supports version 0x829 and 0x831
per-bone rotation/position tracks, CryEngine quantized quaternion families,
and bounded time keys. Controller IDs are CRC32 of lowercase skeleton names.

The output is an ordinary animated glTF 2.0 GLB, consumable by the manager's
existing GPU playback pipeline and Blender. DBA/bitset tracks are not decoded.
Reference: CryEngine public chunk format descriptions and Evolve CAF samples.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct
import zlib

import numpy as np

from model_rigging import BASIS, INVERSE_BASIS
from model_textured_export import _pack_glb, _unpack_glb

MAX_CAF_BYTES = 64 * 1024 * 1024
MAX_CHANNEL_KEYS = 8192
MAX_CONTROLLERS = 1500
ROTATION_SIZE = {1: 16, 3: 6, 4: 4, 5: 6, 6: 8, 8: 8}
TIME_SIZE = {0: 4, 1: 2, 2: 1, 3: 4, 4: 2, 5: 1}


@dataclass(frozen=True)
class CafTrack:
    controller_id: int
    rotation_times: tuple
    rotations: tuple
    position_times: tuple
    positions: tuple


@dataclass(frozen=True)
class CafMotion:
    tracks: tuple
    start_frame: float
    end_frame: float
    fps: float
    additive: bool
    cyclic: bool


def _read_quaternion(raw, at, format_id):
    if format_id == 1:
        q = struct.unpack_from('<4f', raw, at)
    elif format_id == 3:
        xyz = [v / 32767.0 for v in struct.unpack_from('<3h', raw, at)]
        q = (*xyz, math.sqrt(max(0.0, 1.0 - sum(v * v for v in xyz))))
    else:
        widths = {4: (10, 10, 10), 5: (15, 15, 15),
                  6: (20, 20, 20), 8: (21, 21, 20)}.get(format_id)
        if widths is None:
            raise ValueError('Unsupported CAF quaternion compression %d' % format_id)
        size = ROTATION_SIZE[format_id]
        packed = int.from_bytes(raw[at:at + size], 'little')
        largest = (packed >> (size * 8 - 2)) & 3
        components = []
        shift = 0
        for bits in widths:
            value = (packed >> shift) & ((1 << bits) - 1)
            scale = {10: 723., 15: 23170., 20: 741454., 21: 1482909.}[bits]
            components.append(value / scale - math.sqrt(.5))
            shift += bits
        q = []
        k = 0
        for j in range(4):
            if j == largest:
                q.append(math.sqrt(max(0., 1. - sum(v*v for v in components))))
            else:
                q.append(components[k])
                k += 1
    length = math.sqrt(sum(x*x for x in q))
    if not math.isfinite(length) or length < .25 or length > 1.75:
        raise ValueError('Invalid compressed CAF quaternion')
    return tuple(float(x/length) for x in q)


def _read_times(raw, offset, count, kind):
    size = TIME_SIZE.get(kind)
    if size is None:
        raise ValueError('Unsupported CAF time encoding %d' % kind)
    if offset + count * size > len(raw):
        raise ValueError('Truncated CAF time track')
    codes = {1: 'B', 2: 'H', 4: 'f'}
    values = tuple(float(x[0]) for x in struct.iter_unpack(
        '<' + codes[size], raw[offset:offset + count * size]))
    if any(not math.isfinite(x) for x in values) or any(
            b < a for a, b in zip(values, values[1:])):
        raise ValueError('Invalid CAF key timestamps')
    return values, offset + count * size


def _controller(raw, version):
    offset = 0
    if version == 0x829:
        if len(raw) < 16:
            raise ValueError('Truncated CAF controller')
        cid, nr, np_, rot_fmt, rot_time, pos_fmt, pos_shared, pos_time, align, _ = (
            struct.unpack_from('<IHHBBBBBBH', raw))
        offset = 16
    elif version == 0x831:
        if len(raw) < 20:
            raise ValueError('Truncated CAF controller 831')
        cid, _flags, nr, np_, rot_fmt, rot_time, pos_fmt, pos_shared, pos_time, align = (
            struct.unpack_from('<IIHHBBBBBB', raw))
        offset = 20
    else:
        raise ValueError('Unsupported controller chunk version %04X' % version)
    if nr > MAX_CHANNEL_KEYS or np_ > MAX_CHANNEL_KEYS or nr + np_ == 0:
        raise ValueError('Invalid CAF controller key counts')
    rotations = ()
    if nr:
        n = ROTATION_SIZE.get(rot_fmt)
        if n is None or offset + nr*n > len(raw):
            raise ValueError('Unsupported/truncated CAF rotation track')
        rotations = tuple(_read_quaternion(raw, offset + i*n, rot_fmt) for i in range(nr))
        offset += nr*n
        if align:
            offset = (offset + 3) & ~3
        rotation_times, offset = _read_times(raw, offset, nr, rot_time)
        # Evolve does not pad the end of a rotation-only controller. Padding
        # separates blocks, not a trailing empty position section.
        if align and np_:
            offset = (offset + 3) & ~3
    else:
        rotation_times = ()
    positions = ()
    if np_:
        if pos_fmt not in (0, 2) or offset + np_*12 > len(raw):
            raise ValueError('Unsupported/truncated CAF position track')
        positions = tuple(struct.unpack_from('<3f', raw, offset+i*12)
                          for i in range(np_))
        if any(not all(math.isfinite(v) and abs(v) < 10000 for v in xyz)
               for xyz in positions):
            raise ValueError('Invalid CAF positions')
        offset += np_*12
        if align and pos_shared:
            offset = (offset + 3) & ~3
        if pos_shared:
            position_times, offset = _read_times(raw, offset, np_, pos_time)
        else:
            position_times = rotation_times
            if len(position_times) != np_:
                raise ValueError('CAF shared rotation/position time mismatch')
    else:
        position_times = ()
    if offset > len(raw):
        raise ValueError('CAF controller overruns chunk')
    return CafTrack(cid, rotation_times, rotations, position_times, positions)


def decode_caf(raw):
    if not isinstance(raw, bytes) or not 32 <= len(raw) <= MAX_CAF_BYTES or raw[:8] != b'CrChF\x07\x00\x00':
        raise ValueError('Not a supported CryEngine CAF')
    chunk_count, table = struct.unpack_from('<II', raw, 8)
    if not 1 <= chunk_count <= MAX_CONTROLLERS + 20 or table < 16 or table+16*chunk_count > len(raw):
        raise ValueError('Invalid CAF chunk table')
    tracks = []
    flags = 0
    start, end, fps = 0., 0., 30.
    for i in range(chunk_count):
        kind, chunk_id, size, at = struct.unpack_from('<4I', raw, table + i*16)
        if not size or at < table + 16*chunk_count or at+size > len(raw):
            raise ValueError('Malformed CAF chunk bounds')
        version = kind >> 16
        family = kind & 0xFFFF
        if family == 0x100d:
            tracks.append(_controller(raw[at:at+size], version))
        elif kind == 0x09253002 and size >= 40:
            # The header contains CryEngine motion-params including speed and
            # start/end ticks; avoid guessing offsets for unsupported headers.
            flags = struct.unpack_from('<I', raw, at)[0]
            ticks_per_frame = struct.unpack_from('<i', raw, at+8)[0]
            secs_per_tick = struct.unpack_from('<f', raw, at+12)[0]
            raw_start, raw_end = struct.unpack_from('<ii', raw, at+16)
            if ticks_per_frame > 0 and 0 < secs_per_tick < 1:
                fps = 1.0 / (ticks_per_frame * secs_per_tick)
                start = float(raw_start) / ticks_per_frame
                end = float(raw_end) / ticks_per_frame
    if not tracks:
        raise ValueError('CAF contains no playable controllers')
    if len({t.controller_id for t in tracks}) != len(tracks):
        raise ValueError('Duplicate CAF bone controllers')
    if not 1 <= fps <= 240 or not math.isfinite(fps):
        fps = 30.
    if end <= start:
        end = max((max(t.rotation_times + t.position_times, default=0.)
                   for t in tracks), default=0.)
    return CafMotion(tuple(tracks), start, end, fps, bool(flags & 1), bool(flags & 2))


def _rot_matrix(q):
    x,y,z,w = q
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]
    ], dtype=np.float64)


def _matrix_quaternion(m):
    """Return XYZW quaternion from a well-conditioned 3x3 rotation."""
    m = np.asarray(m, dtype=np.float64)
    tr = float(np.trace(m))
    if tr > 0:
        s = math.sqrt(1+tr)*2
        q = ((m[2,1]-m[1,2])/s, (m[0,2]-m[2,0])/s,
             (m[1,0]-m[0,1])/s, .25*s)
    else:
        i = int(np.argmax(np.diag(m)))
        j,k = (i+1)%3,(i+2)%3
        s = math.sqrt(max(1e-8, 1+m[i,i]-m[j,j]-m[k,k]))*2
        q = [0.,0.,0.,0.]
        q[i] = .25*s
        q[3] = (m[k,j]-m[j,k])/s
        q[j] = (m[j,i]+m[i,j])/s
        q[k] = (m[k,i]+m[i,k])/s
    q = np.asarray(q,dtype=np.float64)
    return (q/np.linalg.norm(q)).astype(np.float32)


def append_caf_animations(glb, clips, *, allow_layered=False):
    """Add native CAF tracks to a *rigged* manager GLB.

    clips: iterable of (display_name, CAF bytes). Matches bone names by CRC,
    rejects obviously incompatible skeletons, converts Cry Z-up into glTF Y-up.
    Only one skinned mesh is supported by the existing manager animation viewer.
    """
    document, binary = _unpack_glb(glb)
    skins = document.get('skins', [])
    if len(skins) != 1:
        raise ValueError('Native animation requires exactly one verified skin in the GLB')
    nodes = document['nodes']
    skin = skins[0]
    crc_nodes = {}
    for joint in skin['joints']:
        name = nodes[joint].get('name', '')
        if name:
            crc_nodes[zlib.crc32(name.casefold().encode('utf-8'))] = joint
    if not crc_nodes:
        raise ValueError('Rig is missing named CryEngine bones')
    animations = []
    for name, raw in clips:
        motion = decode_caf(raw)
        layered = (motion.additive or 'aimpose' in name.casefold()
                   or 'additive' in name.casefold())
        if layered and not allow_layered:
            # A layer alone is not a complete motion. Callers must supply
            # an explicit verified base clip and opt into layered export.
            continue
        matched = [t for t in motion.tracks if t.controller_id in crc_nodes]
        if len(matched) < min(3, max(1, len(motion.tracks)//4)):
            raise ValueError('CAF %s does not match this model skeleton' % name)
        channels, samplers = [], []
        for track in matched:
            node = crc_nodes[track.controller_id]
            original = nodes[node]
            if 'matrix' in original:
                base = np.asarray(original.pop('matrix'), dtype=np.float64).reshape(4,4,order='F')
                rot = base[:3,:3]
                scales = np.linalg.norm(rot, axis=0)
                if np.any(scales < 1e-5):
                    raise ValueError('Invalid glTF rest matrix')
                original['translation'] = [float(x) for x in base[:3,3]]
                original['rotation'] = _matrix_quaternion(rot/scales).tolist()
                original['scale'] = scales.tolist()
            for path, times, values in (
                ('rotation', track.rotation_times, track.rotations),
                ('translation', track.position_times, track.positions)):
                if not values:
                    continue
                time = np.asarray(times, dtype=np.float32)
                time = (time - time[0])/motion.fps
                # glTF accessors require increasing keys.
                if len(time) > 1 and np.any(np.diff(time) <= 0):
                    unique = np.concatenate(([True], np.diff(time) > 0))
                    time = time[unique]
                    values = tuple(v for v,keep in zip(values,unique) if keep)
                if not len(time) or len(time) > MAX_CHANNEL_KEYS or time[-1] > 3600:
                    raise ValueError('Invalid CAF playback timeline')
                if path == 'rotation':
                    vals = np.stack([_matrix_quaternion(BASIS[:3,:3] @
                        _rot_matrix(q) @ INVERSE_BASIS[:3,:3]) for q in values])
                    # Quaternion interpolation must choose the shortest arc.
                    for j in range(1,len(vals)):
                        if np.dot(vals[j-1],vals[j]) < 0: vals[j] *= -1
                else:
                    vals = np.stack([BASIS[:3,:3] @ np.asarray(p) for p in values]).astype(np.float32)
                def store(data, width):
                    binary.extend(b'\x00' * (-len(binary) % 4))
                    offset = len(binary)
                    binary.extend(np.asarray(data,dtype='<f4').tobytes())
                    view=len(document['bufferViews'])
                    document['bufferViews'].append({
                        'buffer':0,'byteOffset':offset,
                        'byteLength':len(data)*width*4})
                    acc=len(document['accessors'])
                    document['accessors'].append({
                        'bufferView':view,'componentType':5126,
                        'count':len(data),
                        'type':('SCALAR' if width==1 else 'VEC%d'%width)})
                    return acc
                ti = store(time,1)
                vi = store(vals,4 if path=='rotation' else 3)
                samplers.append({'input':ti,'output':vi,'interpolation':'LINEAR'})
                channels.append({'sampler':len(samplers)-1,
                                 'target':{'node':node,'path':path}})
        if not channels:
            raise ValueError('CAF has no compatible transform tracks')
        animations.append({
            'name': name, 'samplers': samplers, 'channels': channels,
            'extras': {'evolveLayered': bool(layered),
                       'evolveAdditive': bool(motion.additive)},
        })
    if not animations:
        raise ValueError('No standalone compatible CAF animations found')
    document.setdefault('animations',[]).extend(animations)
    document['buffers'][0]['byteLength'] = len(binary)
    return _pack_glb(document,binary), {
        'clips': len(animations), 'bone_channels':sum(len(a['channels']) for a in animations)}
