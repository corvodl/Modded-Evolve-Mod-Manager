"""Read-only Xbox 360 Xenos block-tiled DDS top-mip preview.

This is a *separate* transform for the inspected 2012 prototype's DDS headers,
never a replacement for Windows/Stage2 linear DDS decoding. No source payload
is changed; all conversions run in memory after the verified split stream is
assembled. Smaller console mip tails (<128px) need separate validation and
are intentionally not used for the exported highest-resolution preview.

Xbox GPU block addresses are 32x32 macro-tiled, with 16-bit big-endian words
in compressed surface payloads. This implementation validates a bijection
before using any output pixels. Normal CTX1 is RG, with Z reconstructed.
"""
from __future__ import annotations

from io import BytesIO
import struct

from PIL import Image
from dds_texture import parse_dds

MAX_DIM = 4096
SUPPORTED = {b'DXT1': 8, b'DXT5': 16, b'CTX1': 8}
# Observed 2012 prototype DDS reserved1[0] console surface tags.
SURFACE_TAGS = {0x1800, 0x1040}


def is_2012_console_dds(raw):
    return (
        len(raw) >= 128 and raw[:4] == b'DDS ' and
        raw[84:88] in SUPPORTED and
        struct.unpack_from('<I', raw, 36)[0] in SURFACE_TAGS)


def _tiled_to_xy(offset, blocks_wide, element_bytes):
    """Map a tiled block address onto its linear two-dimensional coordinates."""
    padded = (blocks_wide + 31) & ~31
    bits = element_bytes.bit_length() - 1
    address = offset << bits
    shuffled = ((address & ~4095) >> 3) + ((address & 1792) >> 2) + (address & 63)
    macro = shuffled >> (7 + bits)
    xgroup = ((macro % (padded >> 5)) << 2)
    xtile = ((((shuffled >> (5 + bits)) & 2) + (address >> 6)) & 3)
    xmicro = (((shuffled >> 1) & ~15) + (shuffled & 15)) & ((element_bytes << 3) - 1)
    x = ((xgroup + xtile) << 3) + (xmicro >> bits)
    ygroup = ((macro // (padded >> 5)) << 2)
    ytile = ((shuffled >> (6 + bits)) & 1) + ((address & 2048) >> 10)
    ymicro = ((shuffled & (((element_bytes << 6) - 1) & ~31)) +
              ((shuffled & 15) << 1)) >> (3 + bits)
    y = ((ygroup + ytile) << 3) + (ymicro & ~1) + ((shuffled & 16) >> 4)
    return x, y


def untile_xenos_blocks(payload, width, height, block_bytes):
    """Untile a full sized BC/CTX1 mip, rejecting padding assumptions."""
    if (block_bytes not in (8, 16) or not 128 <= width <= MAX_DIM or
            not 128 <= height <= MAX_DIM or width % 4 or height % 4):
        raise ValueError('Unsupported Xbox 360 block-tile geometry.')
    blocks_wide, blocks_tall = width // 4, height // 4
    count = blocks_wide * blocks_tall
    if len(payload) != count * block_bytes:
        raise ValueError('Xbox 360 highest mip byte count mismatch.')
    out = bytearray(len(payload))
    seen = bytearray(count)
    for tile in range(count):
        x, y = _tiled_to_xy(tile, blocks_wide, block_bytes)
        if x >= blocks_wide or y >= blocks_tall:
            raise ValueError('Xbox 360 tile layout needs missing padding.')
        pos = y * blocks_wide + x
        if seen[pos]:
            raise ValueError('Overlapping Xbox 360 tiled block coordinates.')
        seen[pos] = 1
        start, end = tile * block_bytes, (tile + 1) * block_bytes
        out[pos*block_bytes:(pos+1)*block_bytes] = payload[start:end]
    if not all(seen):
        raise ValueError('Xbox 360 texture tiles do not cover highest mip.')
    # Xbox compressed data uses big-endian 16-bit units throughout each block,
    # including the color/alpha selector words (not just color endpoints).
    for pos in range(0, len(out), 2):
        out[pos], out[pos+1] = out[pos+1], out[pos]
    return bytes(out)


def decode_ctx1_normal(blocks, width, height):
    """RGBA normal preview from Xbox-exclusive two-channel CTX1 blocks."""
    if len(blocks) != (width // 4) * (height // 4) * 8:
        raise ValueError('CTX1 highest mip is incomplete.')
    rgba = bytearray(width * height * 4)
    across = width // 4
    for i in range(0, len(blocks), 8):
        p = i // 8
        bx, by = p % across, p // across
        r0, g0, r1, g1 = blocks[i:i+4]
        selectors, = struct.unpack_from('<I', blocks, i+4)
        options = ((r0, g0), (r1, g1),
                   ((2*r0 + r1 + 1)//3, (2*g0 + g1 + 1)//3),
                   ((r0 + 2*r1 + 1)//3, (g0 + 2*g1 + 1)//3))
        for pixel in range(16):
            x, y = (bx*4 + pixel%4), (by*4 + pixel//4)
            px = (y*width+x)*4
            rgb = options[(selectors >> (pixel*2)) & 3]
            rgba[px:px+4] = bytes((*rgb, 0, 255))
    from normal_map_utils import decode_xy_normal_rgba
    normal = decode_xy_normal_rgba(width, height, bytes(rgba))
    return Image.frombytes('RGBA', (width, height), normal.rgba)


def decode_2012_console_dds(raw, *, normal_map=False):
    """Decode only the highest, fully present and unambiguously console-tiled mip."""
    info = parse_dds(raw)
    fmt = raw[84:88]
    if not is_2012_console_dds(raw):
        raise ValueError('DDS lacks the verified 2012 Xbox 360 surface header.')
    if (info.depth != 1 or info.array_size != 1 or info.caps2
            or info.width > MAX_DIM or info.height > MAX_DIM):
        raise ValueError('Only bounded two-dimensional Xbox 360 DDS is supported.')
    block = SUPPORTED[fmt]
    size = (info.width // 4) * (info.height // 4) * block
    if info.width % 4 or info.height % 4 or len(raw) < info.data_offset + size:
        raise ValueError('Missing the highest-resolution Xbox DDS mip.')
    linear = untile_xenos_blocks(
        raw[info.data_offset:info.data_offset+size],
        info.width, info.height, block)
    if fmt == b'CTX1':
        if not normal_map:
            raise ValueError('CTX1 requires an explicitly identified normal-map slot.')
        return decode_ctx1_normal(linear, info.width, info.height)
    header = bytearray(raw[:info.data_offset])
    # The output image needs only mip 0. Do not append still-tiled smaller mips.
    struct.pack_into('<I', header, 28, 1)
    converted = bytes(header) + linear
    if normal_map:
        from normal_map_utils import decode_normal_dds_image
        return decode_normal_dds_image(converted)
    with Image.open(BytesIO(converted)) as im:
        if im.format != 'DDS' or im.size != (info.width, info.height):
            raise ValueError('Xbox 360 DDS decoder output is invalid.')
        im.load()
        return im.convert('RGBA')
