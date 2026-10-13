"""Read-only tangent-space normal map helpers for Blender exports.

This module intentionally does not parse proprietary archives. Callers supply
already decoded RGBA pixels from their existing DDS decoder.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re


@dataclass(frozen=True)
class NormalMap:
    width: int
    height: int
    rgba: bytes


def is_normal_texture(name: str) -> bool:
    """Recognize CryEngine normal map suffixes, including DDS sidecars."""
    base = Path(name.replace("\\", "/")).name.lower()
    return bool(re.search(r"(?:_ddn|_normal|_nrm)\\.dds(?:\\.\\d+)?$", base))


def decode_xy_normal_rgba(width: int, height: int, rgba: bytes, *,
                          x_channel: int = 0, y_channel: int = 1,
                          invert_green: bool = False) -> NormalMap:
    """Reconstruct Z from packed XY channels; return standard RGBA normals.

    For BC5 pass R/G; for DXT5nm commonly pass A/G, but inspect the
    source material first. Never infer channel mapping from a filename.
    """
    if width <= 0 or height <= 0 or len(rgba) != width * height * 4:
        raise ValueError("Invalid normal map dimensions or pixel count")
    if x_channel not in range(4) or y_channel not in range(4) or x_channel == y_channel:
        raise ValueError("Invalid normal-map channel mapping")
    output = bytearray(len(rgba))
    for offset in range(0, len(rgba), 4):
        x = rgba[offset + x_channel] / 127.5 - 1.0
        y = rgba[offset + y_channel] / 127.5 - 1.0
        if invert_green:
            y = -y
        z = math.sqrt(max(0.0, 1.0 - x*x - y*y))
        length = math.sqrt(x*x + y*y + z*z) or 1.0
        for i, value in enumerate((x / length, y / length, z / length)):
            output[offset+i] = max(0, min(255, round((value + 1.0) * 127.5)))
        output[offset+3] = 255
    return NormalMap(width, height, bytes(output))


def choose_highest_resolution(candidates):
    """Select largest decoded mip by dimensions; reject empty collections."""
    valid = [item for item in candidates if item.width > 0 and item.height > 0
             and len(item.rgba) == item.width * item.height * 4]
    if not valid:
        raise ValueError("No complete decoded normal-map mip")
    return max(valid, key=lambda item: (item.width * item.height, item.width, item.height))
