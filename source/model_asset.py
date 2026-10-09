"""Experimental CryTek chunked model inspection and conservative binary replacement.

Provides safe native file export for external tools. Reimport only permits the
same chunk identity/offset table and exact file length; it cannot validate mesh
semantics, rigging, animation, or actual Evolve runtime compatibility.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib
import os
import shutil
import struct
import tempfile

MODEL_EXTENSIONS = {'.cgf', '.cga', '.chr', '.skin'}
MAX_MODEL_BYTES = 256 * 1024 * 1024
MAX_CHUNKS = 100_000


@dataclass(frozen=True)
class ModelInfo:
    type_name: str
    version: int
    chunks: int
    table_offset: int
    chunk_table: bytes
    file_length: int

    @property
    def description(self):
        return f'CryTek {self.type_name} | version 0x{self.version:X} | {self.chunks} chunks | {self.file_length:,} bytes'


def is_model(path):
    return Path(path).suffix.casefold() in MODEL_EXTENSIONS


def inspect_model(data: bytes) -> ModelInfo:
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError('Model exceeds safe inspection limit (256 MiB).')
    if len(data) < 24 or data[:8] != b'CryTek\0\0':
        raise ValueError('Unrecognized model header. Raw export remains available; model reimport is disabled.')
    filetype, version, table_offset = struct.unpack_from('<III', data, 8)
    if filetype not in (0xFFFF0000, 0xFFFF0001):
        raise ValueError('Unknown CryTek geometry/animation file type.')
    if table_offset < 20 or table_offset > len(data) - 4:
        raise ValueError('Invalid CryTek chunk table offset.')
    count, = struct.unpack_from('<I', data, table_offset)
    if not count or count > MAX_CHUNKS or table_offset + 4 + 16 * count > len(data):
        raise ValueError('Missing, incomplete, or oversized CryTek chunk table.')
    table = data[table_offset:table_offset + 4 + 16*count]
    offsets = []
    for index in range(count):
        kind, chunk_version, offset, chunk_id = struct.unpack_from('<IIII', table, 4 + 16*index)
        if offset < 20 or offset >= len(data) or (table_offset <= offset < table_offset + len(table)):
            raise ValueError('Invalid model chunk offset.')
        offsets.append(offset)
    if len(set(offsets)) != len(offsets):
        raise ValueError('Overlapping or repeated model chunk offsets.')
    return ModelInfo('Geometry' if filetype == 0xFFFF0000 else 'Animation',
                     version, count, table_offset, table, len(data))


def validate_model_replacement(original: bytes, candidate: bytes) -> ModelInfo:
    before, after = inspect_model(original), inspect_model(candidate)
    for key in ('type_name', 'version', 'chunks', 'table_offset', 'chunk_table', 'file_length'):
        if getattr(before,key) != getattr(after,key):
            raise ValueError(f'Incompatible model: {key} changed. Identical chunk table and binary length are required.')
    if original[:20] != candidate[:20]:
        raise ValueError('Model header changed; refusing experimental replacement.')
    return after


def _workspace_model(workspace,relative):
    root=(Path(workspace)/'files').resolve()
    source=(root/relative).resolve()
    if not source.is_relative_to(root) or not source.is_file() or source.is_symlink() or not is_model(relative):
        raise ValueError('Model is missing or outside this project.')
    return root,source


def export_model(workspace,relative,destination):
    _,source=_workspace_model(workspace,relative)
    destination=Path(destination)
    if destination.suffix.casefold()!=source.suffix.casefold() or not destination.parent.is_dir() or destination.resolve()==source:
        raise ValueError('Export to a separate existing folder with the same native model extension.')
    with source.open('rb') as input_file, destination.open('xb') as output:
        shutil.copyfileobj(input_file,output)
    return destination


def replace_model(workspace,relative,imported_file,expected_sha):
    root,target=_workspace_model(workspace,relative)
    candidate_path=Path(imported_file).resolve()
    if candidate_path == target or candidate_path.suffix.casefold()!=target.suffix.casefold() or not candidate_path.is_file():
        raise ValueError('Select a different model file with the same extension.')
    current=target.read_bytes()
    if hashlib.sha256(current).hexdigest()!=expected_sha:
        raise ValueError('Model changed outside editor; reload before importing.')
    candidate=candidate_path.read_bytes()
    validate_model_replacement(current,candidate)
    if current==candidate:return current
    backup=Path(workspace)/'EditorBackups'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')/target.relative_to(root)
    backup.parent.mkdir(parents=True,exist_ok=True)
    with backup.open('xb') as out:out.write(current)
    fd,temp=tempfile.mkstemp(prefix=target.name+'.edit-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as out:out.write(candidate);out.flush();os.fsync(out.fileno())
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected_sha:
            raise ValueError('Model changed while importing. Backup preserved.')
        os.replace(temp,target)
    finally:Path(temp).unlink(missing_ok=True)
    return candidate
