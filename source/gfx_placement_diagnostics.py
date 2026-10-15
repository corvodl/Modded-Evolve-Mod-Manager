"""Read-only Scaleform external-bitmap and first-frame placement diagnostics.

GFX extension 1009 declares atlas textures. 1008 binds bitmap characters to
texture indexes and pixel regions. Bitmap FillStyles link to DefineShape and
first-frame PlaceObject references indicate possible static placements.
This does not compute compositing or emulate runtime ActionScript callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import struct

from gfx_preview_backend import (GfxError, MAX_TAGS, MAX_DECOMPRESSED,
                                 inspect_gfx, _decompress_limited, _virtual_path)


@dataclass(frozen=True)
class BitmapPlacement:
    bitmap_character: int
    atlas_id: int
    texture: str
    logical_size: tuple[int, int]
    atlas_rectangle: tuple[int, int, int, int]
    shape_ids: tuple[int, ...]
    static_first_frame: bool
    file_found: bool


@dataclass(frozen=True)
class PlacementReport:
    movies: tuple[BitmapPlacement, ...]
    images: int
    shape_placements: int
    first_frame_references: int
    messages: tuple[str, ...]


def _tag_list(raw: bytes, offset: int) -> list[tuple[int, bytes]]:
    result = []
    pos = offset
    while pos + 2 <= len(raw):
        header = struct.unpack_from("<H", raw, pos)[0]
        code = header >> 6
        length = header & 63
        pos += 2
        if length == 63:
            if pos + 4 > len(raw):
                raise GfxError("Truncated Scaleform tag length.")
            length = struct.unpack_from("<I", raw, pos)[0]
            pos += 4
        if length > len(raw) - pos:
            raise GfxError("Truncated Scaleform tag payload.")
        result.append((code, raw[pos:pos + length]))
        if len(result) > MAX_TAGS:
            raise GfxError("Too many Scaleform tags.")
        pos += length
        if code == 0:
            break
    return result


def _root_tags(data: bytes) -> list[tuple[int, bytes]]:
    inspect_gfx(data)  # Validate signature, length, decompression and bounds.
    if data[:3] == b"CFX":
        raw = data[:8] + _decompress_limited(data[8:], MAX_DECOMPRESSED)
    else:
        raw = data
    nbits = raw[8] >> 3
    offset = 8 + (5 + 4 * nbits + 7) // 8 + 4
    return _tag_list(raw, offset)


def _bitmap_fill_symbol(code: int, payload: bytes) -> int | None:
    """Verify a first bitmap FillStyle; do not assume complex fills match."""
    if code not in (2, 22, 32, 83) or len(payload) < 8:
        return None
    pos = 2
    nbits = payload[pos] >> 3
    pos += (5 + 4 * nbits + 7) // 8
    if code == 83:
        if pos >= len(payload):
            return None
        nbits = payload[pos] >> 3
        pos += (5 + 4 * nbits + 7) // 8 + 1
    if pos + 4 > len(payload):
        return None
    count = payload[pos]
    if count != 1 or payload[pos + 1] not in (0x40, 0x41, 0x42, 0x43):
        return None
    return struct.unpack_from("<H", payload, pos + 2)[0]


def _first_frame_symbols(tags: list[tuple[int, bytes]]) -> set[int]:
    """Only placement character references present on frame 1 by depth."""
    depths: dict[int, int] = {}
    for code, payload in tags:
        if code == 1:
            break
        if code == 4 and len(payload) >= 4:
            character, depth = struct.unpack_from("<HH", payload)
            depths[depth] = character
        elif code in (26, 70) and len(payload) >= (3 if code == 26 else 4):
            flags = payload[0]
            index = 1 if code == 26 else 2
            depth = struct.unpack_from("<H", payload, index)[0]
            index += 2
            if flags & 0x02 and index + 2 <= len(payload):
                depths[depth] = struct.unpack_from("<H", payload, index)[0]
        elif code == 5 and len(payload) >= 4:
            depths.pop(struct.unpack_from("<H", payload, 2)[0], None)
        elif code == 28 and len(payload) >= 2:
            depths.pop(struct.unpack_from("<H", payload)[0], None)
    return set(depths.values())


def inspect_bitmap_placement(data: bytes, virtual: str,
                             available_paths=()) -> PlacementReport:
    """Recover verified symbol/atlas links from a selected build's GFX.

    Original movie/texture bytes remain read-only. A first-frame reference
    does not prove visibility; masking, blending, transforms, nested sprite
    state and runtime callbacks can still change what the game displays.
    """
    source = _virtual_path(virtual)
    root = _root_tags(data)
    images: dict[int, tuple[str, tuple[int, int]]] = {}
    tiles: dict[int, tuple[int, tuple[int, int, int, int]]] = {}
    filled_shapes: dict[int, list[int]] = {}
    sprites: dict[int, list[tuple[int, bytes]]] = {}
    shape_definitions = 0
    notes = []
    for code, payload in root:
        if code == 1009:
            if len(payload) < 13:
                continue
            idx, _kind, _format, width, height = struct.unpack_from("<5H", payload)
            length = payload[11]
            if not length or 12 + length + 1 != len(payload) or payload[-1] != 0:
                continue
            try:
                path = payload[12:-1].decode("utf-8")
                virtual_name = _virtual_path(
                    str(PurePosixPath(source).parent / path)
                    if "/" not in path else path)
            except (UnicodeError, ValueError):
                continue
            if not virtual_name.casefold().endswith(".tif"):
                continue
            if idx in images:
                notes.append(f"Duplicate Scaleform image index #{idx}.")
                continue
            images[idx] = (virtual_name[:-4] + ".dds.0", (width, height))
        elif code == 1008 and len(payload) == 12:
            character, idx, x0, y0, x1, y1 = struct.unpack("<6H", payload)
            if character in tiles:
                notes.append(f"Duplicate bitmap character #{character}.")
            else:
                tiles[character] = (idx, (x0, y0, x1, y1))
        elif code in (2, 22, 32, 83) and len(payload) >= 2:
            shape_definitions += 1
            shape = struct.unpack_from("<H", payload)[0]
            bitmap_id = _bitmap_fill_symbol(code, payload)
            if bitmap_id is not None:
                filled_shapes.setdefault(bitmap_id, []).append(shape)
        elif code == 39 and len(payload) >= 4:
            sprite_id = struct.unpack_from("<H", payload)[0]
            if sprite_id not in sprites:
                sprites[sprite_id] = _tag_list(payload, 4)

    reachable = _first_frame_symbols(root)
    checked = set()
    stack = [value for value in reachable if value in sprites]
    while stack:
        current = stack.pop()
        if current in checked:
            continue
        checked.add(current)
        children = _first_frame_symbols(sprites[current])
        reachable.update(children)
        stack.extend(x for x in children if x in sprites and x not in checked)

    available = set()
    for path in available_paths:
        try:
            available.add(_virtual_path(path).casefold())
        except ValueError:
            continue
    bindings = []
    for character, (idx, box) in sorted(tiles.items()):
        image = images.get(idx)
        if image is None:
            notes.append(f"Bitmap character #{character} references missing image #{idx}.")
            continue
        path, logical = image
        x0, y0, x1, y1 = box
        if not (x0 < x1 and y0 < y1 and x1 <= logical[0] and y1 <= logical[1]):
            notes.append(f"Bitmap character #{character} has an invalid atlas rectangle.")
            continue
        found = path.casefold() in available
        if not found and path.casefold().endswith(".dds.0"):
            whole = path[:-2]
            if whole.casefold() in available:
                path, found = whole, True
        shapes = tuple(sorted(set(filled_shapes.get(character, ()))))
        bindings.append(BitmapPlacement(
            character, idx, path, logical, box, shapes,
            any(s in reachable for s in shapes), found))
    if not _first_frame_symbols(root):
        notes.append("No root first-frame placements; sprites may require ActionScript/game callbacks.")
    notes.append("Static references do not prove displayed pixels, transforms or masks.")
    return PlacementReport(tuple(bindings), len(images), shape_definitions,
                           len(reachable), tuple(notes))
