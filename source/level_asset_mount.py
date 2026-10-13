"""Map-scoped asset library mounts for extracted Evolve level workspaces.

Compiled level and terrain archives contain root-relative virtual paths shared
by every map. Never allow an unrelated map's brush/terrain data to win a lookup.
This is read-only; source PAKs and imported library manifests are immutable.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path, PurePosixPath
import re

ROOT_LEVEL_NAMES = frozenset(("leveldata.xml", "levelinfo.xml", "mission_mission0.xml"))
ROOT_LEVEL_PREFIXES = ("brush/", "terrain/", "mergedmesh/", "nav/", "ai/")
MAX_IMPORTED = 4096


def level_from_source(source):
    """Return the map identifier from the original archive path, not its filename."""
    parts = str(source or "").replace("\\", "/").lower().split("/")
    match = re.fullmatch(r"levels_([a-z0-9_-]{1,80})\.pak", parts[-1])
    if match:
        return match.group(1)
    for i, part in enumerate(parts[:-1]):
        if part == "levels" and i + 1 < len(parts) - 1:
            name = parts[i + 1].strip()
            if re.fullmatch(r"[a-z0-9_-]{1,80}", name):
                return name
    return None


def map_relative_asset(virtual):
    """Root-relative compiled archive records MUST have a per-map owner."""
    path = str(virtual).replace("\\", "/").casefold().lstrip("/")
    return (path in ROOT_LEVEL_NAMES
            or path.startswith(ROOT_LEVEL_PREFIXES)
            or bool(re.fullmatch(r"tile\d+_\d+\.raw", path)))


def _manifest_source(manifest):
    return (manifest.get("source") or manifest.get("source_pak")
            or manifest.get("archive_source") or "")


def mount_map_index(workspace, map_name, *, library_root=None, cache_root=None):
    """Return a normal full-path lookup index with map-local collision fencing.

    The editable project is first, then only archives whose *original PAK
    source path* identifies the selected map, then global shared assets.
    Ambiguous different global files remain ambiguous at the resolver layer.
    """
    from evolve_pak_workspace import load_workspace, normalized_name
    from asset_library import extracted_index, _home, _manifest_dirs, _load_manifest
    base = Path(workspace).resolve(strict=True)
    root = (base / "files").resolve(strict=True)
    selected = str(map_name).casefold()
    imported = defaultdict(list)
    roots = [_home(library_root)]
    if cache_root is not None and _home(cache_root) not in roots:
        roots.append(_home(cache_root))
    folders = [folder for library in roots for folder in _manifest_dirs(library)]
    if len(folders) > MAX_IMPORTED:
        raise ValueError("Imported archive limit exceeded.")
    matching_archives = set()
    sources = {}
    for folder in folders:
        try:
            manifest = _load_manifest(folder)
            source = _manifest_source(manifest)
            owner = level_from_source(source)
            sources[str(folder.resolve())] = owner
            if owner == selected:
                matching_archives.add(str(folder.resolve()))
        except (ValueError, OSError, KeyError, TypeError):
            continue
    indexes = [extracted_index(library) for library in roots]
    for original_index in indexes:
      for key, records in original_index.items():
          is_scoped = map_relative_asset(key)
          for entry in records:
              try:
                  source_dir = str(Path(entry["file"]).resolve().parent)
                  # Indexed records live under <archive>/files/<virtual>.
                  archive_dir = Path(entry["file"]).resolve()
                  relative = str(entry.get("relative", "")).replace("\\", "/")
                  for _ in PurePosixPath(relative).parts:
                      archive_dir = archive_dir.parent
                  archive_dir = archive_dir.parent
                  mount = str(archive_dir)
                  owner = sources.get(mount)
                  if is_scoped:
                      if owner == selected:
                          imported[key].append(dict(entry, map_owner=owner))
                  elif owner is None or owner == selected:
                      imported[key].append(dict(entry, map_owner=owner))
              except (OSError, KeyError, TypeError, ValueError):
                  continue
    for entry in load_workspace(base)["entries"]:
        virtual = normalized_name(entry["path"])
        file = root.joinpath(*virtual.split("/"))
        if (file.is_file() and not file.is_symlink()
                and file.resolve().is_relative_to(root)):
            imported[virtual.casefold()].insert(0, {
                "file": file, "relative": virtual, "workspace": base,
                "archive": "editable project",
            })
    return dict(imported), {"map": selected, "mounts": len(matching_archives),
                             "indexed_paths": len(imported)}


def resolve_scoped(index, virtual, workspace=None):
    """Reject ambiguous model bytes instead of picking an arbitrary archive."""
    from multi_pak_assets import _resolve
    if map_relative_asset(virtual):
        # The index was already scoped by original archive source.
        pass
    hits, state = _resolve(index, virtual, preferred_workspace=workspace)
    if state != "found":
        return None, state
    record = hits[0]
    file = Path(record["file"])
    if not file.is_file() or file.is_symlink():
        return None, "missing"
    return record, "found"
