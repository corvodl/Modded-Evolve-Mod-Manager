"""Render-only conversion of verified Scaleform 1008/1009 external DDS bitmaps.

A 1008 record defines an external bitmap character ID and atlas pixel crop.
SWF DefineBitsLossless2 (tag 36) with that same ID makes FFDec rasterize
existing DefineShape bitmap fill matrices, nested sprite placements and masks
instead of guessing positions. Never write the synthetic movie to a game PAK.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib

from PIL import Image

from gfx_placement_diagnostics import inspect_bitmap_placement, _tag_list
from gfx_preview_backend import (GfxError, MAX_DECOMPRESSED, MAX_PIXELS,
                                 _decompress_limited, inspect_gfx)

MAX_BITMAPS = 128
MAX_IMAGE_PIXELS = 2_500_000
MAX_TOTAL_BITMAP_PIXELS = 8_000_000
MAX_CONVERTED_BYTES = MAX_DECOMPRESSED


@dataclass(frozen=True)
class TexturingResult:
    movie: bytes
    converted: int
    skipped: int
    diagnostics: tuple[str, ...]


def _tag(code: int, payload: bytes) -> bytes:
    n = len(payload)
    if n < 63:
        return struct.pack("<H", (code << 6) | n) + payload
    return struct.pack("<HI", (code << 6) | 63, n) + payload


def _header(raw: bytes) -> int:
    nbits = raw[8] >> 3
    return 8 + (5 + 4 * nbits + 7) // 8 + 4


def _lossless2(character: int, image: Image.Image) -> bytes:
    """SWF tag 36, format 5: zlib-compressed *straight* ARGB byte planes."""
    image = image.convert("RGBA")
    w, h = image.size
    if not (1 <= w <= 65535 and 1 <= h <= 65535 and w * h <= MAX_IMAGE_PIXELS):
        raise GfxError("Scaleform bitmap crop exceeds safe raster dimensions.")
    red, green, blue, alpha = image.split()
    argb = Image.merge("RGBA", (alpha, red, green, blue)).tobytes()
    return _tag(36, struct.pack("<HBHH", character, 5, w, h) +
                zlib.compress(argb, 6))


def prepare_textured_gfx(data: bytes, virtual: str, available_paths,
                         texture_loader, *, cancelled=None,
                         max_bitmaps=MAX_BITMAPS) -> TexturingResult:
    """Return a temporary, FFDec-readable GFX with safely resolved bitmaps.

    Only tag 1008 bitmap IDs with valid 1009 atlas and a recognized native
    FillStyle may be converted. Missing/incompatible images are left alone,
    not approximated. The original game bytes and embedded script are never
    modified. The exact DDS loader MUST be scoped to the selected game build.
    """
    info = inspect_gfx(data)
    if texture_loader is None or not (1008 in info.extension_tags and
                                      1009 in info.extension_tags):
        return TexturingResult(data, 0, 0, ())
    if not 1 <= max_bitmaps <= MAX_BITMAPS:
        raise GfxError("Invalid external image render budget.")
    if data[:3] == b"CFX":
        raw = b"GFX" + data[3:8] + _decompress_limited(
            data[8:], struct.unpack_from("<I", data, 4)[0] - 8)
    else:
        raw = data
    offset = _header(raw)
    tags = _tag_list(raw, offset)
    report = inspect_bitmap_placement(data, virtual, available_paths)

    # Resolve a resource for each bitmap character. Multiple clips may
    # reference different pixel rectangles from the same DDS atlas.
    replacements = {}
    diagnostics = []
    decoded_atlases = {}
    total_pixels = 0
    skipped = 0
    # Never overwrite characters that are already defined in the file.
    existing = set(info.images)
    candidates = sorted(report.movies,
                        key=lambda b: (not b.static_first_frame,
                                       not bool(b.shape_ids),
                                       b.bitmap_character))
    for bitmap in candidates:
        if cancelled and cancelled():
            raise InterruptedError("Scaleform texture reconstruction cancelled.")
        if not bitmap.shape_ids or not bitmap.file_found:
            skipped += 1
            continue
        if bitmap.bitmap_character in existing:
            diagnostics.append("Bitmap #" + str(bitmap.bitmap_character) +
                               " conflicts with an embedded movie image.")
            skipped += 1
            continue
        if len(replacements) >= max_bitmaps:
            skipped += 1
            continue
        x0, y0, x1, y1 = bitmap.atlas_rectangle
        count = (x1 - x0) * (y1 - y0)
        if count <= 0 or count > MAX_IMAGE_PIXELS or (
                total_pixels + count > MAX_TOTAL_BITMAP_PIXELS):
            skipped += 1
            continue
        try:
            image = decoded_atlases.get(bitmap.texture.casefold())
            if image is None:
                # Bound memory when browsing UI_Assets: 3 physical atlases
                # at a time, never cache the full build of large DDS files.
                if len(decoded_atlases) >= 3:
                    decoded_atlases.clear()
                image = texture_loader(bitmap.texture)
                if image is None:
                    raise ValueError("No same-build texture")
                # Do not scale or thumbnail before atlas slicing: tag 1008
                # stores pixel coordinates relative to logical top-left.
                if image.width > 8192 or image.height > 8192 or (
                        image.width * image.height > MAX_PIXELS):
                    raise ValueError("Texture exceeds source limits")
                image = image.convert("RGBA")
                decoded_atlases[bitmap.texture.casefold()] = image
            if x1 > image.width or y1 > image.height:
                raise ValueError("Atlas has incomplete/missing high-resolution stream")
            frame = image.crop((x0, y0, x1, y1))
            replacements[bitmap.bitmap_character] = _lossless2(
                bitmap.bitmap_character, frame)
            total_pixels += count
        except (OSError, ValueError, TypeError, MemoryError) as exc:
            skipped += 1
            if len(diagnostics) < 8:
                diagnostics.append(
                    f"Bitmap #{bitmap.bitmap_character}: {str(exc)[:115]}")
    if not replacements:
        if report.movies:
            diagnostics.insert(0, "No verified same-build bitmap fills could be staged.")
        return TexturingResult(data, 0, skipped, tuple(diagnostics))

    # Strip *only* replaced Scaleform 1008 mappings; each is supplanted
    # by a standard SWF bitmap character with the same ID. Keep every
    # DefineShape, PlaceObject/2/3, mask, transform, ActionScript, and
    # unreplaced Scaleform extension in their original order.
    parts = []
    length = offset
    for code, payload in tags:
        if code == 1008 and len(payload) == 12:
            char_id, = struct.unpack_from("<H", payload)
            if char_id in replacements:
                continue
        tag_bytes = _tag(code, payload)
        parts.append((code, tag_bytes))
        length += len(tag_bytes)

    injected = b"".join(replacements.values())
    length += len(injected)
    if length > MAX_CONVERTED_BYTES:
        raise GfxError("Render-only movie with external textures exceeds 32 MiB.")
    # FileAttributes tag must stay first for FFDec. Put definitions after
    # this header, before shapes/timeline tags, so bitmap IDs resolve.
    output = []
    inserted = False
    for code, chunk in parts:
        output.append(chunk)
        if not inserted and code == 69:
            output.append(injected)
            inserted = True
    if not inserted:
        output.insert(0, injected)
    buffer = bytearray(raw[:offset] + b"".join(output))
    struct.pack_into("<I", buffer, 4, len(buffer))
    result = bytes(buffer)
    # The render-only movie may exceed the original game GFX 12 MiB source
    # limit, but it remains within a bounded 32 MiB decompressed buffer.
    if len(result) > MAX_CONVERTED_BYTES:
        raise GfxError("Reconstructed GFX movie exceeds render-only size limit.")
    return TexturingResult(
        result, len(replacements), skipped,
        tuple(diagnostics) + (
            f"Staged {len(replacements)} external bitmap characters "
            f"({total_pixels:,} atlas-crop pixels); {skipped} not staged.",
        ))
