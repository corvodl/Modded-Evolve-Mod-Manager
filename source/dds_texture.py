"""Conservative preview/export/import of whole DDS textures from extracted PAK workspaces.

DDS previews and PNG exports show the top-level mip only. Replacements must be
pre-encoded DDS files with the same format, dimensions, mip layout and size.
Split CryEngine streaming texture pieces (.dds.0, .dds.1, ...) are not supported.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import hashlib
import os
import re
import struct
import tempfile
from datetime import datetime

from PIL import Image, UnidentifiedImageError


SPLIT_DDS = re.compile(r"\.dds\.\d+$", re.IGNORECASE)
MAX_PREVIEW_PIXELS = 100_000_000


@dataclass(frozen=True)
class DDSInfo:
    width: int
    height: int
    depth: int
    mipmaps: int
    pixel_format: str
    pixel_signature: bytes
    caps2: int
    resource_dimension: int
    array_size: int
    data_offset: int
    byte_length: int
    layout_signature: bytes

    @property
    def description(self):
        return f"{self.width} x {self.height} | {self.pixel_format} | {self.mipmaps} mip level(s)"


def is_dds(path):
    return Path(path).name.lower().endswith('.dds')


def is_split_dds(path):
    return bool(SPLIT_DDS.search(Path(path).name))


def _u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def parse_dds(data: bytes) -> DDSInfo:
    """Validate the standard DDS envelope; don't infer unknown compression layouts."""
    if len(data) < 128 or data[:4] != b'DDS ':
        raise ValueError('Not a complete standalone DDS texture.')
    if _u32(data, 4) != 124 or _u32(data, 76) != 32:
        raise ValueError('Invalid DDS header size or pixel-format structure.')
    flags = _u32(data, 8)
    height, width = _u32(data, 12), _u32(data, 16)
    if not (flags & 0x1000) or not (flags & 0x4) or not width or not height:
        raise ValueError('DDS header lacks a valid width/height.')
    if width * height > MAX_PREVIEW_PIXELS:
        raise ValueError('DDS dimensions are too large for safe preview.')
    depth = _u32(data, 24) if flags & 0x800000 else 1
    mipmaps = _u32(data, 28) if flags & 0x20000 else 1
    if not depth or not mipmaps or mipmaps > 32:
        raise ValueError('Invalid DDS depth or mip level count.')
    pf_flags = _u32(data, 80)
    fourcc = data[84:88]
    pixel_signature = data[80:108]  # flags, fourCC, bit count, all channel masks
    caps2 = _u32(data, 112)
    resource_dimension, array_size = 0, 1
    offset = 128
    if pf_flags & 0x4:
        if fourcc == b'DX10':
            if len(data) < 148:
                raise ValueError('Missing DDS DX10 extended header.')
            fmt, resource_dimension, misc_flag, array_size, misc2 = struct.unpack_from('<5I', data, 128)
            if not array_size or array_size > 2048:
                raise ValueError('Invalid DX10 array size.')
            pixel_signature += data[128:148]
            pixel_format = f'DXGI {fmt}'
            offset = 148
        else:
            pixel_format = fourcc.decode('ascii', errors='replace').strip('\x00') or 'FOURCC'
    else:
        bits = _u32(data, 88)
        if not (pf_flags & (0x40 | 0x20000 | 0x2)) or bits not in (8, 16, 24, 32):
            raise ValueError('Unsupported or malformed DDS pixel-format header.')
        pixel_format = f'{bits}-bit RGB/luminance'
    if len(data) <= offset:
        raise ValueError('DDS has no texture payload.')
    # Compare header fields that govern pitch, flags and surface/cubemap caps.
    layout_signature = data[8:12] + data[20:24] + data[108:128]
    return DDSInfo(width, height, depth, mipmaps, pixel_format, pixel_signature,
                   caps2, resource_dimension, array_size, offset, len(data), layout_signature)


def validate_replacement(original: bytes, replacement: bytes) -> DDSInfo:
    before = parse_dds(original)
    after = parse_dds(replacement)
    for name in ('width', 'height', 'depth', 'mipmaps', 'pixel_signature', 'caps2',
                 'resource_dimension', 'array_size', 'data_offset', 'byte_length', 'layout_signature'):
        if getattr(before, name) != getattr(after, name):
            raise ValueError(f'Incompatible DDS replacement: {name} differs. Re-export with the original format, mipmaps and dimensions.')
    # Confirm Pillow can decode top-level surfaces before the replacement is saved.
    preview_dds(replacement)
    return after


def preview_dds(data: bytes, *, max_size=(700, 540)):
    info = parse_dds(data)
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format != 'DDS' or im.size != (info.width, info.height):
                raise ValueError('The DDS decoder disagrees with the file header.')
            im.load()
            out = im.convert('RGBA')
            out.thumbnail(max_size, Image.Resampling.LANCZOS)
            return out
    except (OSError, UnidentifiedImageError, NotImplementedError, ValueError) as e:
        raise ValueError(f'This DDS encoding cannot be previewed by the bundled decoder: {e}') from e


def export_png(data: bytes, destination: Path):
    """Write the original-resolution top-level DDS image as a PNG."""
    info = parse_dds(data)
    destination = Path(destination)
    if destination.suffix.lower() != '.png':
        raise ValueError('Choose a .png output filename.')
    if not destination.parent.is_dir():
        raise FileNotFoundError('PNG destination folder does not exist.')
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format != 'DDS' or im.size != (info.width, info.height):
                raise ValueError('DDS header dimensions disagree with the decoder.')
            im.load()
            im.convert('RGBA').save(destination, format='PNG')
    except (OSError, UnidentifiedImageError, NotImplementedError, ValueError) as e:
        raise ValueError(f'Cannot export this DDS as PNG: {e}') from e
    return info


def _path_in_workspace(workspace: Path, relative: str) -> Path:
    root = (Path(workspace) / 'files').resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Texture file is missing or outside this project.')
    if is_split_dds(path):
        # Allow a complete single-mip .dds.0, but never a missing streaming piece.
        from dds_streaming import inspect_whole_part0
        try:
            inspect_whole_part0(workspace, relative)
        except ValueError as error:
            raise ValueError('Only whole DDS textures or complete standalone .dds.0 files may be replaced: ' + str(error)) from error
    elif not is_dds(path):
        raise ValueError('Only whole DDS textures support replacement.')
    return path


def replace_dds(workspace, relative, imported_file, expected_hash):
    """Import a compatible DDS atomically, keeping a timestamped original backup."""
    target = _path_in_workspace(Path(workspace), relative)
    imported_file = Path(imported_file).resolve()
    if imported_file.suffix.lower() != '.dds' or not imported_file.is_file():
        raise ValueError('Select a complete .dds file (not a PNG or split .dds.N file).')
    if target == imported_file:
        raise ValueError('Choose a different file for DDS import.')
    before = target.read_bytes()
    if hashlib.sha256(before).hexdigest() != expected_hash:
        raise ValueError('This DDS changed outside the editor. Reload it before importing.')
    replacement = imported_file.read_bytes()
    validate_replacement(before, replacement)
    if replacement == before:
        return before
    backup = Path(workspace) / 'EditorBackups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f') / Path(relative)
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open('xb') as out:
        out.write(before)
    fd, temporary = tempfile.mkstemp(prefix=target.name + '.edit-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('File changed during DDS import. Original backup retained; reload before retrying.')
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return replacement
