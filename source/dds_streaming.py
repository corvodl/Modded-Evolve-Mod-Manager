"""Conservative CryEngine streaming DDS reconstruction and staged replacement.

Only supports linear BCn/ATI block-compressed texture mip streams with the
CryEngine .dds.0 tail-header layout. Never modifies installed game files.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

from dds_texture import parse_dds, preview_dds, validate_replacement

PART = re.compile(r'(?i)^(.*\.dds)\.(\d+)$')
MAX_PARTS = 20
BLOCK_BYTES = {b'DXT1': 8, b'ATI1': 8, b'BC4U': 8,
               b'DXT3': 16, b'DXT5': 16, b'ATI2': 16, b'BC5U': 16, b'BC5S': 16}
DXGI_BLOCK_BYTES = {71: 8, 72: 8, 74: 16, 75: 16, 77: 16, 78: 16,
                    80: 8, 81: 8, 83: 16, 84: 16, 95: 16, 96: 16, 98: 16, 99: 16}


def _digest(data): return hashlib.sha256(data).hexdigest()


def _whole_path(workspace, relative):
    root = (Path(workspace) / 'files').resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError('Texture is missing or outside this extracted project.')
    return root, path


def _mip_sizes(info, header):
    if info.depth != 1 or info.array_size != 1 or info.caps2:
        raise ValueError('3D/array/cubemap streaming DDS layouts are not supported.')
    if header[84:88] == b'DX10':
        block = DXGI_BLOCK_BYTES.get(int.from_bytes(header[128:132], 'little'))
    else:
        block = BLOCK_BYTES.get(header[84:88])
    if block is None:
        raise ValueError('Only supported block-compressed DDS mip streams can be reconstructed.')
    if info.mipmaps < 2 or info.mipmaps > 20 or info.data_offset not in (128, 148):
        raise ValueError('Unsupported streaming mip count or DDS header.')
    if info.mipmaps > max(info.width, info.height).bit_length():
        raise ValueError('DDS header reports too many mip levels.')
    return [max(1, (max(1, info.width >> level) + 3) // 4) *
            max(1, (max(1, info.height >> level) + 3) // 4) * block
            for level in range(info.mipmaps)]


@dataclass(frozen=True)
class StreamSet:
    files: tuple[Path, ...]  # ascending .0 ... .N
    chunks: tuple[bytes, ...]
    hashes: tuple[str, ...]
    merged: bytes
    top_mip: int
    tail_mips: int

    @property
    def count(self): return len(self.files)


def inspect_stream(workspace, relative):
    """Reject missing/foreign fragments and assemble complete original DDS bytes."""
    root, selected = _whole_path(workspace, relative)
    m = PART.fullmatch(selected.name)
    if not m:
        raise ValueError('Select a CryEngine texture named .dds.0, .dds.1, etc.')
    base = m.group(1)
    found = {}
    for p in selected.parent.iterdir():
        match = PART.fullmatch(p.name)
        if match and match.group(1).casefold() == base.casefold():
            index = int(match.group(2))
            if index >= MAX_PARTS or index in found or not p.is_file() or p.is_symlink():
                raise ValueError('Unsupported, duplicate or unsafe DDS fragment.')
            found[index] = p
    if not found or 0 not in found or max(found) < 1 or sorted(found) != list(range(max(found) + 1)):
        raise ValueError('The full split DDS set must be extracted together (.dds.0 through .dds.N).')
    files = tuple(found[i] for i in sorted(found))
    chunks = tuple(p.read_bytes() for p in files)
    info = parse_dds(chunks[0])
    sizes = _mip_sizes(info, chunks[0])
    tail_len = len(chunks[0]) - info.data_offset
    possible = [n for n in range(1, info.mipmaps) if sum(sizes[-n:]) == tail_len]
    if len(possible) != 1:
        raise ValueError('The DDS .0 fragment does not contain a recognized smallest-mip tail.')
    tail_count = possible[0]
    if len(files) != info.mipmaps - tail_count + 1:
        raise ValueError('DDS streaming set has unexpected part count; check that no fragments are missing.')
    for index in range(1, len(files)):
        expected = sizes[-tail_count - index]
        if len(chunks[index]) != expected:
            raise ValueError(f'{files[index].name} has {len(chunks[index])} bytes; expected {expected}.')
    merged = chunks[0][:info.data_offset] + b''.join(reversed(chunks[1:])) + chunks[0][info.data_offset:]
    if len(merged) != info.data_offset + sum(sizes):
        raise ValueError('Reconstructed DDS byte count does not match expected mip levels.')
    return StreamSet(files, chunks, tuple(map(_digest, chunks)), merged,
                     len(files) - 1, tail_count)


def replace_stream(workspace, relative, replacement_dds, expected_hashes):
    """Split a compatible whole DDS, backup all fragments, and roll back on failure."""
    stream = inspect_stream(workspace, relative)
    if tuple(expected_hashes) != stream.hashes:
        raise ValueError('Streaming DDS fragments changed since preview. Reload before importing.')
    candidate_path = Path(replacement_dds).resolve()
    if not candidate_path.is_file() or candidate_path.suffix.casefold() != '.dds':
        raise ValueError('Choose a complete .dds replacement file, not a PNG or .dds.N fragment.')
    candidate = candidate_path.read_bytes()
    validate_replacement(stream.merged, candidate)  # dimensions, compression, mips, header layout, byte length, decoding
    info = parse_dds(candidate)
    mip_sizes = _mip_sizes(info, candidate)
    payload = candidate[info.data_offset:]
    if len(payload) != sum(mip_sizes):
        raise ValueError('Replacement DDS has unexpected trailing data or truncated mipmaps.')
    pieces = []
    pos = 0
    for n in mip_sizes:
        pieces.append(payload[pos:pos+n]);pos += n
    new_chunks = [stream.chunks[0][:info.data_offset] + b''.join(pieces[-stream.tail_mips:])]
    new_chunks += [pieces[-stream.tail_mips - i] for i in range(1, len(stream.files))]
    if tuple(map(len, new_chunks)) != tuple(map(len, stream.chunks)):
        raise ValueError('Recompressed DDS mip levels do not match the source streaming layout.')
    if tuple(new_chunks) == stream.chunks:
        return inspect_stream(workspace, relative)
    root = (Path(workspace) / 'files').resolve()
    backup = Path(workspace) / 'EditorBackups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup.mkdir(parents=True, exist_ok=False)
    temps=[]
    replaced=[]
    try:
        for file, original, chunk in zip(stream.files, stream.chunks, new_chunks):
            rel = file.resolve().relative_to(root)
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            with saved.open('xb') as handle:handle.write(original)
            fd, temp = tempfile.mkstemp(prefix=file.name + '.edit-', dir=file.parent)
            temps.append(Path(temp))
            with os.fdopen(fd, 'wb') as handle:
                handle.write(chunk);handle.flush();os.fsync(handle.fileno())
        # Recheck all originals immediately before applying any changes.
        if any(_digest(file.read_bytes()) != hashval for file,hashval in zip(stream.files,stream.hashes)):
            raise ValueError('Streaming DDS changed while importing. Original files were not replaced.')
        for file, temp in zip(stream.files, temps):
            os.replace(temp, file)
            replaced.append(file)
    except BaseException as failure:
        rollback_failures=[]
        for file, original in zip(stream.files, stream.chunks):
            if file in replaced:
                try:
                    fd, restore = tempfile.mkstemp(prefix=file.name+'.restore-',dir=file.parent)
                    with os.fdopen(fd,'wb') as handle:handle.write(original);handle.flush();os.fsync(handle.fileno())
                    os.replace(restore,file)
                except OSError as error:rollback_failures.append(f'{file}: {error}')
        if rollback_failures:
            raise RuntimeError('DDS import failed AND rollback incomplete; keep backup '+str(backup)+'; '+repr(rollback_failures)) from failure
        raise
    finally:
        for temp in temps:temp.unlink(missing_ok=True)
    return inspect_stream(workspace, relative)
