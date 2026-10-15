"""Read-only six-face CryEngine DDS cubemaps, including validated mip streams.

Supports known BC1/2/3/5 layouts only. Full-resolution mip chains are kept
face-by-face, and cubemap face order is +X,-X,+Y,-Y,+Z,-Z. Ambiguous layouts
are rejected instead of guessed. Game files are never mutated.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct

from PIL import Image
from dds_texture import parse_dds

FACES = ('Positive X','Negative X','Positive Y','Negative Y','Positive Z','Negative Z')
MAX_CUBE_PIXELS = 6 * 4096 * 4096


def _sizes(header):
    info = parse_dds(header)
    if info.depth != 1 or info.array_size != 1:
        raise ValueError('Cubemap volume/array variants are unsupported.')
    if info.width != info.height or info.width * info.height * 6 > MAX_CUBE_PIXELS:
        raise ValueError('Cubemap face dimensions exceed verified safety limits.')
    if header[84:88] == b'DX10':
        if struct.unpack_from('<I',header,136)[0] & 0x4 == 0:
            raise ValueError('DDS DX10 header is not marked as a cubemap.')
        from dds_streaming import DXGI_BLOCK_BYTES
        block=DXGI_BLOCK_BYTES.get(struct.unpack_from('<I',header,128)[0])
    else:
        if info.caps2 & 0xfe00 != 0xfe00:
            raise ValueError('DDS does not declare all six cubemap faces.')
        block={b'DXT1':8,b'DXT3':16,b'DXT5':16,b'ATI2':16,
               b'BC5U':16}.get(header[84:88])
    if not block:
        raise ValueError('Unsupported cubemap compression.')
    if info.mipmaps > 16 or info.mipmaps > info.width.bit_length():
        raise ValueError('Invalid cubemap mip chain.')
    sizes=[max(1,(max(1,info.width >> i)+3)//4) *
           max(1,(max(1,info.height >> i)+3)//4) * block
           for i in range(info.mipmaps)]
    return info, sizes


def assemble_cubemap_stream(part0: bytes, siblings: dict[int, bytes]) -> bytes:
    """Reconstruct per-face mip chains from six-face-per-mip split parts.

    .0 contains the equal-length low-mip tail for all six faces in face
    order. .1, .2... contain each larger mip for all six faces. Any missing
    level, ambiguous tail length or inconsistent face size is rejected.
    """
    info, sizes = _sizes(part0)
    length=len(part0)-info.data_offset
    possible=[n for n in range(1,info.mipmaps)
              if length == 6 * sum(sizes[-n:])]
    if len(possible)!=1:
        raise ValueError('No unique six-face cubemap mip tail.')
    tail=possible[0]
    needed=info.mipmaps-tail
    if set(siblings)!=set(range(1,needed+1)):
        raise ValueError('Missing or unexpected cubemap DDS stream parts.')
    for number,data in siblings.items():
        if len(data) != 6 * sizes[-tail-number]:
            raise ValueError('Cubemap stream part has invalid face-size layout.')
    payload=bytearray()
    tail_size=sum(sizes[-tail:])
    for face in range(6):
        for number in range(needed,0,-1):
            stride=sizes[-tail-number]
            payload.extend(siblings[number][face*stride:(face+1)*stride])
        payload.extend(part0[info.data_offset+face*tail_size:
                             info.data_offset+(face+1)*tail_size])
    return part0[:info.data_offset]+bytes(payload)


def decode_cubemap_dds(raw: bytes):
    """Return six top-resolution PIL RGB images from a complete cubemap."""
    info,sizes=_sizes(raw)
    face_bytes=sum(sizes)
    if len(raw)!=info.data_offset+6*face_bytes:
        raise ValueError('Missing or trailing cubemap face/mip data.')
    faces=[]
    for i in range(6):
        # An isolated face uses the original verified codec and top mip,
        # with only the cube/array descriptor and mip count cleared.
        header=bytearray(raw[:info.data_offset])
        struct.pack_into('<I',header,112,0)
        struct.pack_into('<I',header,28,1)
        if info.data_offset == 148:
            struct.pack_into('<I',header,136,0)
        offset=info.data_offset+i*face_bytes
        with Image.open(BytesIO(bytes(header)+raw[offset:offset+sizes[0]])) as im:
            if im.format!='DDS' or im.size!=(info.width,info.height):
                raise ValueError('Cubemap face decoder size mismatch.')
            im.load()
            faces.append(im.convert('RGB'))
    return tuple(faces)
