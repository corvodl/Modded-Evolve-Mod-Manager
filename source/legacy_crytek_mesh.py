"""Bounds-checked read-only preview of historical CryTek 0x745 geometry.

These historical .chr files use a 20-byte (five-dword) chunk directory,
not the CrChF v7 four-dword directory. No skinning, rendering material subset
layout, or animation conversion is inferred.
"""
from __future__ import annotations

import math
import struct

CHUNK_STRUCT = struct.Struct('<5I')
STREAM_CHUNK = 0xCCCC0016
SUPPORTED_VERSION = 0x745
MAX_CHUNKS = 100_000
MAX_VERTICES = 250_000
MAX_TRIANGLES = 500_000
MAX_FILE_BYTES = 256 * 1024 * 1024


def parse_crytek_chunks(data, *, max_chunks=MAX_CHUNKS):
    """Return (filetype, version, table_at, table_bytes, rows) or fail closed."""
    if len(data) > MAX_FILE_BYTES or len(data) < 24 or data[:8] != b'CryTek\0\0':
        raise ValueError('Invalid or oversized CryTek model header.')
    filetype, version, table_at = struct.unpack_from('<3I', data, 8)
    if filetype not in (0xFFFF0000, 0xFFFF0001):
        raise ValueError('Unknown CryTek geometry/animation file type.')
    if table_at < 20 or table_at > len(data) - 4:
        raise ValueError('Invalid CryTek table offset.')
    count, = struct.unpack_from('<I', data, table_at)
    start = table_at + 4
    end = start + count * CHUNK_STRUCT.size
    if not 0 < count <= max_chunks or end > len(data):
        raise ValueError('Missing, incomplete or oversized CryTek chunk directory.')
    rows = tuple(CHUNK_STRUCT.iter_unpack(data[start:end]))
    ranges, ids = [], set()
    for kind, chunk_version, offset, chunk_id, size in rows:
        if (size <= 0 or offset < end or offset > len(data) - size
                or chunk_id in ids):
            raise ValueError('Unsafe, duplicate or out-of-bounds CryTek chunk.')
        ids.add(chunk_id)
        ranges.append((offset, offset + size))
    ranges.sort()
    if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
        raise ValueError('Overlapping CryTek chunks.')
    return filetype, version, table_at, data[table_at:end], rows


def read_crytek_geometry(data):
    """Return (positions, triangles, uvs) for a non-rigged preview."""
    filetype, version, _, _, chunks = parse_crytek_chunks(data)
    if filetype != 0xFFFF0000 or version != SUPPORTED_VERSION:
        raise ValueError('Legacy CryTek mesh preview supports geometry version 0x745 only.')
    streams = {}
    for kind, chunk_version, offset, ident, size in chunks:
        if kind != STREAM_CHUNK:
            continue
        if chunk_version != 0x800 or size < 40:
            raise ValueError('Unsupported legacy geometry stream version/header.')
        if struct.unpack_from('<4I', data, offset) != (kind, chunk_version, offset, ident):
            raise ValueError('Legacy stream header disagrees with its chunk directory.')
        flags, stream_type, count, stride, a, b = struct.unpack_from('<6I', data, offset + 16)
        if (flags or a or b or count == 0 or stride == 0
                or size != 40 + count * stride):
            raise ValueError('Invalid legacy geometry stream shape.')
        if stream_type in (0, 2, 5):
            if stream_type in streams:
                raise ValueError('Ambiguous duplicate legacy geometry stream.')
            streams[stream_type] = (offset + 40, count, stride)
    if 0 not in streams or 5 not in streams:
        raise ValueError('Legacy character has no renderable position/index streams.')
    point_at, count, stride = streams[0]
    index_at, index_count, index_stride = streams[5]
    if (stride != 12 or index_stride != 2
            or not 3 <= count <= MAX_VERTICES
            or not 3 <= index_count <= MAX_TRIANGLES * 3
            or index_count % 3):
        raise ValueError('Unsupported legacy vertex/index encoding or safety bounds.')
    positions = tuple(struct.iter_unpack('<3f', memoryview(data)[point_at:point_at + 12 * count]))
    if not all(math.isfinite(v) and abs(v) < 1e7 for xyz in positions for v in xyz):
        raise ValueError('Non-finite or out-of-bounds legacy mesh coordinates.')
    indices = struct.unpack_from('<' + str(index_count) + 'H', data, index_at)
    if any(i >= count for i in indices):
        raise ValueError('Legacy triangle index outside vertex array.')
    triangles = tuple(zip(indices[::3], indices[1::3], indices[2::3]))
    uvs = ()
    if 2 in streams:
        at, num, uv_stride = streams[2]
        if num != count or uv_stride != 8:
            raise ValueError('Unsupported legacy texture coordinate stream.')
        uvs = tuple(struct.iter_unpack('<2f', memoryview(data)[at:at + 8 * num]))
        if not all(math.isfinite(v) and abs(v) <= 1e4 for row in uvs for v in row):
            raise ValueError('Invalid legacy UV coordinates.')
    return positions, triangles, uvs
