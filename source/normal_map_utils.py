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
               b"DXT5": ("DXT5", None, None), b"DXT1": ("DXT1", None, None),
               b"BC5S": ("BC5_SNORM", 0, 1)}
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


def _decode_bc4_block(block: bytes) -> tuple[int, ...]:
    """Decode a single 4x4 unsigned BC4 block in DDS pixel order."""
    if len(block) != 8:
        raise ValueError("Incomplete BC4 block")
    a, b = block[0], block[1]
    if a > b:
        palette = (a, b) + tuple(((7 - i) * a + i * b) // 7 for i in range(1, 7))
    else:
        palette = (a, b) + tuple(((5 - i) * a + i * b) // 5 for i in range(1, 5)) + (0, 255)
    packed = int.from_bytes(block[2:], "little")
    return tuple(palette[(packed >> (3 * i)) & 7] for i in range(16))


def _decode_bc4_signed_block(block: bytes) -> tuple[int, ...]:
    """BC4_SNORM endpoints are signed, unlike BC4_UNORM."""
    if len(block) != 8:
        raise ValueError("Incomplete signed BC4 block")
    a = max(-127, int.from_bytes(block[:1], "little", signed=True))
    b = max(-127, int.from_bytes(block[1:2], "little", signed=True))
    if a > b:
        palette = (a, b) + tuple(int(((7-i)*a+i*b)/7) for i in range(1, 7))
    else:
        palette = (a, b) + tuple(int(((5-i)*a+i*b)/5) for i in range(1, 5)) + (-127, 127)
    bits = int.from_bytes(block[2:], "little")
    return tuple(palette[(bits >> (3*i)) & 7] for i in range(16))


def decode_bc5_normal(width: int, height: int, blocks: bytes, *,
                      invert_green: bool = False, signed: bool = False) -> NormalMap:
    """Decode unsigned BC5 blocks into tangent-space RGBA normals.

    Expects only compressed mip payload, not DDS headers or stream sidecars.
    BC5_SNORM requires signed endpoint interpolation. Partial edge blocks are cropped to the image dimensions.
    """
    if width <= 0 or height <= 0 or width > 32768 or height > 32768:
        raise ValueError("Invalid BC5 dimensions")
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    if len(blocks) != blocks_x * blocks_y * 16:
        raise ValueError("Incomplete BC5 mip payload")
    rgba = bytearray(width * height * 4)
    for by in range(blocks_y):
        for bx in range(blocks_x):
            offset = (by * blocks_x + bx) * 16
            decoder = _decode_bc4_signed_block if signed else _decode_bc4_block
            red = decoder(blocks[offset:offset + 8])
            green = decoder(blocks[offset + 8:offset + 16])
            for iy in range(4):
                for ix in range(4):
                    px, py = bx * 4 + ix, by * 4 + iy
                    if px >= width or py >= height:
                        continue
                    source = iy * 4 + ix
                    target = (py * width + px) * 4
                    red8 = round((red[source]/127+1)*127.5) if signed else red[source]
                    green8 = round((green[source]/127+1)*127.5) if signed else green[source]
                    rgba[target:target + 4] = bytes((red8, green8, 0, 255))
    return decode_xy_normal_rgba(width, height, bytes(rgba),
                                 invert_green=invert_green)


def decode_bc5_dds(data: bytes, *, invert_green: bool = False) -> NormalMap:
    """Decode first complete unsigned BC5 mip in a standard DDS file.

    CryEngine split DDS streaming must be assembled by the existing archive
    resolver first. A truncated highest mip raises rather than returning a
    misleading low-resolution texture.
    """
    meta = inspect_dds_header(data)
    if meta.format_name != "BC5":
        raise ValueError("DDS is not unsigned BC5")
    header_size = 148 if data[84:88] == b"DX10" else 128
    size = ((meta.width + 3) // 4) * ((meta.height + 3) // 4) * 16
    if len(data) < header_size + size:
        raise ValueError("Missing highest-resolution BC5 mip data")
    return decode_bc5_normal(meta.width, meta.height,
                             data[header_size:header_size + size],
                             invert_green=invert_green)


def _decode_dxt5nm_normal(width: int, height: int, blocks: bytes,
                           *, invert_green: bool = False) -> NormalMap:
    """Decode BC3/DXT5nm alpha-as-X and green-as-Y without Pillow swizzle loss."""
    import struct
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    if width <= 0 or height <= 0 or len(blocks) != blocks_x * blocks_y * 16:
        raise ValueError("Incomplete DXT5nm highest mip payload")
    packed = bytearray(width * height * 4)
    for by in range(blocks_y):
        for bx in range(blocks_x):
            chunk = blocks[(by * blocks_x + bx)*16: (by * blocks_x + bx + 1)*16]
            xs = _decode_bc4_block(chunk[:8])
            c0, c1 = struct.unpack_from("<HH", chunk, 8)
            g0 = ((c0 >> 5) & 63) * 255 // 63
            g1 = ((c1 >> 5) & 63) * 255 // 63
            greens = (g0, g1, (2*g0 + g1)//3, (g0 + 2*g1)//3)
            indices = int.from_bytes(chunk[12:16], "little")
            for iy in range(4):
                for ix in range(4):
                    x, y = bx*4 + ix, by*4 + iy
                    if x >= width or y >= height:
                        continue
                    p = iy*4 + ix
                    off = (y*width + x)*4
                    packed[off:off+4] = bytes((xs[p], greens[(indices >> (2*p)) & 3], 0, 255))
    return decode_xy_normal_rgba(width, height, bytes(packed),
                                 invert_green=invert_green)


def decode_normal_dds_image(data: bytes, *, source_name: str = "",
                            invert_green: bool = False):
    """Decode a complete, highest-mip DDS to glTF-ready tangent-space RGB.

    Material slots are explicit evidence that these pixels represent normals.
    Standard BC5 uses R/G; CryEngine DXT5nm uses alpha/green. Uncompressed
    already-encoded RGB normal textures are preserved rather than re-swizzled.
    Unsupported signed/unknown compressed variants fail explicitly.
    """
    from io import BytesIO
    from PIL import Image

    try:
        info = inspect_dds_header(data)
    except ValueError:
        # Uncompressed legacy DDS is independently decoded by Pillow, whose
        # RGB channel masks may differ from compressed DDS fourCC metadata.
        from dds_texture import parse_dds
        parsed = parse_dds(data)
        if not parsed.pixel_format.endswith("RGB/luminance"):
            raise
        info = None
    if info is not None and info.format_name in ("BC5", "BC5_SNORM"):
        offset = 148 if data[84:88] == b"DX10" else 128
        count = ((info.width+3)//4)*((info.height+3)//4)*16
        if len(data) < offset + count:
            raise ValueError("Missing highest-resolution BC5 mip data")
        normal = decode_bc5_normal(info.width, info.height,
                                   data[offset:offset+count],
                                   signed=(info.format_name == "BC5_SNORM"),
                                   invert_green=invert_green)
        return Image.frombytes("RGBA", (normal.width, normal.height), normal.rgba)
    if info is not None and info.format_name in ("DXT5", "BC3", "BC3_SRGB"):
        off = 148 if data[84:88] == b"DX10" else 128
        expected = ((info.width + 3)//4)*((info.height + 3)//4)*16
        if len(data) < off + expected:
            raise ValueError("Missing highest-resolution DXT5nm mip data")
        # Called only for a normal material slot, not arbitrary diffuse DXT5.
        decoded = _decode_dxt5nm_normal(info.width, info.height,
                                         data[off:off+expected],
                                         invert_green=invert_green)
        return Image.frombytes("RGBA", (decoded.width, decoded.height), decoded.rgba)
    with Image.open(BytesIO(data)) as im:
        if im.format != "DDS":
            raise ValueError("Unsupported DDS normal image")
        im.load()
        return im.convert("RGB")
