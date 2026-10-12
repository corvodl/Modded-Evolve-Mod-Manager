"""Read-only discovery of signed map and shared PAK providers.

Missing level assets are extracted only through the verified Asset Library
importer, into a *separate* managed dependency cache. No game/archive edits.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from asset_library import _keys, _verify_pak, import_pak, imported_archives
from asset_library_bulk import discover_game_paks
from evolve_pak_workspace import normalized_name
from level_asset_mount import level_from_source

MAX_PROVIDER_ARCHIVES = 4096


def _virtual(path):
    return normalized_name(str(path).replace("\\", "/")).casefold()


def map_archive_catalog(sources):
    """Group the three common forms of map PAK by canonical map ID."""
    found = defaultdict(list)
    for source in sources:
        name = level_from_source(source)
        if name:
            found[name].append(Path(source))
    return {name: tuple(sorted(files, key=lambda f: f.as_posix().casefold()))
            for name, files in sorted(found.items())}


def needed_virtual_paths(project):
    """Find exact scene references, without basename or guessed PAK matching."""
    result = set()
    for item in project.scene:
        for path in (getattr(item, "model", ""), getattr(item, "material", "")):
            if not path:
                continue
            try:
                result.add(_virtual(path))
            except (ValueError, TypeError):
                continue
    return frozenset(result)


def providers_for_missing(sources, missing, *, stage, cancel=None, progress=None):
    """Return verified candidate PAKs for exact virtual names.

    Other maps are completely excluded (including their root-relative files).
    Ambiguous shared providers are intentionally returned rather than guessed.
    """
    keys = _keys(stage)
    indexed = defaultdict(list)
    candidates = list(sources)
    if len(candidates) > MAX_PROVIDER_ARCHIVES:
        raise ValueError("Too many archives for bounded dependency discovery.")
    target = set(missing)
    for offset, source in enumerate(candidates):
        if cancel is not None and cancel.is_set():
            return None
        if level_from_source(source) is not None:
            continue
        if progress:
            progress("Scanning verified " + Path(source).name, offset, len(candidates))
        try:
            info, _, _ = _verify_pak(Path(source), keys)
            present = set()
            for name in info["entries"]:
                try:
                    key = _virtual(name)
                except (ValueError, TypeError):
                    continue
                if key in target:
                    present.add(key)
            for name in sorted(present):
                indexed[name].append(Path(source))
        except (OSError, ValueError):
            continue  # Never use an archive that failed signature verification.
    return dict(indexed)


def ensure_map_archives(project, game, stage, cache_root, *,
                        imported_root=None, cancel=None, progress=None):
    """Import selected-map PAKs and *required* shared providers, on a worker.

    Returns a bounded diagnostic dictionary. An unavailable game or key leaves
    already extracted assets usable; no pretend 'fully loaded' status.
    """
    result = {"imported": [], "reused": [], "missing": [], "ambiguous": [],
              "errors": [], "map_archives": [], "scanned": 0}
    if game is None or stage is None:
        result["errors"].append("Game installation or verification stage not configured")
        return result
    scan = discover_game_paks(game, library_root=imported_root)
    catalog = map_archive_catalog(scan.files)
    selection = catalog.get(str(project.level_name).casefold(), ())
    result["map_archives"] = [p.name for p in selection]
    prior = {str(p["source"]) for root in (imported_root, cache_root)
             for p in imported_archives(root)}
    def imported(source):
        if cancel is not None and cancel.is_set():
            return False
        if progress:
            progress("Importing " + source.name, 0, 1)
        try:
            if str(source) in prior:
                result["reused"].append(source.name)
            else:
                state = import_pak(source, stage=stage, root=cache_root,
                                   cancelled=cancel.is_set if cancel else None)
                result["imported" if state["status"] == "imported" else "reused"].append(source.name)
                prior.add(str(source))
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            result["errors"].append(source.name + ": " + str(exc)[:180])
            return False
    for source in selection:
        if not imported(source) and cancel is not None and cancel.is_set():
            return result
    if cancel is not None and cancel.is_set():
        return result
    from level_asset_mount import mount_map_index
    index, _ = mount_map_index(project.workspace, project.level_name,
                               library_root=imported_root, cache_root=cache_root)
    missing = needed_virtual_paths(project) - set(index)
    if not missing:
        return result
    others = [p for p in scan.files if str(p) not in prior
              and level_from_source(p) is None]
    result["scanned"] = len(others)
    try:
        provider_map = providers_for_missing(others, missing, stage=stage,
                                             cancel=cancel, progress=progress)
    except (OSError, ValueError) as exc:
        result["errors"].append("Provider inventory: " + str(exc)[:180])
        return result
    if provider_map is None:
        return result
    chosen = set()
    for virtual in sorted(missing):
        providers = provider_map.get(virtual, [])
        if len(providers) == 1:
            chosen.add(providers[0])
        elif len(providers) > 1:
            result["ambiguous"].append(
                virtual + " from " + ", ".join(str(p) for p in providers[:8]))
        else:
            result["missing"].append(virtual)
    for source in sorted(chosen, key=lambda p:p.as_posix().casefold()):
        if cancel is not None and cancel.is_set():
            break
        imported(source)
    return result
