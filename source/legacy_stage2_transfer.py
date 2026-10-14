"""Read-only Legacy -> Stage 2 compiled-map research package.

This prepares and inventories assets from *registered* build snapshots. It
does not compile terrain, create PAKs, register maps, change stage files, or
enable retired game modes. Map-local virtual names must never be satisfied by
an unrelated map or a different build.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from asset_library import _folder_for_identifier, _home, _load_manifest
from build_collections import catalog
from evolve_pak_workspace import normalized_name

MAX_MAP_ARCHIVES = 64
MAX_MAP_FILES = 50000
MAX_BUNDLE_BYTES = 8 * 1024 * 1024 * 1024
_MAP_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_HISTORICAL = frozenset(("legacy", "early"))


def _context(source_build, target_build, *, root=None):
    data = catalog(root)
    builds = data["builds"]
    source = builds.get(source_build)
    target = builds.get(target_build)
    if (not source or not target or source_build == target_build or
            source_build == "unassigned" or target_build == "unassigned"):
        raise ValueError("Choose two distinct registered builds.")
    if source["family"] not in _HISTORICAL:
        raise ValueError("Source must be a registered Legacy or early Evolve build.")
    if target["family"] != "stage2":
        raise ValueError("Destination must be a separate Stage 2 build.")
    return data, source, target


def _archive_path(member, manifest):
    """Prefer explicit build membership, then a known original installed path.

    A basename-only membership never grants arbitrary map ownership.
    """
    relative = str(member.get("relative_path") or "").replace("\\", "/")
    source = str(manifest.get("source") or "").replace("\\", "/")
    if "/levels/" in source.casefold() and "/levels/" not in relative.casefold():
        return source
    return relative


def _canonical_archive(archive_path):
    """Normalize only a known original GAME-relative archive location.

    Windows absolute install paths differ across builds; the same
    Game/Levels/Foo/level.pak should still compare as one archive. Retain
    GameDLC02/GameDLC04 prefixes to prevent collisions with base content.
    """
    raw = str(archive_path).replace("\\", "/").casefold().strip("/")
    match = re.search(r"(?:^|/)(game(?:dlc[0-9]+)?|levels)/", raw)
    if match:
        return raw[match.start(1):]
    return raw


def _role(archive_path, map_id):
    path = str(archive_path).replace("\\", "/").casefold().strip("/")
    map_key = map_id.casefold()
    basename = path.rsplit("/", 1)[-1]
    local = re.search(r"(?:^|/)levels/" + re.escape(map_key) + r"/([^/]+)\Z", path)
    if local and local.group(1) == "level.pak":
        return "compiled-level"
    if local and local.group(1) == "terraintexture.pak":
        return "terrain-textures"
    if basename == "levels_" + map_key + ".pak":
        return "compiled-level"
    if basename == "level_" + map_key + "_data.pak":
        return "map-data"
    if basename == "level_" + map_key + "_ts.pak":
        return "map-textures"
    return None


def _members_for_map(build, map_id, *, library_root):
    rows = []
    for archive_id, member in build["members"].items():
        folder = _folder_for_identifier(archive_id, library_root)
        if not folder.is_dir() or folder.is_symlink():
            continue
        manifest = _load_manifest(folder)
        source_path = _archive_path(member, manifest)
        role = _role(source_path, map_id)
        if role is None:
            continue
        if len(rows) >= MAX_MAP_ARCHIVES:
            raise ValueError("Too many map-specific PAKs; refine the inventory.")
        rows.append((archive_id, member, manifest, folder, source_path, role))
    return rows


def _explicit_map_references(source_build, source, members, names, *, library):
    """Inspect only readable XML links in the selected build and map context.

    No guessing about compiled world, shared PAKs, resource-list sidecars or
    unresolved map-global names. Source references are never looked up in
    another build or unrelated map-specific archive.
    """
    from build_collections import build_index
    from build_transfer_dependencies import discover_dependencies
    from level_asset_mount import map_relative_asset

    selected_ids = {row[0] for row in members}
    foreign_ids = set()
    for aid, member in source["members"].items():
        if aid in selected_ids:
            continue
        folder = _folder_for_identifier(aid, library)
        if not folder.is_dir() or folder.is_symlink():
            continue
        source_path = _archive_path(member, _load_manifest(folder))
        if re.search(r"(?:^|/)levels/[^/]+/", str(source_path).replace("\\", "/").casefold()):
            foreign_ids.add(aid)
    scoped = {}
    for name, rows in build_index(source_build, root=library).items():
        permitted = [
            row for row in rows
            if row["_asset_library"] not in foreign_ids
            and (not map_relative_asset(name) or row["_asset_library"] in selected_ids)
        ]
        if permitted:
            scoped[name] = permitted
    seeds = sorted({name for name in names
                    if name.casefold().split("/")[-1] in
                    ("levelinfo.xml", "leveldata.xml", "mission_mission0.xml")})[:16]
    if not seeds:
        return {"seeds": [], "found": [], "missing": [],
                "conflicts": [], "incomplete": [{"reason": "no-readable-level-metadata"}],
                "edges": [], "closure_complete": False}
    result = discover_dependencies(scoped, seeds, build_id=source_build)
    return dict(result, seeds=seeds)


def plan_compiled_map(source_build, target_build, map_id, *, root=None,
                      selected_archive=None):
    """Produce a bounded, non-deployable review plan from local inventories."""
    if not isinstance(map_id, str) or not _MAP_ID.fullmatch(map_id):
        raise ValueError("Choose a valid exact Legacy map identifier.")
    library = _home(root)
    inventory, source, target = _context(source_build, target_build, root=library)
    candidates = _members_for_map(source, map_id, library_root=library)
    if not any(row[-1] == "compiled-level" for row in candidates):
        raise ValueError("The source build has no imported compiled level PAK for this map.")
    if selected_archive is not None and selected_archive not in {row[0] for row in candidates}:
        raise ValueError("Selected archive does not belong to this map and source build.")
    target_group = _members_for_map(target, map_id, library_root=library)
    target_paths = {_canonical_archive(row[4]) for row in target_group}
    archives, seen_virtual = [], {}
    total_size = 0
    total_entries = 0
    conflicts = []
    for aid, member, manifest, folder, original, role in candidates:
        if manifest.get("warnings"):
            raise ValueError("Source map has an incomplete imported archive: " + str(original))
        count = len(manifest["entries"])
        total_entries += count
        total_size += sum(int(e.get("bytes", 0)) for e in manifest["entries"])
        if (total_entries > MAX_MAP_FILES or total_size > MAX_BUNDLE_BYTES or
                any(int(e.get("bytes", -1)) < 0 for e in manifest["entries"])):
            raise ValueError("Map review bundle exceeds bounded size/entry limits.")
        for entry in manifest["entries"]:
            virtual = normalized_name(entry["path"])
            key = virtual.casefold()
            old = seen_virtual.get(key)
            digest = entry.get("sha256")
            if old and old["sha256"] != digest:
                conflicts.append({"path": virtual, "first_archive": old["archive_id"],
                                  "second_archive": aid,
                                  "reason": "different-payload-same-map-virtual-path"})
            seen_virtual[key] = {"archive_id": aid, "sha256": digest}
        archives.append({
            "archive_id": aid, "source_relative": original, "role": role,
            "source_archive_sha256": manifest.get("source_sha256"),
            "entries": count, "extracted_bytes": sum(int(e["bytes"]) for e in manifest["entries"]),
            "target_archive_present": _canonical_archive(original) in target_paths,
            "deployment": "existing-archive-review" if _canonical_archive(original)
                          in target_paths else "new-archive-not-supported",
        })
    virtual_names = set(seen_virtual)
    # Compiled maps can have navigation and world data but no authored sources.
    has_source_heightmap = any(name.endswith("heightmap.dat") for name in virtual_names)
    has_authored_layer = any(name.endswith(".lyr") for name in virtual_names)
    has_compiled_terrain = any(name.endswith("terrain/terrain.dat") for name in virtual_names)
    reference_scan = _explicit_map_references(
        source_build, source, candidates, virtual_names, library=library)
    return {
        "schema": 1, "type": "legacy-stage2-compiled-map-review",
        "map_id": map_id, "source_build_id": source_build,
        "target_build_id": target_build, "inventory_revision": inventory["revision"],
        "source_family": source["family"], "target_family": target["family"],
        "archives": archives, "extracted_file_count": total_entries,
        "extracted_bytes": total_size, "virtual_path_conflicts": conflicts,
        "source_heightmap_present": has_source_heightmap,
        "source_layers_present": has_authored_layer,
        "compiled_terrain_present": has_compiled_terrain,
        "target_has_compiled_level": any(row[-1] == "compiled-level" for row in target_group),
        "requires_new_archive_support": any(not a["target_archive_present"] for a in archives),
        "needs_external_dependencies": True,
        "source_reference_diagnostics": reference_scan,
        "all_engine_dependencies_verified": False,
        "reference_scope": "selected-map-explicit-xml-only",
        "runtime_compatibility": "unverified",
        "editor_compatibility": "not-established-without-source-heightmap"
          if not has_source_heightmap else "unverified",
        "staging": "none", "installable": False, "validated_in_game": False,
        "warnings": [
            "Review-only: no Stage 2 map registration, archive signing, mounting or rollback performed.",
            "Map companions and shared dependencies may be absent; resource lists are not complete dependency proofs.",
            "Compiled terrain, AI, navigation, entities and gameplay rules require Stage 2 runtime validation.",
            "Legacy script signatures and removed game modes must never be copied wholesale.",
        ],
    }


def _hash_copy(source, destination, expected_hash, expected_size):
    digest = hashlib.sha256()
    amount = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            amount += len(chunk)
            if amount > expected_size:
                raise ValueError("Extracted source changed while copying.")
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if amount != expected_size or digest.hexdigest() != expected_hash:
        raise ValueError("Extracted source hash/size did not match its verified manifest.")


def export_map_review_bundle(source_build, target_build, map_id, destination, *,
                             root=None, selected_archive=None,
                             cancelled=None, progress=None):
    """Copy verified extracted map-local files into a NEW, review-only folder.

    Does not copy encrypted PAKs, install content, write to Asset Library or
    modify any game. On interruption, leave an INCOMPLETE marker for diagnosis.
    """
    library = _home(root)
    plan = plan_compiled_map(source_build, target_build, map_id, root=library,
                             selected_archive=selected_archive)
    target = Path(destination).expanduser().resolve()
    if (target.exists() or not target.parent.is_dir() or
            target.is_relative_to(library) or library.is_relative_to(target)):
        raise ValueError("Choose a NEW output folder outside the Asset Library.")
    snapshot = catalog(library)["builds"]
    for build_id in (source_build, target_build):
        location = snapshot[build_id].get("location")
        if location:
            parent = Path(location).resolve()
            if target.is_relative_to(parent) or parent.is_relative_to(target):
                raise ValueError("Review bundle must be outside either game installation.")
    selected = _members_for_map(snapshot[source_build], map_id, library_root=library)
    for _aid, _member, manifest, _folder, _original, _role_name in selected:
        # An archive imported from an unregistered Legacy installation still
        # carries its physical EvolveGame origin in the library manifest.
        # Never create review exports inside that game root.
        original = Path(str(manifest.get("source") or "")).resolve()
        for candidate in original.parents:
            if candidate.name.casefold() == "evolvegame":
                if target.is_relative_to(candidate) or candidate.is_relative_to(target):
                    raise ValueError("Research export must not be inside an EvolveGame installation.")
                break
    target.mkdir(mode=0o700)
    marker = target / "INCOMPLETE.txt"
    marker.write_text("Map review export incomplete. Do not load or deploy files.\n",
                      encoding="utf-8")
    try:
        manifest_rows = []
        completed = 0
        for aid, _member, manifest, folder, _original, role in selected:
            files_root = (folder / "files").resolve(strict=True)
            if (folder / "files").is_symlink() or not files_root.is_relative_to(folder.resolve()):
                raise ValueError("Imported archive has an unsafe files directory.")
            for row in manifest["entries"]:
                if cancelled and cancelled():
                    from asset_library import ImportCanceled
                    raise ImportCanceled("Map review bundle cancelled; incomplete files remain marked.")
                name = normalized_name(row["path"])
                src = files_root.joinpath(*name.split("/"))
                if (src.is_symlink() or not src.is_file() or
                        not src.resolve().is_relative_to(files_root)):
                    raise ValueError("An extracted map file is missing or unsafe: " + name)
                expected_size = int(row["bytes"])
                expected_hash = str(row.get("sha256") or "")
                if not re.fullmatch("[0-9a-fA-F]{64}", expected_hash):
                    raise ValueError("Map entry has no valid SHA-256 provenance: " + name)
                if src.stat().st_size != expected_size:
                    raise ValueError("Extracted map file size changed: " + name)
                relative = Path("archives") / aid / Path(*name.split("/"))
                dest = target / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                _hash_copy(src, dest, expected_hash.casefold(), expected_size)
                manifest_rows.append({
                    "archive_id": aid, "role": role, "virtual": name,
                    "bundle_path": relative.as_posix(), "sha256": expected_hash.casefold(),
                    "bytes": expected_size
                })
                completed += 1
                if progress:
                    progress("Exporting map review files", completed,
                             plan["extracted_file_count"])
        plan["exported_files"] = manifest_rows
        plan["bundle_output"] = str(target)
        (target / "transfer-review.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8")
        (target / "READ-ME-FIRST.txt").write_text(
            "LEGACY MAP RESEARCH ASSETS - NOT A WORKING STAGE 2 MAP\n"
            "The files remain separated by source PAK to preserve provenance.\n"
            "No game, staging plan, private key or game PAK was modified.\n"
            "No mode, map registration, terrain conversion or binary script port occurred.\n"
            "Inspect transfer-review.json for conflicts and missing engine support.\n",
            encoding="utf-8")
        marker.unlink()
    except Exception:
        # A partial bundle is intentionally marked so it cannot be mistaken
        # for a complete installation or a deployable map.
        raise
    return plan
