"""Persistent extracted CryPAK Asset Library (read-only reference assets).

A PAK is decrypted ONCE into a managed directory. The library stores loose
virtual-path files, not copies of the encrypted PAK. Original game and editable
project files are never changed. Supports any signed Evolve CryPAK whose
signature matches either the original or custom stage public RSA key.

No automatic eviction: imports are retained until the user removes them.
"""
from __future__ import annotations
from collections import defaultdict, OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET

from app_runtime import DATA_HOME

VERSION = 1
MANIFEST = "asset-library.json"
LIBRARY_HOME = DATA_HOME / "AssetLibrary"
DEFAULT_NAMES = frozenset({
    "characters_hunters_shared_data.pak",
    "characters_hunters_shared_ts.pak",
    "characters_monsters_shared_data.pak",
    "characters_monsters_shared_ts.pak",
    "characters_creatures_shared_data.pak",
    "characters_creatures_shared_ts.pak",
    "animationscaf.pak",
    "animationsconfig.pak",
    "animationsdba.pak",
})
MAX_ARCHIVES = 4096  # opt-in full-game library; imports remain individually bounded
MAX_ENTRIES = 250_000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024 * 1024
_lock = threading.RLock()
_index_cache = OrderedDict()
MAX_INDEX_ROOTS = 4


class ImportCanceled(RuntimeError):
    pass


def _home(root=None):
    return Path(root if root is not None else LIBRARY_HOME).resolve()


def _sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _invalidate():
    with _lock:
        _index_cache.clear()


def _archive_identifier(folder):
    """Keep the original 20-char library ID in readable folder names."""
    match = re.fullmatch(
        r"(?:[A-Za-z0-9][A-Za-z0-9._-]{0,95}__)?([0-9a-f]{20})",
        Path(folder).name)
    return match.group(1) if match else None


def _archive_directory_name(name, identifier):
    """Windows-safe human-readable PAK name with collision-proof stable ID."""
    if not re.fullmatch(r"[0-9a-f]{20}", str(identifier)):
        raise ValueError("Invalid library archive identifier.")
    basename = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip(" ._")[:96]
    if not basename:
        basename = "Imported_PAK"
    return basename + "__" + identifier


def _folder_for_identifier(identifier, root):
    """Resolve a stable identifier across legacy and readable layouts."""
    if not re.fullmatch(r"[0-9a-f]{20}", str(identifier)):
        raise ValueError("Invalid library archive identifier.")
    legacy = root / identifier
    if legacy.is_dir() and not legacy.is_symlink():
        return legacy
    for folder in _manifest_dirs(root):
        if _archive_identifier(folder) == identifier:
            return folder
    return legacy


def migrate_readable_archive_folders(root=None):
    """Rename legacy hash folders safely, without re-extracting any PAK bytes.

    Intended for app startup before model editor paths/indexes are loaded.
    Import IDs remain stable; collisions and inaccessible folders are skipped.
    """
    home = _home(root)
    changed = []
    with _lock:
        for folder in _manifest_dirs(home):
            if folder.name != _archive_identifier(folder):
                continue
            try:
                manifest = _load_manifest(folder)
                dest = home / _archive_directory_name(
                    manifest.get("name", ""), folder.name)
                if dest.exists() or folder.is_symlink():
                    continue
                os.rename(folder, dest)
                changed.append((folder, dest))
            except (OSError, ValueError, KeyError, TypeError):
                # Windows may have a file open; keep the original folder safe.
                continue
        if changed:
            _invalidate()
    return tuple(changed)


def _manifest_dirs(root):
    if not root.is_dir():
        return []
    result = []
    for child in root.iterdir():
        if (child.is_dir() and not child.is_symlink()
                and _archive_identifier(child) is not None
                and (child / MANIFEST).is_file()
                and not (child / MANIFEST).is_symlink()):
            result.append(child)
    if len(result) > MAX_ARCHIVES:
        raise ValueError("Asset Library archive count exceeds the supported limit.")
    return sorted(result)


def _load_manifest(folder):
    data = json.loads((folder / MANIFEST).read_text("utf-8"))
    if data.get("version") != VERSION or not isinstance(data.get("entries"), list):
        raise ValueError("Unsupported Asset Library manifest.")
    if len(data["entries"]) > MAX_ENTRIES:
        raise ValueError("Invalid Asset Library entry count.")
    return data


def imported_archives(root=None):
    root = _home(root)
    result = []
    for folder in _manifest_dirs(root):
        try:
            data = _load_manifest(folder)
        except (OSError, ValueError, KeyError):
            continue
        result.append({
            "id": _archive_identifier(folder),
            "name": str(data.get("name", folder.name)),
            "source": str(data.get("source", "")),
            "source_sha256": str(data.get("source_sha256", "")),
            "entries": len(data["entries"]),
            "bytes": int(data.get("extracted_bytes", 0)),
            "warnings": len(data.get("warnings", [])),
        })
    return tuple(result)


def required_archive_status(root=None, *, build_id=None):
    """Report six shared + three animation PAK coverage, not arbitrary PAK count.

    Only complete imported manifests with readable extracted files count.
    Missing archives and per-PAK skipped files are visible to the user.
    """
    root = _home(root)
    allowed = None
    if build_id is not None:
        from build_collections import catalog
        builds = catalog(root)["builds"]
        if build_id == "all" or build_id not in builds:
            raise ValueError("An explicit build context is required.")
        allowed = set(builds[build_id]["members"])
    found = {}
    for folder in _manifest_dirs(root):
        if allowed is not None and _archive_identifier(folder) not in allowed:
            continue
        try:
            manifest = _load_manifest(folder)
            name = str(manifest.get("name", "")).casefold()
            if name not in DEFAULT_NAMES or not manifest.get("entries"):
                continue
            base = (folder / "files").resolve()
            if not all(
                (file := base.joinpath(*entry["path"].split("/"))).is_file()
                and not file.is_symlink()
                and file.resolve().is_relative_to(base)
                and file.stat().st_size == entry["bytes"]
                for entry in manifest["entries"]
            ):
                continue
            found[name] = {
                "archive": manifest["name"], "files": len(manifest["entries"]),
                "warnings": len(manifest.get("warnings", [])),
                "source": str(manifest.get("source", "")),
            }
        except (OSError, ValueError, KeyError, TypeError):
            continue
    missing = tuple(sorted(DEFAULT_NAMES - found.keys()))
    return {
        "shared": sum(name.startswith("characters_") for name in found),
        "animations": sum(name.startswith("animations") for name in found),
        "present": len(found), "required": len(DEFAULT_NAMES),
        "missing": missing,
        "warnings": sum(x["warnings"] for x in found.values()),
        "complete": not missing, "archives": found,
    }


def covered_stage_archives(candidates, root=None):
    """Return stage-relative companions already fully present as loose files.

    Conservatively match the imported source's actual GAME-relative path,
    not merely an archive basename. Skip incomplete/warning-bearing imports.
    This is called on user-requested unpack, not every frame/asset lookup.
    """
    from evolve_pak_workspace import normalized_name
    target = {normalized_name(path).casefold(): path for path in candidates}
    if not target:
        return set()
    covered = set()
    for folder in _manifest_dirs(_home(root)):
        try:
            manifest = _load_manifest(folder)
            if manifest.get("warnings") or not manifest["entries"]:
                continue
            source = str(manifest.get("source", "")).replace("\\", "/").casefold()
            matches = [rel for rel in target if source.endswith("/" + rel)]
            if not matches:
                continue
            base = (folder / "files").resolve()
            for entry in manifest["entries"]:
                relpath = normalized_name(entry["path"])
                file = base.joinpath(*relpath.split("/"))
                if (file.is_symlink() or not file.is_file()
                        or not file.resolve().is_relative_to(base)
                        or file.stat().st_size != entry["bytes"]):
                    break
            else:
                covered.update(matches)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {target[rel] for rel in covered}


def reference_status(virtual, root=None):
    """Diagnostic: which extracted PAKs actually provide this virtual name."""
    from evolve_pak_workspace import normalized_name
    key = normalized_name(virtual).casefold()
    return tuple(sorted({str(x["archive"]) for x in
                         extracted_index(root).get(key, ())}))


def extracted_index(root=None):
    """Read-only catalog of extracted loose files; never decrypts at lookup."""
    home = _home(root)
    folders = _manifest_dirs(home)
    stamp = tuple((folder.name, (folder / MANIFEST).stat().st_mtime_ns)
                  for folder in folders)
    cache_key = (str(home), stamp)
    with _lock:
        cached = _index_cache.get(str(home))
        if cached is not None and cached[0] == cache_key:
            _index_cache.move_to_end(str(home))
            return {key: list(records) for key, records in cached[1].items()}
    from evolve_pak_workspace import normalized_name
    index = defaultdict(list)
    for folder in folders:
        try:
            manifest = _load_manifest(folder)
            base = (folder / "files").resolve()
            for entry in manifest["entries"]:
                virtual = normalized_name(entry["path"])
                if entry.get("bytes", -1) < 0:
                    continue
                source_path = base.joinpath(*virtual.split("/"))
                if (source_path.is_symlink() or not source_path.is_file()
                        or not source_path.resolve().is_relative_to(base)
                        or source_path.stat().st_size != entry["bytes"]):
                    continue
                index[virtual.casefold()].append({
                    "archive": manifest["name"], "workspace": None,
                    "relative": virtual, "file": source_path,
                    "_asset_library": _archive_identifier(folder),
                })
        except (OSError, ValueError, KeyError, TypeError):
            # Partial/damaged imports cannot silently provide assets.
            continue
    result = dict(index)
    with _lock:
        _index_cache[str(home)] = (cache_key, result)
        _index_cache.move_to_end(str(home))
        while len(_index_cache) > MAX_INDEX_ROOTS:
            _index_cache.popitem(last=False)
    return {key: list(records) for key, records in result.items()}


def _keys(stage=None, extra_keys=()):
    from shared_pak_cache import _original_public_key
    keys = []
    if stage:
        stage = Path(stage).resolve()
        for key in (stage / "mykeys" / "public_key.bin",
                    _original_public_key(stage)):
            if key and key.is_file() and key not in keys:
                keys.append(key)
    for key in extra_keys:
        key = Path(key).resolve()
        if key.is_file() and key not in keys:
            keys.append(key)
    if not keys:
        raise FileNotFoundError(
            "No Evolve PAK verification key found; complete manager setup first.")
    return tuple(keys)


def _verify_pak(path, keys):
    from evolve_pak_rekey import extract_plain_cdr
    problems = []
    for key in keys:
        try:
            info, _, _, basename = extract_plain_cdr(path, key)
            if not info.get("entries"):
                raise ValueError("PAK contains no files.")
            return info, key, basename
        except (OSError, ValueError) as exc:
            problems.append(type(exc).__name__ + ": " + str(exc)[:100])
        except Exception as exc:
            from cryptography.exceptions import InvalidSignature
            if isinstance(exc, InvalidSignature):
                problems.append("RSA signature mismatch with " + key.name)
            else:
                raise
    raise ValueError(
        path.name + " is not a verified compatible Evolve PAK. " +
        "; ".join(problems[:3]))


def import_pak(source, *, stage=None, root=None, extra_keys=(),
               progress=None, cancelled=None, build_id=None, relative_archive_path=None):
    """Extract entire verified PAK as loose references into AssetLibrary.

    This does not create a modding workspace or require repacking. A unique
    digest-derived directory is populated atomically; failed imports are
    discarded, never indexed. All input archives are treated as untrusted.
    """
    from evolve_pak_workspace import normalized_name
    from evolve_gameplay_editor import decrypt_entry, decode_cryxml
    root = _home(root)
    def finish(result):
        if build_id is not None:
            from build_collections import assign_archive
            assign_archive(build_id, result["id"], root=root,
                           relative_path=relative_archive_path)
        return result
    supplied = Path(source).expanduser()
    if supplied.is_symlink():
        raise ValueError("Linked PAK sources are not accepted.")
    source = supplied.resolve(strict=True)
    if not source.is_file() or source.suffix.casefold() != ".pak":
        raise ValueError("Select an actual, non-linked .pak file.")
    if source.is_relative_to(root):
        raise ValueError("Import source must be outside the Asset Library.")
    if cancelled and cancelled():
        raise ImportCanceled("Import canceled.")
    # Fast, persistent idempotence check on launch. Do not rehash/decrypt large
    # unchanged PAKs after a successful first import.
    stat = source.stat()
    for prior in _manifest_dirs(root):
        try:
            prior_manifest = _load_manifest(prior)
            if (prior_manifest.get("source") == str(source) and
                    prior_manifest.get("source_size") == stat.st_size and
                    prior_manifest.get("source_mtime_ns") == stat.st_mtime_ns):
                if all((prior / "files" / entry["path"]).is_file()
                       and (prior / "files" / entry["path"]).stat().st_size
                       == entry["bytes"] for entry in prior_manifest["entries"]):
                    return finish({"status": "already-imported", "id": _archive_identifier(prior),
                            "name": source.name, "entries": len(prior_manifest["entries"])})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    sha = _sha_file(source)
    slug = hashlib.sha256((source.name.casefold() + "|" + sha).encode()).hexdigest()[:20]
    if build_id is not None and relative_archive_path:
        from build_collections import catalog
        from historical_zip import is_ordinary_zip
        relative = normalized_name(relative_archive_path.replace("\\", "/"))
        old = catalog(root)["builds"][build_id]["members"].get(slug)
        if old and old["relative_path"] != relative and is_ordinary_zip(source):
            # Identical historical PAKs can occur at multiple level paths.
            # Keep both locations without overwriting an existing membership.
            slug = hashlib.sha256((source.name.casefold() + "|" + sha +
                                   "|" + relative).encode()).hexdigest()[:20]
    # Preserve a previously imported legacy hash folder until explicitly
    # migrated; all new imports use the readable PAK name.
    legacy = root / slug
    target = (legacy if legacy.exists() else
              root / _archive_directory_name(source.name, slug))
    if target.is_dir() and (target / MANIFEST).is_file():
        manifest = _load_manifest(target)
        if manifest.get("source_sha256") == sha:
            base = target / "files"
            if all((base / entry["path"]).is_file() and
                   (base / entry["path"]).stat().st_size == entry["bytes"]
                   for entry in manifest["entries"]):
                return finish({"status": "already-imported", "id": slug,
                        "name": source.name, "entries": len(manifest["entries"])})
            raise ValueError("Cached extraction has missing or damaged files. "
                             "Remove this archive in Asset Library and re-import it.")
    if target.exists():
        raise ValueError("An incomplete/conflicting library import exists: " + slug)
    if len(_manifest_dirs(root)) >= MAX_ARCHIVES:
        raise ValueError("Too many imported PAKs in the library.")
    from historical_zip import HistoricalZipFile, is_ordinary_zip
    plain_zip = is_ordinary_zip(source)
    if plain_zip:
        from legacy_pak_workspace import inspect_archive
        inventory = inspect_archive(source, allow_empty=True)
        info = {"entries": {e["archive_name"]: {
            "original_size": e["size"], "method": e["method"], "extra_length": 0
        } for e in inventory["entries"]}}
        key, signed_name = None, source.name
    else:
        # Signed CryPAKs still require their original cryptographic verification.
        info, key, signed_name = _verify_pak(source, _keys(stage, extra_keys))
    if len(info["entries"]) > MAX_ENTRIES:
        raise ValueError("Archive has too many entries.")
    estimated = sum(max(0, entry.get("original_size", 0))
                    for entry in info["entries"].values())
    if estimated > MAX_TOTAL_BYTES:
        raise ValueError("PAK exceeds the 100 GiB extraction safety limit.")
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < estimated + 128 * 1024 * 1024:
        raise OSError("Not enough free disk space to extract " + source.name)
    work = root / (slug + ".importing")
    if work.exists():
        raise FileExistsError("Import already in progress for " + source.name)
    records, skipped, written = [], [], 0
    archive = None
    work.mkdir(parents=True)
    try:
        archive = HistoricalZipFile(source) if plain_zip else None
        base = work / "files"
        seen = set()
        count = len(info["entries"])
        for position, (name, meta) in enumerate(info["entries"].items(), 1):
            if cancelled and cancelled():
                raise ImportCanceled("Import canceled.")
            virtual = normalized_name(name)
            token = virtual.casefold()
            if token in seen:
                raise ValueError("Duplicate case-insensitive PAK entry: " + virtual)
            seen.add(token)
            length = int(meta.get("original_size", -1))
            if (meta.get("method") not in ((0, 8) if plain_zip else (13, 14)) or
                    meta.get("extra_length") != 0 or length < 0 or
                    length > MAX_ENTRY_BYTES):
                if plain_zip:
                    raise ValueError("Unsupported historical entry size/method: " + virtual)
                skipped.append(virtual + ": unsupported size/method")
                continue
            data = archive.read(name) if plain_zip else decrypt_entry(source, info, name)
            converted = False
            if data.startswith(b"CryXmlB\x00"):
                element = decode_cryxml(data)
                data = ET.tostring(element, encoding="utf-8", xml_declaration=True)
                converted = True
            destination = base.joinpath(*virtual.split("/"))
            if not destination.parent.resolve().is_relative_to(base.resolve()):
                raise ValueError("Unsafe archive output path.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(data)
            written += len(data)
            records.append({"path": virtual, "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "cryxml": converted})
            if progress and (position == 1 or position % 50 == 0 or position == count):
                progress(source.name, position, count)
        if not records:
            if plain_zip:
                skipped.append("Empty historical ZIP archive; no content to preview.")
            else:
                raise ValueError("No supported PAK entries could be extracted.")
        if _sha_file(source) != sha:
            raise RuntimeError("PAK changed during extraction; import discarded.")
        manifest = {
            "version": VERSION, "name": source.name,
            "signed_name": signed_name, "source": str(source),
            "source_sha256": sha, "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "public_key_sha256": _sha_file(key) if key is not None else None,
            "archive_format": "ordinary-zip" if plain_zip else "signed-crypak",
            "verification": "crc32" if plain_zip else "rsa-pss",
            "imported_at": int(time.time()), "extracted_bytes": written,
            "warnings": skipped, "entries": records,
        }
        (work / MANIFEST).write_text(json.dumps(manifest, indent=2), "utf-8")
        os.replace(work, target)
        _invalidate()
        return finish({"status": "imported", "id": slug, "name": source.name,
                "entries": len(records), "bytes": written, "skipped": len(skipped)})
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        if archive is not None:
            archive.close()


def installed_defaults(game):
    """Find only the nine known defaults, not every large game archive."""
    game = Path(game).resolve() if game else None
    if not game or not (game / "bin64_SteamRetail" / "Evolve.exe").is_file():
        return ()
    result = {}
    for folder, dirs, files in os.walk(game, followlinks=False):
        dirs[:] = [name for name in dirs
                   if not (Path(folder) / name).is_symlink()]
        for name in files:
            if name.casefold() in DEFAULT_NAMES:
                path = Path(folder) / name
                if not path.is_symlink() and path.is_file():
                    result.setdefault(name.casefold(), path)
    return tuple(result[k] for k in sorted(result))


def verify_library(root=None, *, progress=None, cancelled=None):
    """Full hash verification; never silently repairs a user's extracted files."""
    home = _home(root)
    report = {"archives": 0, "files": 0, "corrupt": [], "missing": []}
    from evolve_pak_workspace import normalized_name
    for folder in _manifest_dirs(home):
        if cancelled and cancelled():
            raise ImportCanceled("Verification canceled.")
        manifest = _load_manifest(folder)
        report["archives"] += 1
        base = (folder / "files").resolve()
        for entry in manifest["entries"]:
            path = base.joinpath(*normalized_name(entry["path"]).split("/"))
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(base):
                report["missing"].append(manifest["name"] + "/" + entry["path"])
            elif path.stat().st_size != entry["bytes"] or _sha_file(path) != entry["sha256"]:
                report["corrupt"].append(manifest["name"] + "/" + entry["path"])
            report["files"] += 1
        if progress:
            progress(manifest["name"], report["files"], report["files"])
    return report


def remove_archive(identifier, root=None):
    """Explicit user action only; operates solely on owned manifest directories."""
    home = _home(root)
    if not re.fullmatch("[0-9a-f]{20}", str(identifier)):
        raise ValueError("Invalid library archive identifier.")
    from build_collections import catalog
    memberships = [name for name, build in catalog(home)["builds"].items()
                   if name != "unassigned" and identifier in build["members"]]
    if memberships:
        raise ValueError("Archive belongs to a registered build; remove memberships first.")
    folder = _folder_for_identifier(identifier, home)
    if folder.is_symlink() or not folder.is_dir() or not (folder / MANIFEST).is_file():
        raise ValueError("Selected library archive no longer exists.")
    # Reject unexpected links before cleanup.
    for base, dirs, files in os.walk(folder, followlinks=False):
        if any((Path(base) / name).is_symlink() for name in dirs + files):
            raise ValueError("Library contains a linked path; manual review required.")
    shutil.rmtree(folder)
    _invalidate()
