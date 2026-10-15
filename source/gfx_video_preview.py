"""Inspect embedded Scaleform video without presenting FFDec's X placeholder as UI.

This module only rewrites a temporary rendering copy. Never apply its bytes to
a game PAK, a staged GFX edit, or an imported movie source.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from gfx_preview_backend import GfxError, MAX_DECOMPRESSED, _decompress_limited, inspect_gfx
from gfx_placement_diagnostics import _tag_list


@dataclass(frozen=True)
class VideoStream:
    character_id: int
    frames: int
    width: int
    height: int


def _raw(data):
    inspect_gfx(data)
    if data[:3] == b"CFX":
        return b"GFX" + data[3:8] + _decompress_limited(
            data[8:], struct.unpack_from("<I", data, 4)[0] - 8)
    return data


def _offset(raw):
    nbits = raw[8] >> 3
    return 8 + (5 + 4 * nbits + 7) // 8 + 4


def _tag(code, payload):
    size = len(payload)
    if size < 63:
        return struct.pack("<H", (code << 6) | size) + payload
    return struct.pack("<HI", (code << 6) | 63, size) + payload


def video_streams(data):
    """Return verified native VideoStream definitions, never guessed sprites."""
    raw = _raw(data)
    result = {}
    for code, body in _tag_list(raw, _offset(raw)):
        if code == 60 and len(body) >= 10:
            cid, frames, width, height = struct.unpack_from("<HHHH", body)
            if cid not in result:
                result[cid] = VideoStream(cid, frames, width, height)
    return tuple(result.values())


def _placed_character(code, payload):
    if code == 4 and len(payload) >= 4:
        return struct.unpack_from("<H", payload)[0]
    if code in (26, 70):
        # PlaceObject2 has one flags byte; PlaceObject3 has two.
        flags_size = 1 if code == 26 else 2
        if len(payload) < flags_size + 2:
            return None
        if not (payload[0] & 2) or len(payload) < flags_size + 4:
            return None
        return struct.unpack_from("<H", payload, flags_size + 2)[0]
    return None


def hide_video_placeholders(data):
    """Remove ONLY video placements in a render-only movie, including sprites.

    Keep DefineVideoStream, FrameLabel, ActionScript, sounds, ordinary shapes,
    bitmap matrices, clips, fonts and masks intact. If no video was placed,
    preserve the original movie bytes exactly. Runtime playback is not emulated.
    """
    raw = _raw(data)
    pos = _offset(raw)
    tags = _tag_list(raw, pos)
    stream_ids = {stream.character_id for stream in video_streams(data)}
    if not stream_ids:
        return data, 0

    removed = 0

    def scrub(source):
        nonlocal removed
        chunks = []
        for code, payload in source:
            if _placed_character(code, payload) in stream_ids:
                removed += 1
                continue
            if code == 39 and len(payload) >= 4:
                nested = _tag_list(payload, 4)
                cleaned = scrub(nested)
                payload = payload[:4] + cleaned
            chunks.append(_tag(code, payload))
        return b"".join(chunks)

    body = scrub(tags)
    if not removed:
        return data, 0
    result = bytearray(raw[:pos] + body)
    if len(result) > MAX_DECOMPRESSED:
        raise GfxError("Video-filtered preview exceeds Scaleform size limit.")
    struct.pack_into("<I", result, 4, len(result))
    inspect_gfx(bytes(result))
    return bytes(result), removed
