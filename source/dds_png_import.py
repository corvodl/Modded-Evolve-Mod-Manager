"""Encode a PNG into a format-compatible DDS payload, preserving the source header.

Only block-compressed 2D DDS DXT1/DXT3/DXT5/ATI2(BC5) and matching DX10
BC1/BC2/BC3/BC5 are supported. Existing DDS headers, mip counts, and fragment
layouts are immutable. Pillow encodes each mip; importing is still lossy.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct
from PIL import Image, UnidentifiedImageError
from dds_texture import parse_dds, validate_replacement

SUPPORTED_FOURCC = {
    b'DXT1': ('DXT1', 8),
    b'DXT3': ('DXT3', 16),
    b'DXT5': ('DXT5', 16),
    b'ATI2': ('BC5', 16),
    b'BC5U': ('BC5', 16),
}
SUPPORTED_DXGI = {
    71: ('DXT1', 8), 72: ('DXT1', 8),
    74: ('DXT3', 16), 75: ('DXT3', 16),
    77: ('DXT5', 16), 78: ('DXT5', 16),
    82: ('BC5', 16), 83: ('BC5', 16),
}


def compression_for_dds(original: bytes):
    info = parse_dds(original)
    if info.depth != 1 or info.array_size != 1 or info.caps2:
        raise ValueError('PNG import supports only 2D single-image DDS (no cube/array/volume textures).')
    if info.mipmaps > max(info.width, info.height).bit_length():
        raise ValueError('DDS has an invalid mipmap count.')
    if original[84:88] == b'DX10':
        fmt = struct.unpack_from('<I', original, 128)[0]
        spec = SUPPORTED_DXGI.get(fmt)
    else:
        spec = SUPPORTED_FOURCC.get(original[84:88])
    if spec is None:
        raise ValueError('Automatic PNG import supports DXT1, DXT3, DXT5 and ATI2/BC5 only. Use Import DDS for this texture.')
    if info.mipmaps > 20:
        raise ValueError('DDS mip count exceeds the supported limit.')
    encoder, bytes_per_block = spec
    sizes = []
    for level in range(info.mipmaps):
        w, h = max(1, info.width >> level), max(1, info.height >> level)
        sizes.append(((w + 3) // 4) * ((h + 3) // 4) * bytes_per_block)
    if len(original) != info.data_offset + sum(sizes):
        raise ValueError('DDS includes missing, extra or nonstandard mip payload bytes; automatic PNG import is disabled.')
    return encoder, sizes


def encode_png_as_dds(original: bytes, png_path) -> bytes:
    """Encode all mip levels to match an existing DDS, then validate byte-for-byte layout.

    The caller passes the result to replace_dds/replace_stream for atomic
    replacement and backups; this function never modifies the source project.
    """
    encoder, expected_mip_sizes = compression_for_dds(original)
    info = parse_dds(original)
    path = Path(png_path)
    if not path.is_file() or path.suffix.casefold() != '.png':
        raise ValueError('Select an existing PNG image.')
    try:
        with Image.open(path) as image:
            if image.format != 'PNG':
                raise ValueError('The imported file is not a PNG.')
            if image.size != (info.width, info.height):
                raise ValueError(f'PNG dimensions {image.width} x {image.height} must match the DDS: {info.width} x {info.height}.')
            image.load()
            source = image.convert('RGB' if encoder == 'BC5' else 'RGBA')
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f'Unable to read PNG: {error}') from error
    payloads = []
    for level, expected in enumerate(expected_mip_sizes):
        size = (max(1, info.width >> level), max(1, info.height >> level))
        mip = source if level == 0 else source.resize(size, Image.Resampling.LANCZOS)
        with BytesIO() as out:
            try:
                mip.save(out, format='DDS', pixel_format=encoder)
            except (OSError, NotImplementedError) as error:
                raise ValueError(f'The installed image codec cannot encode {encoder}: {error}') from error
            compressed = out.getvalue()
        # Pillow BC5 currently emits a DX10 encoder header with array_size=0;
        # only use its block payload. Keep the game's known-good DDS header.
        offset = 148 if compressed[84:88] == b'DX10' else 128
        if len(compressed) < offset or int.from_bytes(compressed[12:16], 'little') != size[1] or int.from_bytes(compressed[16:20], 'little') != size[0]:
            raise ValueError('DDS encoder returned an unexpected header or dimensions.')
        block = compressed[offset:]
        if len(block) != expected:
            raise ValueError(f'Encoder produced a different mip layout at level {level}: {len(block)} != {expected}.')
        payloads.append(block)
    result = original[:info.data_offset] + b''.join(payloads)
    validate_replacement(original, result)
    return result
