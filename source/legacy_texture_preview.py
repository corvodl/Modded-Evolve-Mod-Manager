"""Preview legacy 2012 CryEngine DDS streams without altering archive members.

Standard streams use .dds.0 ... .dds.N; 8-bit auxiliary planes use .dds.0a
... .dds.Na and omit the four-byte 'DDS ' magic. Unsupported formats stay
accessible as raw files, not silently converted into damaged textures.
"""
from __future__ import annotations
from io import BytesIO
import re

from PIL import Image
from dds_texture import parse_dds, preview_dds
from dds_streaming import BLOCK_BYTES, DXGI_BLOCK_BYTES
from legacy_pak_workspace import entries_by_name, read_member

PART_RE = re.compile(r"(?i)^(.+\.dds)\.(\d+)(a?)$")
MAX_MIP_PARTS = 20


def _mip_sizes(header, *, aux=False):
    if not aux:
        info = parse_dds(header)
        w, h, count, offset = info.width, info.height, info.mipmaps, info.data_offset
        if info.depth != 1 or info.array_size != 1 or info.caps2:
            raise ValueError("Only ordinary 2D legacy textures are previewable.")
    else:
        if len(header) < 124 or header[:4] != b"\x7c\x00\x00\x00":
            raise ValueError("Unsupported legacy auxiliary DDS header.")
        info = parse_dds(b"DDS " + header)
        w, h, count, offset = info.width, info.height, info.mipmaps, 124
    if count < 1 or count > 20 or w * h > 100_000_000:
        raise ValueError("Unsupported legacy texture dimensions/mipmaps.")
    dds_header = b"DDS " + header if aux else header
    fourcc = dds_header[84:88]
    if fourcc == b"DX10":
        block = DXGI_BLOCK_BYTES.get(int.from_bytes(dds_header[128:132], "little"))
    else:
        block = BLOCK_BYTES.get(fourcc)
    if fourcc == b"CTX1":
        block = 8  # Reconstructable, but the preview codec does not support CTX1.
    bits = int.from_bytes(dds_header[88:92], "little")
    if not block and bits not in (8, 32):
        raise ValueError("Legacy texture compression is unsupported for preview.")
    sizes = []
    for level in range(count):
        mw, mh = max(1, w >> level), max(1, h >> level)
        sizes.append((((mw + 3) // 4) * ((mh + 3) // 4) * block)
                     if block else mw * mh * (bits // 8))
    return info, sizes, offset, fourcc


def decode_legacy_texture(project, relative):
    """Return (Pillow image, description); raise for malformed/unknown textures."""
    match = PART_RE.fullmatch(relative)
    index = entries_by_name(project)
    if not match and not relative.casefold().endswith(".dds"):
        raise ValueError("Select a DDS texture or its first streaming fragment.")
    if not match:
        raw = read_member(project, relative, max_bytes=200_000_000)
        image = preview_dds(raw, max_size=(900, 700))
        info = parse_dds(raw)
        return image, info.description
    root, part_no, aux_letter = match.groups()
    if int(part_no) != 0:
        relative = root + ".0" + aux_letter
        if relative not in index:
            raise ValueError("Missing first streaming fragment.")
    auxiliary = bool(aux_letter)
    first = read_member(project, relative, max_bytes=32 * 1024 * 1024)
    info, sizes, offset, fourcc = _mip_sizes(first, aux=auxiliary)
    parts = {}
    for number in range(MAX_MIP_PARTS):
        name = root + "." + str(number) + aux_letter
        if name not in index:
            break
        parts[number] = read_member(project, name, max_bytes=200_000_000)
    if sorted(parts) != list(range(len(parts))):
        raise ValueError("Missing or out-of-order texture fragments.")
    tail = len(first) - offset
    tail_matches = [n for n in range(1, len(sizes) + 1)
                    if sum(sizes[-n:]) == tail]
    if len(tail_matches) != 1:
        if len(parts) == 1 and len(first) == offset + sum(sizes):
            tail_matches = [len(sizes)]
        else:
            raise ValueError("Legacy texture tail does not match declared mip layout.")
    tail_count = tail_matches[0]
    if len(parts) != len(sizes) - tail_count + 1:
        raise ValueError("Incomplete legacy DDS stream; extract its sibling fragments.")
    for part_no in range(1, len(parts)):
        expected = sizes[-tail_count - part_no]
        if len(parts[part_no]) != expected:
            raise ValueError("Legacy DDS fragment has an unexpected byte count.")
    highest_to_smallest = (b"".join(parts[n] for n in range(len(parts)-1, 0, -1))
                          + first[offset:])
    if len(highest_to_smallest) != sum(sizes):
        raise ValueError("Combined DDS mip payload has an invalid length.")
    if auxiliary:
        image = Image.frombytes("L", (info.width, info.height),
                                highest_to_smallest[:sizes[0]])
    else:
        if fourcc == b"CTX1":
            raise ValueError("CTX1 is preserved for editing/export, but preview decoding needs a CTX1 codec.")
        raw = first[:offset] + highest_to_smallest
        image = preview_dds(raw, max_size=(900, 700))
    image.thumbnail((900, 700), Image.Resampling.LANCZOS)
    kind = "auxiliary 8-bit plane" if auxiliary else info.pixel_format
    return image, f"{info.width} × {info.height} | {kind} | {info.mipmaps} mips"


def png_for_legacy_texture(project, relative):
    image, _ = decode_legacy_texture(project, relative)
    with BytesIO() as output:
        image.convert("RGBA").save(output, format="PNG")
        return output.getvalue()
