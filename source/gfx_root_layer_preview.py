"""Preview-only isolation of *root* Scaleform display-list placements.

Layer isolation is a diagnostic for static root timeline content, not a
replacement for a real Evolve ActionScript/runtime UI. Never mutate the
source movie, edit session or the installed game's PAK.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from gfx_preview_backend import GfxError, inspect_gfx
from gfx_layout_backend import _raw, _records, _start

MAX_ROOT_LAYERS = 512


@dataclass(frozen=True)
class RootLayer:
    depth: int
    character: int
    label: str
    kind: str


def _target(code: int, payload: bytes):
    """Return the root display-list depth and replacement character, if any.

    None for tags that do not affect a root instance. Character None means a
    MoveObject update with no new character or a RemoveObject.
    """
    if code == 4 and len(payload) >= 4:
        character, depth = struct.unpack_from("<HH", payload)
        return depth, character
    if code == 26 and len(payload) >= 3:
        flags, = payload[:1]
        depth = struct.unpack_from("<H", payload, 1)[0]
        if flags & 0x02:
            if len(payload) < 5:
                raise GfxError("Truncated root PlaceObject2 character.")
            return depth, struct.unpack_from("<H", payload, 3)[0]
        return depth, None
    if code == 70 and len(payload) >= 4:
        flags = payload[0]
        depth = struct.unpack_from("<H", payload, 2)[0]
        if flags & 0x02:
            # PlaceObject3 can have ClassName/FilterList; the character ID
            # occurs after depth and optional class name. Isolation only
            # needs depth; use None if the character is not unambiguous.
            if payload[1] & 0x08:
                return depth, None
            if len(payload) < 6:
                raise GfxError("Truncated root PlaceObject3 character.")
            return depth, struct.unpack_from("<H", payload, 4)[0]
        return depth, None
    if code == 5 and len(payload) >= 4:
        return struct.unpack_from("<H", payload, 2)[0], None
    if code == 28 and len(payload) >= 2:
        return struct.unpack_from("<H", payload)[0], None
    return None


def root_layers(data: bytes, *, frame: int = 1) -> tuple[RootLayer, ...]:
    """List actual root display-list instances at the start of a frame.

    A timeline can reuse the same depth in later frames. The selector
    identifies a depth, not a particular recurring frame/ActionScript state.
    """
    info = inspect_gfx(data)
    if not isinstance(frame, int) or not 1 <= frame <= info.frames:
        raise GfxError("Root frame is outside the movie timeline.")
    raw = _raw(data)
    _, records = _records(raw)
    visible: dict[int, int] = {}
    index = 1
    for code, payload, _header in records:
        if index > frame:
            break
        if code == 1:
            index += 1
            continue
        if code not in (4, 5, 26, 28, 70):
            continue
        target = _target(code, payload)
        if target is None:
            continue
        depth, character = target
        if code in (5, 28):
            visible.pop(depth, None)
        elif character is not None:
            visible[depth] = character
    exports = dict(info.exports)
    sprites = dict(info.sprites)
    kinds = set(info.images)
    result = []
    for depth, character in sorted(visible.items()):
        kind = ("sprite" if character in sprites else
                "image" if character in kinds else "shape / other")
        result.append(RootLayer(depth, character,
                                exports.get(character) or f"Character #{character}",
                                kind))
        if len(result) > MAX_ROOT_LAYERS:
            raise GfxError("More root layers than the safe diagnostic limit.")
    return tuple(result)


def isolate_root_depth(data: bytes, *, mode: str = "all",
                       depth: int | None = None, frame: int = 1) -> bytes:
    """Create a temporary GFX filtered by root depth for FFDec rendering.

    Applies hide/isolate to root placement and removal tags in every frame,
    preserving definition tags, ActionScript and nested sprite tags exactly.
    This is preview-only, and must NEVER be passed to project staging.
    """
    if mode == "all":
        return data
    if mode not in ("hide", "isolate"):
        raise GfxError("Unknown root layer visibility mode.")
    if isinstance(depth, bool) or not isinstance(depth, int):
        raise GfxError("Choose an existing root timeline depth.")
    known = {layer.depth for layer in root_layers(data, frame=frame)}
    if depth not in known:
        raise GfxError("Selected root layer no longer exists in this frame.")
    raw = _raw(data)
    offset, records = _records(raw)
    output = []
    filtered = 0
    for code, payload, prefix in records:
        target = _target(code, payload) if code in (4, 5, 26, 28, 70) else None
        if target is not None:
            layer_depth, _ = target
            remove = (layer_depth == depth if mode == "hide"
                      else layer_depth != depth)
            if remove:
                filtered += 1
                continue
        output.append(prefix + payload)
    if not filtered:
        raise GfxError("Selected filter did not match any root timeline placement.")
    movie = bytearray(raw[:offset] + b"".join(output))
    struct.pack_into("<I", movie, 4, len(movie))
    # Root filters are render-only copies. The original is never altered.
    result = bytes(movie)
    inspect_gfx(result)
    return result
