"""Safely discover and copy editable loose game files outside PAKs.

Loose game files are never PAK signing inputs and editing never writes to the
installed game. Only explicit copies inside the manager's Projects are edited.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid

TEXT_EXTENSIONS = frozenset({'.xml', '.txt', '.cfg', '.ini', '.lua', '.json',
                             '.csv', '.mtl', '.cdf', '.chrparams', '.log', '.yaml', '.yml'})
ASSET_EXTENSIONS = frozenset({'.dds', '.tif', '.tiff', '.png', '.bmp', '.tga',
                              '.cgf', '.cga', '.chr', '.skin', '.skinm', '.chrm', '.fbx', '.obj'})
SUPPORTED = TEXT_EXTENSIONS | ASSET_EXTENSIONS
MAX_SCAN = 12000
MAX_COPY = 512 * 1024 * 1024


@dataclass(frozen=True)
class LooseFile:
    relative: str
    size: int


def is_editable_name(name: str) -> bool:
    lower = name.casefold()
    # Cooked DDS streams are .dds.0, .dds.1, .dds.0a, etc.
    if '.dds.' in lower:
        suffix = lower.rsplit('.dds.', 1)[1]
        return suffix.isdigit() or (suffix[:-1].isdigit() and suffix.endswith('a'))
    return Path(lower).suffix in SUPPORTED


def scan_loose_files(game_root: Path, max_files: int = MAX_SCAN) -> list[LooseFile]:
    root = Path(game_root)
    if not root.is_dir() or root.is_symlink() or max_files < 1:
        return []
    found = []
    def walk(folder: Path):
        # Do not follow junctions/symlinks out of the actual game root.
        try:
            with os.scandir(folder) as entries:
                children = sorted(entries, key=lambda e: e.name.casefold())
        except (OSError, PermissionError):
            return
        for entry in children:
            if len(found) >= max_files:
                return
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and is_editable_name(entry.name):
                    relative = Path(entry.path).relative_to(root).as_posix()
                    found.append(LooseFile(relative, entry.stat(follow_symlinks=False).st_size))
            except (OSError, ValueError):
                continue
    walk(root)
    return found


def verified_source(game_root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative.replace('\\', '/'))
    if (not relative or rel.is_absolute() or any(p in ('.', '..', '') for p in rel.parts)
            or ':' in relative or not is_editable_name(rel.name)):
        raise ValueError('Invalid loose game-file path')
    root = Path(game_root).resolve(strict=True)
    path = root.joinpath(*rel.parts)
    current = root
    for piece in rel.parts:
        current = current / piece
        if current.is_symlink():
            raise ValueError('Linked game paths cannot be edited')
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('Loose file is missing or outside the game folder')
    if path.stat().st_size > MAX_COPY:
        raise ValueError('Loose file is too large to copy safely')
    return path


def copy_to_project(game_root: Path, relative: str, projects: Path) -> Path:
    """Keep the first copy; never overwrite existing edited project content."""
    source = verified_source(game_root, relative)
    project = Path(projects).resolve() / 'LooseGameFiles'
    game = Path(game_root).resolve(strict=True)
    if project.is_relative_to(game) or game.is_relative_to(project):
        raise ValueError('Project copies must be stored outside the installed game folder')
    target = project.joinpath(*PurePosixPath(relative.replace('\\', '/')).parts)
    if not target.resolve().is_relative_to(project):
        raise ValueError('Project path escapes the configured projects folder')
    current = project
    for piece in PurePosixPath(relative.replace('\\', '/')).parts:
        current = current / piece
        if current.is_symlink():
            raise ValueError('Linked project paths cannot be edited')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file(): raise ValueError('Project target is not a file')
        return target
    temp = target.with_name(target.name + '.new-' + uuid.uuid4().hex)
    try:
        shutil.copyfile(source, temp)
        def sha(path):
            digest = hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda: f.read(1048576), b''): digest.update(block)
            return digest.digest()
        if sha(temp) != sha(source):
            raise RuntimeError('Copied game file failed hash verification')
        # Never clobber another file created during the copy.
        with temp.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    finally:
        temp.unlink(missing_ok=True)
    return target
