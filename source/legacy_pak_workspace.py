"""Safe standalone editor for historical (2012) *ordinary ZIP* Evolve PAKs.

This is intentionally NOT the Stage 2 signed CryPak workflow. Original archives
are immutable; edits are sparse project overrides and builds create separate
unsigned ZIP-compatible .pak files. Neither game installs nor signing keys are
touched. Unknown ZIP methods, ZIP encryption, links, unsafe paths and oversized
entries fail closed.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile
from historical_zip import HistoricalZipFile

PROJECT_FILE = ".evolve-legacy-pak.json"
PROJECT_FORMAT = "evolve-legacy-zip-v1"
SUPPORTED_METHODS = frozenset((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
MAX_ENTRIES = 60000
MAX_FILE = 2 * 1024 ** 3
MAX_TOTAL = 16 * 1024 ** 3
MAX_PREVIEW = 3 * 1024 ** 2
BUFFER = 1024 * 1024


def sha_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as src:
        for part in iter(lambda: src.read(BUFFER), b""):
            value.update(part)
    return value.hexdigest()


def normalized_member(name):
    # CryEngine virtual paths are case-insensitive but may use ZIP slashes.
    from evolve_pak_workspace import normalized_name
    normalized = normalized_name(name)
    if name.endswith("/") or name.endswith("\\"):
        raise ValueError("Directories are not editable archive members.")
    return normalized


def _reject_links(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Use a regular non-linked file.")


def _safe_inside(root, relative):
    root = Path(root).absolute()
    target = root.joinpath(*relative.split("/"))
    # Do not call resolve() on the root before inspecting symlinks: that would
    # hide a malicious "overrides" -> outside-folder junction/symlink.
    for part in (target, *target.parents):
        if part.is_symlink():
            raise ValueError("Linked project paths are not allowed.")
        if part == root:
            break
    if not target.resolve().is_relative_to(root):
        raise ValueError("Project entry resolves outside project folder.")
    return target


def _inventory(archive, *, allow_empty=False):
    records = []
    seen = set()
    total = 0
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        if len(records) >= MAX_ENTRIES:
            raise ValueError("Legacy PAK has too many entries.")
        name = normalized_member(entry.filename)
        key = name.casefold()
        if key in seen:
            raise ValueError("Case-insensitive duplicate legacy PAK path: " + name)
        seen.add(key)
        mode = (entry.external_attr >> 16)
        if stat.S_IFMT(mode) == stat.S_IFLNK:
            raise ValueError("ZIP symlink member is not supported.")
        if entry.flag_bits & 1:
            raise ValueError("Encrypted legacy ZIP entries are not supported.")
        if entry.compress_type not in SUPPORTED_METHODS:
            raise ValueError("Unsupported legacy ZIP compression method: " +
                             str(entry.compress_type))
        if entry.file_size > MAX_FILE:
            raise ValueError("Legacy ZIP entry exceeds the per-file safety limit.")
        if (entry.file_size > 2 * BUFFER and
                entry.file_size > max(1, entry.compress_size) * 1000):
            raise ValueError("Legacy PAK has an extreme compression ratio.")
        total += entry.file_size
        if total > MAX_TOTAL:
            raise ValueError("Legacy PAK uncompressed size is too large.")
        records.append({
            "path": name, "archive_name": entry.filename,
            "size": entry.file_size, "compressed": entry.compress_size,
            "method": entry.compress_type, "crc": entry.CRC,
        })
    if not records and not allow_empty:
        raise ValueError("Legacy PAK contains no editable files.")
    return records


def inspect_archive(source, *, verify_crc=False, allow_empty=False):
    """Return metadata only for valid ordinary ZIP PAKs, without extracting."""
    candidate = Path(source).expanduser()
    _reject_links(candidate)
    source = candidate.resolve(strict=True)
    if source.suffix.casefold() != ".pak" or not zipfile.is_zipfile(source):
        raise ValueError("Select a ZIP-compatible historical .pak archive.")
    try:
        with HistoricalZipFile(source, "r") as archive:
            entries = _inventory(archive, allow_empty=allow_empty)
            if verify_crc:
                for entry in archive.infolist():
                    with archive.open(entry) as stream:
                        while stream.read(BUFFER):
                            pass
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ValueError("Malformed or unreadable legacy PAK: " + str(exc)) from exc
    return {"name": source.name, "entries": entries,
            "count": len(entries), "uncompressed": sum(e["size"] for e in entries)}


def create_project(source, folder):
    """Index once; do not unpack hundreds of megabytes until a file is opened."""
    candidate = Path(source).expanduser()
    _reject_links(candidate)
    source = candidate.resolve(strict=True)
    folder = Path(folder).resolve()
    if folder.exists():
        raise FileExistsError("Choose a new legacy project directory.")
    if source.is_relative_to(folder) or folder.is_relative_to(source):
        raise ValueError("Legacy source and project directory must be separate.")
    inventory = inspect_archive(source, verify_crc=True)
    digest = sha_file(source)
    folder.mkdir(parents=True)
    try:
        manifest = {
            "format": PROJECT_FORMAT, "source": str(source),
            "source_sha256": digest, "source_name": source.name,
            "created": datetime.now(timezone.utc).isoformat(),
            "entries": inventory["entries"],
        }
        (folder / PROJECT_FILE).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(folder)
        raise
    return folder


def load_project(folder, *, check_source=True):
    folder = Path(folder).resolve(strict=True)
    manifest_file = folder / PROJECT_FILE
    if manifest_file.is_symlink():
        raise ValueError("Legacy project manifest must not be a link.")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("format") != PROJECT_FORMAT:
        raise ValueError("Not a supported legacy Evolve editing project.")
    records = manifest.get("entries")
    if not isinstance(records, list) or not records or len(records) > MAX_ENTRIES:
        raise ValueError("Malformed legacy project inventory.")
    unique = set()
    for item in records:
        rel = normalized_member(item["path"])
        if rel != item["path"] or rel.casefold() in unique:
            raise ValueError("Unsafe or duplicated legacy project entry.")
        unique.add(rel.casefold())
    candidate = Path(manifest["source"])
    _reject_links(candidate)
    source = candidate.resolve(strict=True)
    if check_source and sha_file(source) != manifest["source_sha256"]:
        raise ValueError("Original legacy PAK changed; refuse to edit/rebuild from a new baseline.")
    manifest["folder"] = folder
    manifest["source_path"] = source
    return manifest


def entries_by_name(project):
    return {entry["path"]: entry for entry in project["entries"]}


def _override_path(project, name):
    if name not in entries_by_name(project):
        raise ValueError("Selected file is not in this legacy PAK.")
    return _safe_inside(project["folder"] / "overrides", name)


def read_member(project, name, *, max_bytes=MAX_FILE):
    record = entries_by_name(project).get(name)
    if record is None:
        raise ValueError("Unknown legacy PAK member.")
    staged = _override_path(project, name)
    if staged.is_file():
        _reject_links(staged)
        if staged.stat().st_size > max_bytes:
            raise ValueError("Staged file is too large to read into memory.")
        return staged.read_bytes()
    if record["size"] > max_bytes:
        raise ValueError("Archive member is too large for this preview.")
    with HistoricalZipFile(project["source_path"]) as archive:
        data = archive.read(record["archive_name"])
    if len(data) != record["size"]:
        raise ValueError("Archive data size changed.")
    return data


def read_editable_text(project, name, *, limit=MAX_PREVIEW):
    data = read_member(project, name, max_bytes=limit)
    if b"\x00" in data:
        raise ValueError("This is a binary asset. Use Replace File or Export Original.")
    if sum(byte < 9 or 13 < byte < 32 for byte in data[:4096]) > 8:
        raise ValueError("Binary or compiled text cannot be edited in the text pane.")
    codecs = ("utf-8-sig",) if data.startswith(b"\xef\xbb\xbf") else ("utf-8", "cp1252")
    for codec in codecs:
        try:
            return data.decode(codec), codec
        except UnicodeDecodeError:
            continue
    raise ValueError("Unknown text encoding; export the file for external editing.")


def _stage_stream(project, name, reader, size):
    dest = _override_path(project, name)
    if not (0 <= size <= MAX_FILE):
        raise ValueError("Replacement size exceeds legacy PAK safety limits.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    previous = dest if dest.is_file() else None
    fd, temporary = tempfile.mkstemp(prefix=".legacy-", dir=dest.parent)
    temp = Path(temporary)
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                chunk = reader.read(BUFFER)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_FILE or written > size:
                    raise ValueError("Replacement changed size during copy.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if written != size:
            raise ValueError("Replacement was incomplete.")
        # Keep last staged version as a recoverable copy (no edits to originals).
        if previous:
            backup = _safe_inside(project["folder"] / "history", name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                backup.unlink()
            shutil.copy2(previous, backup)
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return digest.hexdigest()


def stage_file(project, name, replacement):
    replacement = Path(replacement).resolve(strict=True)
    _reject_links(replacement)
    size = replacement.stat().st_size
    if size > MAX_FILE:
        raise ValueError("Replacement is too large.")
    if replacement == project["source_path"]:
        raise ValueError("Cannot stage the PAK itself as one of its own members.")
    with replacement.open("rb") as source:
        return _stage_stream(project, name, source, size)


def stage_text(project, name, text, encoding):
    if encoding not in ("utf-8", "utf-8-sig", "cp1252"):
        raise ValueError("Unsupported text encoding.")
    if not isinstance(text, str):
        raise ValueError("Text must be a string.")
    payload = text.encode(encoding)
    if len(payload) > MAX_PREVIEW:
        raise ValueError("Text edit exceeds the 3 MiB editor limit.")
    from io import BytesIO
    return _stage_stream(project, name, BytesIO(payload), len(payload))


def revert_file(project, name):
    dest = _override_path(project, name)
    if dest.is_file():
        _reject_links(dest)
        dest.unlink()
    return True


def changed_members(project):
    changes = []
    for record in project["entries"]:
        dest = _override_path(project, record["path"])
        if dest.is_file():
            _reject_links(dest)
            if dest.stat().st_size > MAX_FILE:
                raise ValueError("Oversized staged file.")
            changes.append(record["path"])
    return changes


def export_member(project, name, destination):
    """Stream even multi-hundred-megabyte FSB / USM files without buffering."""
    record = entries_by_name(project).get(name)
    if record is None:
        raise ValueError("Select a file from the legacy archive.")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Choose a new export filename.")
    if destination.is_relative_to(project["folder"]):
        raise ValueError("Export outside the editing project's internal folders.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = _override_path(project, name)
    with destination.open("xb") as out:
        try:
            if staged.is_file():
                _reject_links(staged)
                with staged.open("rb") as source:
                    shutil.copyfileobj(source, out, BUFFER)
            else:
                with HistoricalZipFile(project["source_path"]) as archive, \
                     archive.open(record["archive_name"], "r") as source:
                    shutil.copyfileobj(source, out, BUFFER)
        except Exception:
            out.close()
            destination.unlink(missing_ok=True)
            raise
    return destination


def rebuild_pak(project, destination, *, progress=None):
    """Repack and CRC-verify into a *new*, unsigned historical PAK.

    The current Stage 2 game will not accept this as a signed mod archive.
    """
    project = load_project(project["folder"], check_source=True)
    destination = Path(destination).resolve()
    if destination.suffix.casefold() != ".pak":
        raise ValueError("Choose a .pak output filename.")
    if destination == project["source_path"] or destination.exists():
        raise FileExistsError("Never overwrite an original PAK or existing output.")
    if destination.is_relative_to(project["folder"]):
        raise ValueError("Save the rebuilt PAK outside internal project data.")
    edited = set(changed_members(project))
    if not edited:
        raise ValueError("No staged changes to build; modify at least one file.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".evolve-legacy-", suffix=".pak.tmp", dir=destination.parent)
    os.close(fd)
    temp = Path(temporary)
    try:
        with HistoricalZipFile(project["source_path"]) as original, \
             zipfile.ZipFile(temp, "w", allowZip64=True) as output:
            original_entries = {normalized_member(i.filename): i
                                for i in original.infolist() if not i.is_dir()}
            output.comment = original.comment
            for idx, record in enumerate(project["entries"], 1):
                name = record["path"]
                item = copy.copy(original_entries[name])
                if item.compress_type not in SUPPORTED_METHODS:
                    raise ValueError("Unsafe compression found during build.")
                # zipfile writes updated CRC/size headers and deflate payloads.
                with output.open(item, "w", force_zip64=True) as dst:
                    if name in edited:
                        staged = _override_path(project, name)
                        _reject_links(staged)
                        source = staged.open("rb")
                    else:
                        source = original.open(record["archive_name"], "r")
                    with source:
                        shutil.copyfileobj(source, dst, BUFFER)
                if progress and (idx % 25 == 0 or idx == len(project["entries"])):
                    progress(idx, len(project["entries"]))
        with zipfile.ZipFile(temp) as rebuilt:
            entries = _inventory(rebuilt)
            if len(entries) != len(project["entries"]):
                raise ValueError("Rebuilt PAK lost archive entries.")
            bad = rebuilt.testzip()
            if bad:
                raise ValueError("Rebuilt legacy PAK failed CRC: " + bad)
        if sha_file(project["source_path"]) != project["source_sha256"]:
            raise ValueError("Original PAK changed while rebuilding.")
        if destination.exists():
            raise FileExistsError("Output appeared while rebuilding; refusing to overwrite it.")
        os.replace(temp, destination)
        return {"output": str(destination), "changes": len(edited),
                "members": len(project["entries"]), "bytes": destination.stat().st_size}
    finally:
        temp.unlink(missing_ok=True)
