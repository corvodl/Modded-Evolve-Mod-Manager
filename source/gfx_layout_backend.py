"""Conservative Scaleform root-timeline placement editing for private GFX sessions.

Edits only uniquely identified first-frame, root PlaceObject (4) or
PlaceObject2 (26) tags. Character IDs, depth order, scripts, masks, sprite
internals, paths and other tags are kept byte-for-byte unchanged. No
ActionScript-created objects are represented as editable static placements.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
import zlib

from gfx_preview_backend import (GfxError, MAX_DECOMPRESSED, inspect_gfx,
                                 _decompress_limited)
from gfx_edit_backend import read_gfx_session, _replace_edit_copy
from gfx_placement_diagnostics import _tag_list

MAX_ELEMENTS = 512


def _take(data, pos, n):
    if n < 0 or n > 31 or pos + n > len(data) * 8:
        raise GfxError("Truncated Scaleform matrix/rectangle.")
    out = 0
    for index in range(n):
        index += pos
        out = (out << 1) | ((data[index // 8] >> (7 - index % 8)) & 1)
    return out, pos + n


def _signed(value, n):
    return 0 if n == 0 else (
        value - (1 << n) if value & (1 << (n - 1)) else value)


class _Bits:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        result, self.pos = _take(self.data, self.pos, n)
        return result

    def signed(self, n):
        return _signed(self.read(n), n)

    def size(self):
        return (self.pos + 7) // 8


def _rect(data, offset):
    b = _Bits(data[offset:])
    n = b.read(5)
    if not 2 <= n <= 31:
        raise GfxError("Unsupported GFX rectangle encoding.")
    v = tuple(b.signed(n) / 20 for _ in range(4))
    return v, b.size()


def _matrix(data, offset):
    b = _Bits(data[offset:])
    a, d, skew0, skew1 = 1., 1., 0., 0.
    if b.read(1):
        n = b.read(5)
        a, d = b.signed(n) / 65536, b.signed(n) / 65536
    if b.read(1):
        n = b.read(5)
        skew0, skew1 = b.signed(n) / 65536, b.signed(n) / 65536
    n = b.read(5)
    x, y = b.signed(n) / 20, b.signed(n) / 20
    return (a, skew0, skew1, d, x, y), b.size()


def _signed_bits(value):
    return max(2, abs(int(value)).bit_length() + 2)


def _bits(values):
    stream = []
    for value, length in values:
        if length < 0 or length > 31:
            raise GfxError("Layout transformation is too large.")
        stream.extend((value >> pos) & 1 for pos in range(length - 1, -1, -1))
    stream += [0] * (-len(stream) % 8)
    return bytes(sum(bit << (7 - offset) for offset, bit in
                     enumerate(stream[i:i + 8]))
                 for i in range(0, len(stream), 8))


def _packed_matrix(x, y, sx, sy, rotation):
    if not all(math.isfinite(v) for v in (x, y, sx, sy, rotation)):
        raise GfxError("Layout properties must be finite numbers.")
    if not (-100000 <= x <= 100000 and -100000 <= y <= 100000 and
            .01 <= sx <= 20 and .01 <= sy <= 20 and
            -360 <= rotation <= 360):
        raise GfxError("Position, scale or rotation exceeds safe limits.")
    theta = math.radians(rotation)
    # SWF MATRIX order: a, b, c, d; displayed x = a*x + c*y + tx.
    a, b = sx * math.cos(theta), sx * math.sin(theta)
    c, d = -sy * math.sin(theta), sy * math.cos(theta)
    values = [round(a * 65536), round(d * 65536),
              round(c * 65536), round(b * 65536)]
    n = max(_signed_bits(t) for t in values)
    if n > 31:
        raise GfxError("Scale/rotation exceeds SWF matrix bit capacity.")
    translated = [round(x * 20), round(y * 20)]
    nt = max(_signed_bits(t) for t in translated)
    if nt > 31:
        raise GfxError("Position exceeds SWF matrix bit capacity.")
    pairs = [(1, 1), (n, 5)] + [(v, n) for v in values[:2]]
    pairs += [(1, 1), (n, 5)] + [(v, n) for v in values[2:]]
    pairs += [(nt, 5)] + [(v, nt) for v in translated]
    return _bits(pairs)


def _properties(matrix):
    a, c, b, d, x, y = matrix
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    if sx < .01 or sy < .01:
        return x, y, sx, sy, 0., False
    # Reject skewed or reflected matrices instead of dropping shear.
    cosangle = (a * c + b * d) / (sx * sy)
    supported = (abs(cosangle) <= .002 and a * d - b * c > 0)
    return x, y, sx, sy, math.degrees(math.atan2(b, a)), supported


def _raw(data):
    inspect_gfx(data)
    if data[:3] == b"CFX":
        expected = struct.unpack_from("<I", data, 4)[0]
        return b"GFX" + data[3:8] + _decompress_limited(
            data[8:], min(expected - 8, MAX_DECOMPRESSED))
    return data


def _start(data):
    _, n = _rect(data, 8)
    return 8 + n + 4


def _records(raw):
    offset = _start(raw)
    pos = offset
    records = []
    for code, payload in _tag_list(raw, offset):
        # Recreate original tag headers, leaving all payloads untouched.
        # A canonical short/long header is valid for any SWF tag.
        length = len(payload)
        prefix = (struct.pack("<H", (code << 6) | length) if length < 63
                  else struct.pack("<HI", (code << 6) | 63, length))
        records.append((code, payload, prefix))
        pos += len(payload) + len(prefix)
    return offset, records


def _placement(code, payload):
    if code == 4:
        if len(payload) < 5:
            return None
        character, depth = struct.unpack_from("<HH", payload)
        try:
            matrix, length = _matrix(payload, 4)
        except (GfxError, IndexError):
            return None
        return character, depth, matrix, 4, length, True
    if code == 26 and len(payload) >= 5:
        flags = payload[0]
        if not flags & 0x02:  # move-only placement, no character
            return None
        depth = struct.unpack_from("<H", payload, 1)[0]
        character = struct.unpack_from("<H", payload, 3)[0]
        start = 5
        if not flags & 0x04:
            return character, depth, (1., 0., 0., 1., 0., 0.), start, 0, True
        try:
            matrix, length = _matrix(payload, start)
        except (GfxError, IndexError):
            return None
        return character, depth, matrix, start, length, True
    if code == 70 and len(payload) >= 6:
        # PlaceObject3 supports class names, filters and blend modes;
        # expose it for diagnosis but avoid destructive structural edits.
        flags1, flags2 = payload[:2]
        if not flags1 & 0x02 or flags2 & 0x08:
            return None
        depth, character = struct.unpack_from("<HH", payload, 2)
        start = 6
        try:
            matrix, _size = (_matrix(payload, start) if flags1 & 0x04 else
                             ((1., 0., 0., 1., 0., 0.), 0))
        except (GfxError, IndexError):
            return None
        return character, depth, matrix, start, 0, False
    return None


@dataclass(frozen=True)
class StaticElement:
    depth: int
    character: int
    label: str
    rect: tuple[float, float, float, float] | None
    x: float
    y: float
    scale_x: float
    scale_y: float
    rotation: float
    editable: bool
    reason: str


def static_elements(data):
    raw = _raw(data)
    start, records = _records(raw)
    info = inspect_gfx(data)
    names = dict(info.exports)
    shapes = {}
    placed = {}
    frame = 1
    for index, (code, payload, header) in enumerate(records):
        if code in (2, 22, 32, 83) and len(payload) >= 3:
            try:
                bounds, _ = _rect(payload, 2)
                shapes[struct.unpack_from("<H", payload)[0]] = bounds
            except GfxError:
                pass
        if code == 1:
            frame += 1
        if frame != 1:
            continue
        item = _placement(code, payload)
        if item is not None:
            character, depth, matrix, _start, _length, safe = item
            if depth in placed:
                placed[depth] = None  # Ambiguous multiple placements at one depth.
            else:
                placed[depth] = (index, character, matrix, safe)
        elif code == 28 and len(payload) >= 2:
            placed.pop(struct.unpack_from("<H", payload)[0], None)
        elif code == 5 and len(payload) >= 4:
            placed.pop(struct.unpack_from("<H", payload, 2)[0], None)
    output = []
    for depth, found in sorted(placed.items()):
        if found is None:
            continue
        _, character, matrix, safe = found
        x, y, sx, sy, rotation, supported = _properties(matrix)
        rect = shapes.get(character)
        reason = ("Root first-frame shape" if rect else
                  "Sprite/image has no verified bounds; position editable with numeric controls")
        editable = safe and supported
        if not editable:
            reason = "Unsupported PlaceObject3 or skew/reflection transform"
        output.append(StaticElement(
            depth, character, names.get(character, f"Character #{character}"),
            rect, x, y, sx, sy, rotation, editable, reason))
        if len(output) >= MAX_ELEMENTS:
            break
    return tuple(output)


def _encode(raw, records):
    head = raw[:_start(raw)]
    body = b"".join(prefix + payload for _, payload, prefix in records)
    data = bytearray(head + body)
    if len(data) > MAX_DECOMPRESSED:
        raise GfxError("Edited GFX exceeds decompressed movie bounds.")
    struct.pack_into("<I", data, 4, len(data))
    return bytes(data)


def edit_first_frame(data, *, depth, character, x, y, scale_x=1.,
                     scale_y=1., rotation=0., visible=True):
    """Edit one unambiguous root placement. Hide removes its placement tag."""
    items = [e for e in static_elements(data)
             if e.depth == depth and e.character == character]
    if len(items) != 1 or not items[0].editable:
        raise GfxError("Selected root placement is missing, duplicated or unsafe to edit.")
    replacement = _packed_matrix(x, y, scale_x, scale_y, rotation)
    raw = _raw(data)
    offset, records = _records(raw)
    matches = []
    frame = 1
    for index, (code, payload, _header) in enumerate(records):
        if code == 1:
            frame += 1
        if frame != 1:
            continue
        item = _placement(code, payload)
        if item and item[0] == character and item[1] == depth:
            matches.append(index)
    if len(matches) != 1:
        raise GfxError("Ambiguous root display-list placement.")
    i = matches[0]
    code, payload, header = records[i]
    if not visible:
        records.pop(i)
    else:
        _, _, _, start, n, _ = _placement(code, payload)
        if code == 26:
            payload = bytes([payload[0] | 0x04]) + payload[1:]
        payload = payload[:start] + replacement + payload[start + n:]
        length = len(payload)
        header = (struct.pack("<H", (code << 6) | length) if length < 63
                  else struct.pack("<HI", (code << 6) | 63, length))
        records[i] = (code, payload, header)
    rewritten = _encode(raw, records)
    inspect_gfx(rewritten)
    return rewritten


def stage_static_layout(workspace, session, **updates):
    snapshot = read_gfx_session(workspace, session)
    source = snapshot.working_file.read_bytes()
    rewritten = edit_first_frame(source, **updates)
    return _replace_edit_copy(snapshot, rewritten)
