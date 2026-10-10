"""Conservative context-menu file operations for extracted PAK workspaces.

Only targets manifest-listed files and never the installed game. All writes
are staged with hash checks and EditorBackups; original restores require an
actual backup whose checksum matches the extraction manifest.
"""
from __future__ import annotations
from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import uuid


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(2 * 1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def checked_path(workspace, relative, *, directory=False):
    from evolve_pak_workspace import normalized_name
    root = (Path(workspace) / 'files').resolve()
    if not root.is_dir():
        raise FileNotFoundError('Workspace extraction folder not found.')
    if directory:
        rel = normalized_name(relative) if relative else ''
    else:
        rel = normalized_name(relative)
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError('File or folder resolves outside the extracted project.')
    if directory and not target.is_dir():
        raise FileNotFoundError('Extracted folder is missing.')
    if not directory and not target.is_file():
        raise FileNotFoundError('Extracted file is missing.')
    return target


def backup_path(workspace, relative):
    backup_root = (Path(workspace) / 'EditorBackups').resolve()
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '-' + uuid.uuid4().hex
    target = (backup_root / stamp / relative).resolve()
    if not target.is_relative_to(backup_root):
        raise ValueError('Unsafe backup destination.')
    return target


def _atomic_copy(source, target, expected):
    fd, tmp = tempfile.mkstemp(prefix=target.name + '.replace-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output, Path(source).open('rb') as inp:
            shutil.copyfileobj(inp, output, length=2 * 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if sha_file(tmp) != expected:
            raise RuntimeError('Temporary replacement differs from the validated input.')
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def replace_raw_file(workspace, relative, new_file, expected_hash):
    """Replace a non-CryXML, non-DDS, non-model extracted binary file.

    Caller must first obtain explicit user confirmation about binary formats.
    """
    target = checked_path(workspace, relative)
    candidate = Path(new_file).resolve()
    if not candidate.is_file() or target == candidate:
        raise ValueError('Choose a different, existing replacement file.')
    if sha_file(target) != expected_hash:
        raise ValueError('Extracted file changed outside the editor. Reload before replacing.')
    # Prevent accidentally embedding an oversized file in a game PAK.
    if candidate.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('Replacement exceeds the 512 MiB conservative size limit.')
    replacement_hash = sha_file(candidate)
    if replacement_hash == expected_hash:
        return False
    savepoint = backup_path(workspace, relative)
    savepoint.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(target, savepoint)
    if sha_file(savepoint) != expected_hash:
        raise RuntimeError('Backup verification failed; extracted file has not been replaced.')
    if sha_file(target) != expected_hash:
        raise RuntimeError('File changed during replacement; backup retained.')
    _atomic_copy(candidate, target, replacement_hash)
    return True


def extracted_original_backup(workspace, relative, original_hash):
    """Find a verified original backup (not simply the most recent version)."""
    checked_path(workspace, relative)
    root = (Path(workspace) / 'EditorBackups').resolve()
    if not root.is_dir() or not original_hash or len(original_hash) != 64:
        return None
    matches = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        candidate = (folder / relative).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            matches.append(candidate)
    for candidate in sorted(matches):
        if sha_file(candidate) == original_hash:
            return candidate
    return None


def restore_extracted_original(workspace, relative, original_hash, expected_hash):
    """Restore only exact manifest-matching bytes with backup and stale-file guard."""
    target = checked_path(workspace, relative)
    if sha_file(target) != expected_hash:
        raise ValueError('Extracted file changed outside the editor. Reload before restoring.')
    if expected_hash == original_hash:
        return False
    source = extracted_original_backup(workspace, relative, original_hash)
    if source is None:
        raise ValueError('Original bytes are not in EditorBackups. The original cannot be safely restored from this workspace.')
    saved = backup_path(workspace, relative)
    saved.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(target, saved)
    if sha_file(saved) != expected_hash:
        raise RuntimeError('Backup verification failed; not restoring.')
    if sha_file(target) != expected_hash or sha_file(source) != original_hash:
        raise RuntimeError('Original or current file changed during restore. Nothing overwritten.')
    _atomic_copy(source, target, original_hash)
    return True
