"""Decode CryEngine's authored DDNA gloss alpha sidecar, without altering DDS RGB.

In Stage 2 a *_ddna.dds.0 ATI2/BC5 normal stream has a matching .0a
ATI1/BC4 gloss stream, with mip payloads .1a, .2a, ... .Na. These are
independent compressed textures; the latter is not diffuse alpha or glow.
Only exact same-virtual-path sidecars are associated. No guessed colors.
"""
from __future__ import annotations

from pathlib import Path
import re

from dds_streaming import _mip_sizes
from dds_texture import parse_dds
from multi_pak_assets import _resolve


_DDNA = re.compile(r'(?i)_ddna\.dds\.0$')


def decode_ddna_gloss(normal_record, *, index=None, max_dimension=16384):
    """Read the highest-resolution BC4 gloss sidecar for a verified DDNA normal.

    Return None when no authored gloss sidecar exists; raise ValueError on a
    present but malformed/incomplete stream instead of silently substituting
    the lowest mip. Source PAK files are read-only.
    """
    relative = str(normal_record['relative']).replace('\\', '/')
    if not _DDNA.search(relative):
        return None
    index = index or {}
    workspace = normal_record.get('workspace')

    def exact_part(suffix):
        virtual = relative[:-1] + suffix  # *.dds.0 -> *.dds.0a / 1a / 2a
        hits, status = _resolve(index, virtual, preferred_workspace=workspace)
        if status == 'ambiguous':
            raise ValueError('Ambiguous DDNA gloss sidecar: ' + virtual)
        if status == 'found':
            record = hits[0]
            path = Path(record['file'])
        else:
            path = Path(normal_record['file']).with_name(Path(virtual).name)
            if not path.is_file():
                return None
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe DDNA gloss sidecar: ' + virtual)
        return path

    first = exact_part('0a')
    if first is None:
        return None
    if first.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('Oversized DDNA gloss header.')
    # CryEngine strips the four-character DDS magic from its .0a sidecar.
    tail = first.read_bytes()
    if tail[:4] == b'DDS ':
        raise ValueError('Unexpected standalone DDS signature in DDNA .0a.')
    header = b'DDS ' + tail
    if len(header) < 128 or header[84:88] not in (b'ATI1', b'BC4U'):
        raise ValueError('DDNA gloss sidecar must be an ATI1/BC4 mip stream.')
    info = parse_dds(header)
    normal_header = Path(normal_record['file']).read_bytes()[:148]
    original = parse_dds(normal_header)
    if (info.width, info.height, info.mipmaps) != (
            original.width, original.height, original.mipmaps):
        raise ValueError('DDNA gloss dimensions or mip counts differ from normal map.')
    if info.width * info.height > 100_000_000:
        raise ValueError('DDNA gloss exceeds the decoding pixel budget.')
    sizes = _mip_sizes(info, header)
    remaining = len(header) - info.data_offset
    tail_options = [n for n in range(1, info.mipmaps)
                    if sum(sizes[-n:]) == remaining]
    if len(tail_options) != 1:
        raise ValueError('Invalid DDNA .0a smallest-mip tail.')
    tail_count = tail_options[0]
    part_count = info.mipmaps - tail_count + 1
    if part_count > 20:
        raise ValueError('Too many DDNA gloss parts.')
    chunks = []
    for number in range(1, part_count):
        path = exact_part(str(number) + 'a')
        if path is None:
            raise ValueError('Incomplete DDNA gloss stream: missing .' +
                             str(number) + 'a for ' + relative)
        expected = sizes[-tail_count-number]
        if path.stat().st_size != expected:
            raise ValueError('DDNA gloss mip size mismatch: ' + path.name)
        chunks.append(path.read_bytes())
    raw = header[:info.data_offset] + b''.join(reversed(chunks)) + header[info.data_offset:]
    if len(raw) != info.data_offset + sum(sizes):
        raise ValueError('Invalid reconstructed DDNA gloss byte count.')
    # The shared texture decoder handles BC4, resolution bounds and caching.
    from material_preview import _decode_texture_pixels
    return _decode_texture_pixels(raw, relative + 'a',
                                  max_dimension=max_dimension).getchannel('R')
