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
_scoped_catalogs = {}
_scoped_workers = {}
_scoped_status = {}
_scoped_revision = 0
MAX_CHARACTER_CATALOGS = 12


@dataclass
class _Archive:
    path: Path
    relative: str
    fingerprint: tuple
    entries: dict
    verified_info: object
    cache_dir: Path


class SharedCatalog:
    def __init__(self, stage, cache_root, key, archive_names=None, installed_game=None):
        self.stage = Path(stage).resolve()
        self.cache_root = Path(cache_root).resolve()
        self.key = Path(key).resolve()
        self.archive_names = SHARED_NAMES if archive_names is None else frozenset(n.casefold() for n in archive_names)
        self.archives = []
        self.index = defaultdict(list)
        self._read_lock = threading.RLock()
        self._retained = 0
        self.installed_game = Path(installed_game).resolve() if installed_game else None
        self.warnings = []

    def _candidate_files(self):
        if self.installed_game is None:
            from universal_stage import allowed_paks
            base = (self.stage / "paks").resolve()
            return [(rel, base / rel) for rel in allowed_paks(self.stage).values()
                    if Path(rel).name.casefold() in self.archive_names]
        root = self.installed_game
        if not (root / "bin64_SteamRetail" / "Evolve.exe").is_file():
            raise ValueError("An installed EvolveGame folder is required.")
        result = []
        visited = 0
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [n for n in dirs if not (Path(folder) / n).is_symlink()]
            visited += 1
            if visited > 6000:
                raise ValueError("Shared asset directory scan limit exceeded.")
            for name in files:
                if name.casefold() not in self.archive_names:
                    continue
                path = Path(folder) / name
                if path.is_file() and not path.is_symlink():
                    result.append((path.relative_to(root).as_posix(), path))
                if len(result) > 48:
                    raise ValueError("Unexpected number of shared PAK copies.")
        return result

    def scan(self):
        from universal_stage import allowed_paks
        from evolve_pak_rekey import extract_plain_cdr
        from evolve_pak_workspace import normalized_name
        stage_paks = (self.stage / "paks").resolve()
        allowed = allowed_paks(self.stage)
        names = [p for p in allowed.values()
                 if Path(p).name.casefold() in self.archive_names]
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
                            if Path(rel).name.casefold() in self.archive_names)
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
        scoped = [key for key, state in _scoped_status.items()
                  if state.startswith('Ready:')]
        return _status + (' | Character catalogs: %d (rev %d)' %
                           (len(scoped), _scoped_revision) if _scoped_revision else '')


def _character_folder(relative):
    """Return the character asset path segment, not a guessed animation name."""
    from evolve_pak_workspace import normalized_name
    parts = normalized_name(relative).casefold().split('/')
    if len(parts) < 4 or parts[0] != 'characters' or parts[1] not in (
            'hunters', 'monsters', 'creatures'):
        return None
    if parts[2] == 'shared':
        return None
    return parts[1], parts[2]


def _archive_candidates(stage, character):
    """Only archive basenames clearly referring to this character."""
    from universal_stage import allowed_paks
    aliases = {'merc_tech': ('merc_tech', 'kala')}
    tokens = aliases.get(character[1], (character[1],))
    names = []
    for rel in allowed_paks(Path(stage).resolve()).values():
        stem = Path(rel).stem.casefold().replace('-', '_')
        if any(token in stem for token in tokens):
            names.append(rel)
    return names


def warm_character_catalog(stage, character, selected_archives, cache_root=None):
    """Worker: stage-signed metadata scan for a character's own archive(s)."""
    global _scoped_revision
    stage = Path(stage).resolve()
    root = (Path(cache_root) if cache_root else _default_cache().parent /
            'CharacterReferences').resolve()
    # Each character uses its own cache root so scans cannot evict each other.
    slug = hashlib.sha256((str(stage).casefold() + str(character)).encode()).hexdigest()[:20]
    try:
        candidate = SharedCatalog(
            stage, root / slug, stage / 'mykeys' / 'public_key.bin',
            archive_names={Path(name).name.casefold() for name in selected_archives}
        ).scan()
        with _lock:
            _scoped_catalogs[(str(stage), character)] = candidate
            _scoped_status[(str(stage), character)] = (
                'Ready: %d character PAKs / %d paths' %
                (len(candidate.archives), len(candidate.index)))
            _scoped_revision += 1
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        with _lock:
            _scoped_status[(str(stage), character)] = 'Unavailable: ' + str(exc)[:150]
            _scoped_revision += 1


def start_character_catalog(stage, selected):
    """Nonblocking supplemental index for character-specific CAFs and textures."""
    global _scoped_revision
    character = _character_folder(selected)
    if character is None:
        return False
    stage = Path(stage).resolve()
    key = (str(stage), character)
    with _lock:
        live = _scoped_catalogs.get(key)
        if live is not None and live.is_current():
            return False
        if _scoped_workers.get(key) is not None and _scoped_workers[key].is_alive():
            return False
        if key not in _scoped_catalogs and len(_scoped_catalogs) >= MAX_CHARACTER_CATALOGS:
            _scoped_status[key] = 'Catalog limit reached; restart to index other characters.'
            return False
        archives = _archive_candidates(stage, character)
        if not archives:
            _scoped_status[key] = (
                'No matching staged package for ' + '/'.join(character))
            _scoped_revision += 1
            return False
        _scoped_status[key] = 'Scanning ' + '/'.join(character)
        worker = threading.Thread(
            target=warm_character_catalog,
            args=(stage, character, tuple(archives)), daemon=True,
            name='CharacterPAKAssetIndex')
        _scoped_workers[key] = worker
        worker.start()
        return True


def shared_asset_index(stage):
    """Snapshot shared metadata without waiting for an in-progress scan."""
    with _lock:
        stage = Path(stage).resolve()
        combined = defaultdict(list)
        if _current is not None and _current.stage == stage:
            for key, records in _current.index.items():
                combined[key].extend(records)
        for (path, _character), catalog in _scoped_catalogs.items():
            if path == str(stage):
                for key, records in catalog.index.items():
                    combined[key].extend(records)
        return dict(combined)


def materialize_shared_record(record):
    archive = record.get("_shared_archive")
    if archive is None:
        return record
    with _lock:
        catalog = _current
    if catalog is not None and any(item is archive for item in catalog.archives):
        return catalog.materialize(record)
    with _lock:
        owners = tuple(_scoped_catalogs.values())
    for owner in owners:
        if any(item is archive for item in owner.archives):
            return owner.materialize(record)
    raise ValueError("Signed asset index expired; refresh model assets.")
