"""Resolve a single CryEngine CAF on Play, without extracting entire PAKs.

Called only in a background conversion worker.  Prefer exact user-extracted
files, then signed custom-stage archives, then original signed game archives.
Every candidate is verified by the existing PAK signer/CryPAK implementation.
The result is a cached read-only file outside the editable project.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading

from character_animation_catalog import AnimationReference, caf_header
from evolve_pak_workspace import normalized_name

_LOCK = threading.RLock()
_CATALOGS = {}
_MAX_CATALOGS = 8


def _exact_loose(index, virtual, preferred_workspace=None):
    """Only inspect paths already extracted; do not decrypt while listing."""
    preferred = Path(preferred_workspace).resolve() if preferred_workspace else None
    matches = []
    for record in index.get(virtual.casefold(), ()):
        if "_shared_archive" in record:
            archive = record["_shared_archive"]
            entry = record["_shared_entry"]
            file = archive.cache_dir / normalized_name(entry)
            # Existing cached CAF bytes belong to a specific signed PAK
            # generation; never replay an obsolete cache after game repair.
            try:
                stat = archive.path.stat()
                if (stat.st_size, stat.st_mtime_ns) != archive.fingerprint:
                    continue
                if not file.resolve().is_relative_to(archive.cache_dir.resolve()):
                    continue
            except (OSError, ValueError):
                continue
        else:
            file = record.get("file")
        if not file:
            continue
        file = Path(file)
        if not file.is_file() or file.is_symlink():
            continue
        # Editable workspace wins over cache copies of the same virtual asset.
        is_local = (preferred is not None and
                    record.get("workspace") is not None and
                    Path(record["workspace"]).resolve() == preferred)
        matches.append((is_local, file))
    local = [file for local, file in matches if local]
    if len(local) == 1:
        return local[0]
    if len(matches) == 1:
        return matches[0][1]
    # Import All Game PAKs can expose the same virtual CAF through multiple
    # archives. Do not call it "missing" merely because the index has more
    # than one owner. Only resolve duplicates when their bytes are identical;
    # conflicting copies remain ambiguous instead of guessing.
    pool = local if local else [file for _, file in matches]
    if len(pool) > 1:
        import hashlib
        digests = set()
        for file in pool:
            h = hashlib.sha256()
            try:
                with file.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(chunk)
            except OSError:
                return None
            digests.add(h.digest())
            if len(digests) > 1:
                return None
        return pool[0]
    return None


def _scoped_archives(stage, virtual, game=None):
    """Only animation, shared, and the selected character's PAK family."""
    from shared_pak_cache import REFERENCE_ARCHIVES
    names = set(REFERENCE_ARCHIVES)
    parts = virtual.casefold().split("/")
    if len(parts) >= 5 and parts[0] == "characters":
        character = parts[2]
        from shared_pak_cache import _archive_candidates
        try:
            for rel in _archive_candidates(stage, (parts[1], character)):
                names.add(Path(rel).name.casefold())
        except (OSError, ValueError, RuntimeError):
            pass
        # Original game PAKs can exist without a matching custom-signed stage.
        # Include just the exact character family, not every game archive.
        if game:
            root = Path(game).resolve() / "Game"
            if root.is_dir():
                prefix = ("characters_" + parts[1] + "_" + character)
                for path in root.glob("*.pak"):
                    stem = path.stem.casefold()
                    if (not path.is_symlink() and
                            (stem == prefix or stem.startswith(prefix + "_"))):
                        names.add(path.name.casefold())
    return frozenset(names)


def _catalog(stage, game, names, original):
    from shared_pak_cache import SharedCatalog, _default_cache, _original_public_key
    import hashlib

    stage = Path(stage).resolve()
    game = Path(game).resolve() if game else None
    if original:
        if game is None:
            return None
        key = _original_public_key(stage)
        if key is None:
            return None
        installed = game
    else:
        key = stage / "mykeys" / "public_key.bin"
        installed = None
    if not key.is_file():
        return None
    # Separate independent cache roots for the two signing domains and each
    # selected game installation; no files are written into installed Evolve.
    identity = hashlib.sha256(
        (str(stage) + "|" + str(game) + "|" + str(sorted(names)) +
         "|" + str(original)).encode("utf-8")).hexdigest()[:20]
    bucket = _default_cache().parent / "AnimationOnDemand" / identity
    cache_key = (str(stage), str(game), names, original)
    with _LOCK:
        saved = _CATALOGS.get(cache_key)
        if saved is not None and saved.is_current():
            return saved
        instance = SharedCatalog(
            stage, bucket, key, archive_names=names,
            installed_game=installed).scan()
        if len(_CATALOGS) >= _MAX_CATALOGS:
            _CATALOGS.pop(next(iter(_CATALOGS)))
        _CATALOGS[cache_key] = instance
        return instance


def _materialize_one(catalog, virtual):
    """Resolve exact virtual path, never a guessed CAF of another skeleton."""
    records = catalog.index.get(virtual.casefold(), ())
    if not records:
        return None
    # Multiple genuinely different files at the same virtual path must not
    # be guessed; exact duplicate results are acceptable.
    from multi_pak_assets import _content_fingerprint
    paths = []
    for record in records:
        result = catalog.materialize(record)
        path = Path(result["file"])
        if not caf_header(path):
            raise ValueError(
                "The verified PAK contains an unsupported CAF header: " + virtual)
        paths.append(path)
    if len(paths) > 1 and len({_content_fingerprint(path) for path in paths}) != 1:
        raise ValueError("Conflicting signed PAK entries for " + virtual)
    return paths[0]


def resolve_caf_for_play(reference, *, stage_root, game_root=None,
                         asset_index=None, preferred_workspace=None,
                         loose_index=None):
    """Return (resolved AnimationReference, extracted_now, origin).

    The caller must run this OFF the Qt GUI thread.  Metadata/index lookup
    does not touch installed game files, except for read-only PAK decryption.
    """
    if not isinstance(reference, AnimationReference) or reference.kind != "caf":
        raise ValueError("Choose a CAF animation to play.")
    virtual = normalized_name(reference.virtual_path)
    if not virtual.casefold().endswith(".caf"):
        raise ValueError("The selected animation is not a CAF.")
    if reference.available and reference.file:
        candidate = Path(reference.file)
        if candidate.is_file() and not candidate.is_symlink() and caf_header(candidate):
            return reference, False, "existing extraction"
    if loose_index:
        candidate = loose_index.get(virtual.casefold())
        if candidate and Path(candidate).is_file() and caf_header(candidate):
            return replace(reference, available=True, file=Path(candidate)), False, "selected animation folder"
    asset_index = asset_index or {}
    candidate = _exact_loose(asset_index, virtual, preferred_workspace)
    if candidate is not None and caf_header(candidate):
        return replace(reference, available=True, file=candidate), False, "extracted project/cache"
    # The persistent full-game library can finish importing after the viewer
    # was opened. Re-read it at Play time before touching signed archives.
    from asset_library import extracted_index
    fresh_library = extracted_index()
    candidate = _exact_loose(fresh_library, virtual, preferred_workspace)
    if candidate is not None and caf_header(candidate):
        return replace(reference, available=True, file=candidate), False, "Asset Library"
    # Source indexes may finish AFTER the model was opened. Refresh metadata
    # on demand; do not depend on whatever snapshot the editor first loaded.
    from shared_pak_cache import shared_asset_index
    candidates = shared_asset_index(stage_root)
    candidate = _exact_loose(candidates, virtual, preferred_workspace)
    if candidate is not None and caf_header(candidate):
        return replace(reference, available=True, file=candidate), False, "preview cache"
    names = _scoped_archives(stage_root, virtual, game_root)
    from cryptography.exceptions import InvalidSignature
    errors = []
    for original in (False, True):
        try:
            catalog = _catalog(stage_root, game_root, names, original)
            if catalog is None:
                continue
            path = _materialize_one(catalog, virtual)
            if path is not None:
                return (replace(reference, available=True, file=path), True,
                        "original game PAK" if original else "staged signed PAK")
        except (OSError, ValueError, RuntimeError, InvalidSignature) as exc:
            errors.append((str(exc) or 'archive signature mismatch')[:180])
    message = ("The exact CAF was not found in verified animation / character "
               "PAKs. Check the installed EvolveGame and selected animation folder.")
    if errors:
        message += " (" + "; ".join(errors[:2]) + ")"
    raise FileNotFoundError(virtual + ": " + message)
