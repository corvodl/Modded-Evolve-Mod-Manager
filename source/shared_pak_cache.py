"""Background catalog and on-demand verified extraction of six shared CryPAKs.

The manager indexes *names* from signed central directories in a worker, and
decodes only exact referenced entries into a bounded read-only preview cache.
Never changes staged/installed archives or creates editable project workspaces.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import threading
from xml.etree import ElementTree as ET

SHARED_NAMES = frozenset({
    "characters_hunters_shared_data.pak",
    "characters_hunters_shared_ts.pak",
    "characters_monsters_shared_data.pak",
    "characters_monsters_shared_ts.pak",
    "characters_creatures_shared_data.pak",
    "characters_creatures_shared_ts.pak",
})
MAX_ENTRIES = 150000
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024 * 1024
_ALLOWED = re.compile(
    r"\.(?:mtl|cdf|skin|skinm|chr|chrm|chrparams|cgf|cgfm|cga|cgam|caf|dba|"
    r"bspace|comb|anim|dds(?:\.\d+)?)$", re.I)
_lock = threading.RLock()
_current = None
_worker = None
_error = ""
_status = "Not started"


@dataclass
class _Archive:
    path: Path
    relative: str
    fingerprint: tuple
    entries: dict
    verified_info: object
    cache_dir: Path


class SharedCatalog:
    def __init__(self, stage, cache_root, key):
        self.stage = Path(stage).resolve()
        self.cache_root = Path(cache_root).resolve()
        self.key = Path(key).resolve()
        self.archives = []
        self.index = defaultdict(list)
        self._read_lock = threading.RLock()
        self._retained = 0

    def scan(self):
        from universal_stage import allowed_paks
        from evolve_pak_rekey import extract_plain_cdr
        from evolve_pak_workspace import normalized_name
        stage_paks = (self.stage / "paks").resolve()
        allowed = allowed_paks(self.stage)
        names = [p for p in allowed.values()
                 if Path(p).name.casefold() in SHARED_NAMES]
        if not self.key.is_file() or not self.key.is_relative_to(self.stage):
            raise FileNotFoundError("Shared asset index needs the staged public key.")
        key_hash = hashlib.sha256(self.key.read_bytes()).hexdigest()
        for rel in sorted(names):
            path = (stage_paks / rel).resolve()
            if (not path.is_relative_to(stage_paks) or path.is_symlink()
                    or not path.is_file()):
                continue
            before = path.stat()
            fingerprint = (before.st_size, before.st_mtime_ns)
            info, _, _, _ = extract_plain_cdr(path, self.key)
            if (path.stat().st_size, path.stat().st_mtime_ns) != fingerprint:
                raise ValueError("Shared archive changed during indexing: " + rel)
            names_by_key = {}
            for entry_name, meta in info["entries"].items():
                try:
                    virtual = normalized_name(entry_name)
                except ValueError:
                    continue
                if not _ALLOWED.search(virtual):
                    continue
                if (meta.get("method") not in (13, 14)
                        or meta.get("extra_length") != 0
                        or not 0 < meta.get("original_size", 0) <= MAX_ASSET_BYTES
                        or not 0 < meta.get("compressed_size", 0) <= MAX_ASSET_BYTES):
                    continue
                names_by_key[virtual.casefold()] = entry_name
                if sum(len(a.entries) for a in self.archives) + len(names_by_key) > MAX_ENTRIES:
                    raise ValueError("Shared reference catalog exceeds safety limit.")
            identity = hashlib.sha256(
                (str(path).casefold() + key_hash + str(fingerprint)).encode()
            ).hexdigest()[:20]
            cache_dir = self.cache_root / identity
            archive = _Archive(path, rel, fingerprint, names_by_key, info, cache_dir)
            self.archives.append(archive)
            for key, entry_name in names_by_key.items():
                self.index[key].append({
                    "archive": rel, "relative": entry_name.replace("\\", "/"),
                    "workspace": None, "_shared_archive": archive,
                    "_shared_entry": entry_name,
                })
        # Remove obsolete self-owned cache generations when the stage/key
        # changes. Unrelated folders and symlink directories are untouched.
        if self.cache_root.is_dir():
            keep = {archive.cache_dir.name for archive in self.archives}
            for stale in self.cache_root.iterdir():
                if (stale.is_dir() and not stale.is_symlink()
                        and re.fullmatch(r"[0-9a-f]{20}", stale.name)
                        and stale.name not in keep):
                    shutil.rmtree(stale)
            for archive in self.archives:
                if archive.cache_dir.is_dir():
                    for existing in archive.cache_dir.rglob("*"):
                        if existing.is_symlink():
                            continue
                        if existing.is_file() and not existing.name.endswith(".building"):
                            self._retained += existing.stat().st_size
        return self

    def is_current(self):
        try:
            from universal_stage import allowed_paks
            listed = sorted(rel for rel in allowed_paks(self.stage).values()
                            if Path(rel).name.casefold() in SHARED_NAMES)
            if listed != sorted(a.relative for a in self.archives):
                return False
            return all((a.path.stat().st_size, a.path.stat().st_mtime_ns)
                       == a.fingerprint for a in self.archives)
        except (OSError, ValueError, RuntimeError):
            return False

    def _check_source(self, archive):
        stat = archive.path.stat()
        if (stat.st_size, stat.st_mtime_ns) != archive.fingerprint:
            raise ValueError("Shared source PAK changed; rebuild background index.")

    def materialize(self, record):
        """Return a resolved file record; streamed DDS siblings materialize together."""
        from evolve_gameplay_editor import decrypt_entry, decode_cryxml
        from evolve_pak_workspace import normalized_name
        archive = record["_shared_archive"]
        virtual = normalized_name(record["_shared_entry"])
        dest = archive.cache_dir / virtual
        with self._read_lock:
            self._check_source(archive)
            if (dest.is_symlink() or not dest.resolve().is_relative_to(
                    archive.cache_dir.resolve())):
                raise ValueError("Unsafe shared-cache asset path.")
            if True:  # verify streaming siblings even when part 0 is cached
                requested = [virtual]
                match = re.search(r"\.dds\.0$", virtual, re.I)
                if match:
                    base = virtual[:-2].casefold()
                    requested += [
                        key for key in archive.entries
                        if re.fullmatch(re.escape(base) + r"\.\d+", key, re.I)
                        and key != virtual.casefold()
                    ]
                for asset in requested:
                    key = asset.casefold()
                    entry = archive.entries.get(key)
                    if entry is None:
                        continue
                    target = archive.cache_dir / normalized_name(asset)
                    if target.is_symlink() or not target.resolve().is_relative_to(archive.cache_dir.resolve()):
                        raise ValueError('Untrusted shared preview cache path.')
                    if target.is_file():
                        continue
                    raw = decrypt_entry(archive.path, archive.verified_info, entry)
                    if raw.startswith(b"CryXmlB\x00"):
                        tree = decode_cryxml(raw)
                        raw = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
                    if self._retained + len(raw) > MAX_CACHE_BYTES:
                        raise ValueError("Shared preview cache limit reached (512 MiB).")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_name(target.name + ".building")
                    try:
                        with tmp.open("xb") as handle:
                            handle.write(raw)
                        os.replace(tmp, target)
                    finally:
                        tmp.unlink(missing_ok=True)
                    self._retained += len(raw)
            return {"archive": archive.relative, "workspace": None,
                    "relative": virtual, "file": dest}

    def rows(self):
        return self.index


def _default_cache():
    from app_runtime import DATA_HOME
    return DATA_HOME / "Cache" / "SharedReferences"


def warm_shared_catalog(stage, cache_root=None):
    """Background task. Errors are exposed through shared_catalog_status."""
    global _current, _error, _status
    path = Path(stage).resolve()
    cache = Path(cache_root).resolve() if cache_root else _default_cache().resolve()
    key = path / "mykeys" / "public_key.bin"
    try:
        candidate = SharedCatalog(path, cache, key).scan()
        with _lock:
            _current = candidate
            _error = ""
            _status = ("Ready: %d shared PAKs, %d references" %
                       (len(candidate.archives), len(candidate.index)))
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        with _lock:
            _current = None
            _error = str(exc)
            _status = "Unavailable: " + str(exc)[:160]


def start_shared_catalog(stage, cache_root=None):
    """One daemon per stage. Never block GUI startup or editor opening."""
    global _worker, _status
    path = Path(stage).resolve()
    with _lock:
        if _worker and _worker.is_alive():
            return False
        if _current is not None and _current.stage == path and _current.is_current():
            return False
        _status = "Scanning signed shared PAK directories..."
        _worker = threading.Thread(
            target=warm_shared_catalog, args=(path, cache_root),
            daemon=True, name="SharedPAKAssetIndex")
        _worker.start()
    return True


def shared_catalog_status():
    with _lock:
        return _status


def shared_asset_index(stage):
    """Snapshot shared metadata without waiting for an in-progress scan."""
    with _lock:
        if _current is None or _current.stage != Path(stage).resolve():
            return {}
        return {key: list(records) for key, records in _current.index.items()}


def materialize_shared_record(record):
    archive = record.get("_shared_archive")
    if archive is None:
        return record
    with _lock:
        catalog = _current
    if catalog is None or not any(item is archive for item in catalog.archives):
        raise ValueError("Shared index expired; refresh shared assets.")
    return catalog.materialize(record)
