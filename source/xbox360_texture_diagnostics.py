"""Read-only 2012 Xenos DDS fragment -> decoded PNG diagnostic.

Run with --model an exact original CryTek v0x745 .chr plus .dds.0..N fragment
paths (these may be in different extracted PAKs). An output outside the
original Asset Library is mandatory. No game payloads are bundled or mutated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from dds_streaming import _mip_sizes
from dds_texture import parse_dds
from xbox360_dds import decode_2012_console_dds, is_2012_console_dds

PART = re.compile(r'(?i)^(.*\.dds)\.(\d+)$')
MAX_PART = 20
MAX_FRAGMENT = 64 * 1024 * 1024


def decode_fragment_files(model, fragments, output, *, normal_map=False):
    model = Path(model).resolve(strict=True)
    if not model.is_file() or model.is_symlink():
        raise ValueError('The historical model must be an existing regular file.')
    with model.open('rb') as fh:
        header = fh.read(16)
    if (len(header) < 16 or header[:8] != b'CryTek\0\0'
            or int.from_bytes(header[12:16], 'little') != 0x745):
        raise ValueError('Selected model does not use historical CryTek 0x745.')
    if not 2 <= len(fragments) <= MAX_PART:
        raise ValueError('Select exactly .dds.0 through .dds.N (N>=1).')
    rows = {}
    base = None
    for name in fragments:
        file = Path(name)
        if not file.is_file() or file.is_symlink() or file.stat().st_size > MAX_FRAGMENT:
            raise ValueError('Unsafe, missing or oversized texture fragment: '+str(file))
        match = PART.fullmatch(file.name)
        if not match:
            raise ValueError('Expected a numbered .dds.N filename.')
        stem, ordinal = match.group(1).casefold(), int(match.group(2))
        if ordinal >= MAX_PART or ordinal in rows or (base is not None and stem != base):
            raise ValueError('Texture fragments must share one name and unique indices.')
        base = stem
        raw = file.read_bytes()
        rows[ordinal] = (file.resolve(), raw)
    if set(rows) != set(range(len(rows))):
        raise ValueError('Missing or noncontiguous DDS mip fragments.')
    first = rows[0][1]
    if not is_2012_console_dds(first):
        raise ValueError('Missing verified 2012 Xenos DDS surface marker.')
    info = parse_dds(first)
    sizes = _mip_sizes(info, first)
    tail = [n for n in range(1, info.mipmaps) if
            sum(sizes[-n:]) == len(first)-info.data_offset]
    if len(tail) != 1 or len(rows) != info.mipmaps-tail[0]+1:
        raise ValueError('Incomplete or unrecognized Xbox DDS streaming mip layout.')
    for n in range(1,len(rows)):
        if len(rows[n][1]) != sizes[-tail[0]-n]:
            raise ValueError('DDS streaming mip size mismatch: '+str(rows[n][0]))
    combined = (first[:info.data_offset] +
                b''.join(rows[i][1] for i in range(len(rows)-1,0,-1)) +
                first[info.data_offset:])
    result = decode_2012_console_dds(combined, normal_map=normal_map)
    output = Path(output).absolute()
    if output.suffix.casefold() != '.png' or output.exists() or output.is_symlink():
        raise ValueError('Choose a NEW output PNG filename.')
    # Prevent writing into any source import folder, including under its
    # parent. This tool never changes original textures even by accident.
    for source in [model, *(file for file,_ in rows.values())]:
        if output.resolve(strict=False).is_relative_to(source.parent):
            raise ValueError('Decoded PNG must be saved outside extracted source folders.')
    if not output.parent.is_dir():
        raise FileNotFoundError('Create the destination PNG directory first.')
    with output.open('xb') as fh:
        result.save(fh, format='PNG')
    return {'source_model':hashlib.sha256(model.read_bytes()).hexdigest(),
            'fragments':[{'file':file.name,
                          'sha256':hashlib.sha256(data).hexdigest()}
                         for file,data in rows.values()],
            'size':result.size,
            'kind':'normal' if normal_map else 'diffuse',
            'status':'read-only preview; compare against original game screenshot',
            'output':str(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--fragment',type=Path,action='append',required=True,
                        help='Repeat once per .dds.N fragment from each extracted PAK.')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--normal',action='store_true',
                        help='Reconstruct XYZ from CTX1 RG data')
    args=parser.parse_args(argv)
    try:
        report=decode_fragment_files(args.model,args.fragment,args.output,
                                     normal_map=args.normal)
        print(json.dumps(report,indent=2))
        return 0
    except (ValueError,OSError) as exc:
        print('2012 texture preview failed:',exc,file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
