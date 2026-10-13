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
    return bool(re.search(r"(?:_ddn|_normal|_nrm)\.dds(?:\.\d+)?$", base))


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


def encode_normal_png(normal: NormalMap) -> bytes:
    """Encode an 8-bit RGBA PNG without Pillow or platform dependencies."""
    import struct
    import zlib

    if normal.width <= 0 or normal.height <= 0:
        raise ValueError("Invalid image dimensions")
    if len(normal.rgba) != normal.width * normal.height * 4:
        raise ValueError("Incomplete RGBA image")
    stride = normal.width * 4
    scanlines = b"".join(
        b"\x00" + normal.rgba[row * stride:(row + 1) * stride]
        for row in range(normal.height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

    header = struct.pack(">IIBBBBB", normal.width, normal.height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(scanlines))
            + chunk(b"IEND", b""))


def export_normal_png(normal: NormalMap, output_dir: Path, stem: str) -> Path:
    """Write a normal map only within a dedicated output textures directory.

    The caller chooses the destination project; this function never touches
    source DDS data or game archives.
    """
    if not stem or stem in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_-]+", stem):
        raise ValueError("Unsafe texture name")
    target = Path(output_dir) / "textures" / (stem + "_normal.png")
    payload = encode_normal_png(normal)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


@dataclass(frozen=True)
class DDSMetadata:
    width: int
    height: int
    mip_count: int
    format_name: str
    x_channel: int | None
    y_channel: int | None


def inspect_dds_header(data: bytes) -> DDSMetadata:
    """Inspect standard DDS and DX10 headers without decoding compressed pixels.

    Reject truncated/unsupported headers rather than guessing normal channels.
    BC5 maps to RG; legacy DXT5 needs material evidence before swizzling.
    """
    import struct

    if len(data) < 128 or data[:4] != b"DDS ":
        raise ValueError("Missing DDS header")
    size, flags, height, width = struct.unpack_from("<4I", data, 4)
    mip_count = struct.unpack_from("<I", data, 28)[0] or 1
    pixel_format_size = struct.unpack_from("<I", data, 76)[0]
    fourcc = data[84:88]
    if size != 124 or pixel_format_size != 32 or not (0 < width <= 32768 and 0 < height <= 32768):
        raise ValueError("Malformed DDS header")
    mapping = {b"ATI2": ("BC5", 0, 1), b"BC5U": ("BC5", 0, 1),
               b"DXT5": ("DXT5", None, None), b"DXT1": ("DXT1", None, None)}
    if fourcc == b"DX10":
        if len(data) < 148:
            raise ValueError("Truncated DDS DX10 header")
        dxgi = struct.unpack_from("<I", data, 128)[0]
        dxgi_formats = {83: ("BC5", 0, 1), 84: ("BC5_SNORM", 0, 1),
                        77: ("BC3", None, None), 78: ("BC3_SRGB", None, None)}
        if dxgi not in dxgi_formats:
            raise ValueError(f"Unsupported DDS DXGI format: {dxgi}")
        fmt, x_channel, y_channel = dxgi_formats[dxgi]
    elif fourcc in mapping:
        fmt, x_channel, y_channel = mapping[fourcc]
    else:
        raise ValueError("Unsupported DDS pixel format")
    return DDSMetadata(width, height, mip_count, fmt, x_channel, y_channel)
