"""Build-scoped Legacy -> Stage 2 bulk transfer review exports.

A complete registered Legacy collection can be compared with a separate
registered Stage 2 collection and exported in batches or as a whole-build
review bundle. These exports are NEVER installed, converted to CryPAKs,
added to a signed stage plan, or represented as a playable map/mode.

Every extracted source file is hash-verified while copying. Ambiguous source
payloads and target conflicts are *reported* and never silently selected.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from asset_library import _folder_for_identifier, _home, _load_manifest
from build_collections import catalog
from evolve_pak_workspace import normalized_name
from legacy_stage2_transfer import _archive_path, _canonical_archive, _context, _hash_copy

MAX_ARCHIVES = 4096
MAX_ENTRIES = 400_000  # 271,786 Legacy catalog records in inspected installation.
MAX_EXPORT_BYTES = 100 * 1024 ** 3
HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
_MAP_FOLDER = re.compile(r"(?:^|/)levels/([a-z0-9_-]{1,80})/", re.I)
_MAP_DATA = re.compile(r"(?:^|/)level_([a-z0-9_-]{1,80})_(?:data|ts)\.pak\Z", re.I)
_MAP_PACKED = re.compile(r"(?:^|/)levels_([a-z0-9_-]{1,80})\.pak\Z", re.I)
_NORMAL_EXTENSIONS = (".cgf", ".cgfm", ".cga", ".cgam", ".chr", ".chrm",
                      ".skin", ".skinm")


def _owner(archive):
    relative = str(archive).replace("\\", "/").casefold()
    for pattern in (_MAP_FOLDER, _MAP_DATA, _MAP_PACKED):
        match = pattern.search(relative)
        if match:
            return match.group(1)
    return None


def _scope(archive, virtual):
    """Map-local paths must only compare with that same map in Stage 2.

    Even a map's global-looking model/resource path is kept map-scoped here;
    a separate shared PAK match would need independent dependency validation.
    """
    owner = _owner(archive)
    return "map:" + owner if owner else "global"


def _asset_type(path, scope):
    name = path.casefold()
    if name.endswith(".lua"):
        return "binary-script-review"
    if name.endswith((".caf", ".dba", ".chrparams")):
        return "animation-review"
    if (name.endswith((".dds", ".tif", ".png")) or
            re.search(r"\.dds\.[0-9]+\Z", name)):
        return "texture-review"
    if name.endswith(_NORMAL_EXTENSIONS):
        return "native-model-review"
    if name.endswith((".mtl", ".xml", ".cdf")):
        return "metadata-review"
    if scope.startswith("map:"):
        return "compiled-map-review"
    return "unknown-format-review"


def _safe_prefix(prefix):
    if not prefix:
        return ""
    value = normalized_name(str(prefix).strip().replace("\\", "/").rstrip("/"))
    return value.casefold()


def _archive_records(build, library, *, selected_archive=None, prefix="",
                     cancelled=None, blocked=None):
    """Yield bounded manifest records from one registered build.

    A full-game import can include partially extracted or absent archives.
    Exclude those *entire* archives and record clear errors instead of
    crashing every transfer, inventing their contents or silently claiming
    they are compatible. Explicitly selecting a damaged PAK still fails.
    """
    members = build["members"]
    if selected_archive and selected_archive not in members:
        raise ValueError("Selected PAK does not belong to the chosen source build.")
    if len(members) > MAX_ARCHIVES:
        raise ValueError("Build exceeds the supported archive inventory limit.")
    total = 0
    for aid, member in sorted(members.items()):
        if selected_archive and selected_archive != aid:
            continue
        if cancelled and cancelled():
            from asset_library import ImportCanceled
            raise ImportCanceled("Legacy transfer inventory cancelled.")
        try:
            folder = _folder_for_identifier(aid, library)
            if folder.is_symlink() or not folder.is_dir():
                raise ValueError("Missing or unsafe library archive folder.")
            manifest = _load_manifest(folder)
            if manifest.get("warnings"):
                raise ValueError("Archive has unreviewed extracted-file warnings.")
            if manifest.get("source_sha256") != member.get("source_sha256"):
                raise ValueError("Build membership does not match imported source PAK.")
            relative = _archive_path(member, manifest)
            parsed = []
            for entry in manifest["entries"]:
                total += 1
                if total > MAX_ENTRIES:
                    raise ValueError(
                        "Too many imported entries. Export a smaller archive scope.")
                virtual = normalized_name(entry["path"])
                size = entry.get("bytes")
                sha = str(entry.get("sha256") or "").casefold()
                if (not isinstance(size, int) or size < 0 or
                        not HEX64.fullmatch(sha)):
                    raise ValueError(
                        "Invalid extracted entry hash/size: " + virtual)
                if prefix and not (virtual.casefold() == prefix or
                                   virtual.casefold().startswith(prefix + "/")):
                    continue
                parsed.append({
                    "archive_id": aid, "original_archive": relative,
                    "scope": _scope(relative, virtual), "path": virtual,
                    "sha256": sha, "bytes": size,
                })
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if selected_archive:
                raise ValueError(
                    "Selected Legacy PAK is incomplete or unavailable: " + str(exc)
                ) from exc
            if blocked is not None:
                blocked.append({"archive_id": aid,
                                "reason": str(exc)[:250]})
            continue
        yield from parsed

def plan_legacy_build_export(source_build, target_build, *, root=None,
                             source_archive=None, prefix="", include_identical=False,
                             cancelled=None):
    """Classify every path from the selected imported Legacy PAK inventory.

    There is no automatic import or patching of Stage 2. A missing path means
    absent from *the imported Stage 2 catalog*, not the installed game.
    """
    library = _home(root)
    inventory, src, dst = _context(source_build, target_build, root=library)
    if not src["members"]:
        raise ValueError("Import Legacy PAKs into the chosen source build first.")
    if not dst["members"]:
        raise ValueError("Import Stage 2 PAKs into the target build before comparison.")
    prefix = _safe_prefix(prefix)
    source_map, target_map = defaultdict(list), defaultdict(list)
    source_blocked, target_blocked = [], []
    for record in _archive_records(
            dst, library, cancelled=cancelled, blocked=target_blocked):
        target_map[(record["scope"], record["path"].casefold())].append(record)
    for record in _archive_records(
            src, library, selected_archive=source_archive, prefix=prefix,
            cancelled=cancelled, blocked=source_blocked):
        source_map[(record["scope"], record["path"].casefold())].append(record)
    if not source_map:
        raise ValueError("No imported Legacy assets match the selected archive/path filter.")

    rows = []
    total_bytes = 0
    counts = defaultdict(int)
    for scope, key in sorted(source_map):
        if cancelled and cancelled():
            from asset_library import ImportCanceled
            raise ImportCanceled("Legacy transfer planning cancelled.")
        sources = source_map[(scope, key)]
        targets = target_map.get((scope, key), [])
        source_hashes = {r["sha256"] for r in sources}
        target_hashes = {r["sha256"] for r in targets}
        if len(source_hashes) != 1:
            action = "ambiguous-legacy-source"
        elif len(target_hashes) > 1:
            action = "ambiguous-stage2-target"
        elif targets and source_hashes == target_hashes:
            action = "same-payload-candidate"
        elif targets:
            action = "existing-path-review"
        else:
            action = "not-in-imported-stage2-catalog"
        winner = min(sources, key=lambda x: x["archive_id"])
        kind = _asset_type(winner["path"], scope)
        # Never copy an arbitrary winner when the source is ambiguous.
        copy = (action not in ("ambiguous-legacy-source",
                              "ambiguous-stage2-target") and
                (include_identical or action != "same-payload-candidate"))
        if copy:
            total_bytes += winner["bytes"]
            if total_bytes > MAX_EXPORT_BYTES:
                raise ValueError("Review export exceeds 100 GiB. Select fewer assets.")
        counts[action] += 1
        rows.append({
            "path": winner["path"], "scope": scope,
            "asset_type": kind, "action": action,
            "source_providers": sources, "target_providers": targets,
            "copy_for_review": copy,
            "writer_supported": False,
            "validated_in_game": False,
            "apply_to_stage2": "not-approved",
            "compatibility": ("unverified" if not targets
                              else "hash-match-only" if action == "same-payload-candidate"
                              else "requires-per-file-validation"),
        })
    result = {
        "schema": 1, "type": "legacy-stage2-build-transfer-review",
        "source_build_id": source_build,
        "target_build_id": target_build,
        "source_family": src["family"], "target_family": dst["family"],
        "inventory_revision": inventory["revision"],
        "source_archive": source_archive,
        "virtual_prefix": prefix,
        "include_identical": bool(include_identical),
        "source_imported_archive_count": len(src["members"]),
        "target_imported_archive_count": len(dst["members"]),
        "source_blocked_archives": source_blocked,
        "target_blocked_archives": target_blocked,
        "source_usable_archive_count": len(src["members"]) - len(source_blocked),
        "target_usable_archive_count": len(dst["members"]) - len(target_blocked),
        "file_count": len(rows),
        "files_to_copy": sum(bool(row["copy_for_review"]) for row in rows),
        "bytes_to_copy": total_bytes,
        "status_counts": dict(counts),
        "files": rows,
        "staging": "none",
        "installer_written": False, "game_files_modified": False,
        "validated_in_game": False,
        "warnings": [
            "This export is NOT a playable Stage 2 mod or map, or a signed PAK.",
            "Comparison covers only imported assets from the two registered builds.",
            "Incomplete imports are excluded and separately listed in blocked_archives.",
            "No cross-map fallback: compiled map paths are scoped by map ID.",
            "Existing-path assets require per-file model/material/texture/animation validation.",
            "New paths, map PAKs and modes require new-entry signing, mounting, registration and rollback.",
            "Streamed DDS mip/alpha companions are copied as named but completeness is not verified.",
            "Legacy LuaQ and Stage 2 Jee binary scripts are not converted.",
        ],
    }
    # This digest ensures the previewed build inventory and policy remain
    # exactly the same when an export job starts later.
    fingerprint = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return dict(result, inventory_sha256=fingerprint)


def _deny_output(destination, *, library, builds, protected_roots=()):
    candidate = Path(destination).expanduser().resolve()
    if candidate.exists() or not candidate.parent.is_dir() or candidate.parent.is_symlink():
        raise ValueError("Choose a new review bundle folder under an existing directory.")
    roots = [library]
    for build in builds:
        location = build.get("location")
        if location:
            roots.append(Path(location).resolve())
    roots.extend(Path(p).resolve() for p in protected_roots if p)
    for root in roots:
        if candidate.is_relative_to(root) or root.is_relative_to(candidate):
            raise ValueError("Review output must stay outside game, signing stage and library roots.")
    return candidate


def export_legacy_build_bundle(source_build, target_build, destination, *,
                               root=None, source_archive=None, prefix="",
                               include_identical=False, plan_only=False,
                               expected_inventory_sha256=None, protected_roots=(),
                               cancelled=None, progress=None):
    """Create a new review bundle with exact imported source bytes and hashes.

    Never writes to a registered game, asset library, staged signing directory
    or existing project. Interrupted work retains INCOMPLETE.txt and cannot
    masquerade as a usable finished transfer package.
    """
    library = _home(root)
    inventory, source, target = _context(source_build, target_build, root=library)
    output = _deny_output(destination, library=library, builds=(source, target),
                          protected_roots=protected_roots)
    plan = plan_legacy_build_export(
        source_build, target_build, root=library,
        source_archive=source_archive, prefix=prefix,
        include_identical=include_identical, cancelled=cancelled)
    if expected_inventory_sha256 and plan["inventory_sha256"] != expected_inventory_sha256:
        raise ValueError("Build catalog changed after preview. Review transfer again.")
    if not plan_only:
        free = shutil.disk_usage(output.parent).free
        if free < plan["bytes_to_copy"] + 64 * 1024 * 1024:
            raise ValueError("Insufficient destination disk space for selected assets.")
    output.mkdir(mode=0o700)
    marker = output / "INCOMPLETE.txt"
    marker.write_text("Incomplete Legacy review export; do not deploy.\n", encoding="utf-8")
    try:
        copied = 0
        payloads = []
        if not plan_only:
            for row in plan["files"]:
                if not row["copy_for_review"]:
                    continue
                if cancelled and cancelled():
                    from asset_library import ImportCanceled
                    raise ImportCanceled("Legacy export cancelled; partial bundle remains marked.")
                provider = min(row["source_providers"], key=lambda x: x["archive_id"])
                folder = _folder_for_identifier(provider["archive_id"], library)
                if folder.is_symlink() or not folder.is_dir():
                    raise ValueError("Source archive disappeared after review.")
                root_path = folder / "files"
                if root_path.is_symlink():
                    raise ValueError("Source archive has an unsafe file root.")
                root_path = root_path.resolve(strict=True)
                path = root_path.joinpath(*provider["path"].split("/"))
                if (path.is_symlink() or not path.is_file() or
                        not path.resolve().is_relative_to(root_path)):
                    raise ValueError("Extracted asset missing or unsafe: " + provider["path"])
                # Keep map ownership in the export path, not merely the name
                # (e.g. both maps may contain terrain/terrain.dat).
                relative = (Path("assets") / provider["archive_id"] /
                            Path(*provider["path"].split("/")))
                output_file = output / relative
                output_file.parent.mkdir(parents=True, exist_ok=True)
                _hash_copy(path, output_file, provider["sha256"], provider["bytes"])
                payloads.append({
                    "path": provider["path"], "scope": row["scope"],
                    "source_archive_id": provider["archive_id"],
                    "file": relative.as_posix(),
                    "sha256": provider["sha256"], "bytes": provider["bytes"],
                })
                copied += 1
                if progress and (copied % 25 == 0 or
                                 copied == plan["files_to_copy"]):
                    progress("Exporting Legacy review assets", copied, plan["files_to_copy"])
        plan["exported_files"] = payloads
        plan["export_mode"] = "inventory-only" if plan_only else "review-assets"
        plan["output"] = str(output)
        (output / "stage2-transfer-review.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8")
        (output / "READ-ME-FIRST.txt").write_text(
            "LEGACY -> STAGE 2: REVIEW ONLY, NOT INSTALLABLE\n"
            "Extracted asset bytes and build/hash provenance are documented\n"
            "in stage2-transfer-review.json. No CryPAKs were created or\n"
            "installed, and no engine compatibility was established.\n"
            "Do not copy these files to the game or assume maps/modes work.\n",
            encoding="utf-8")
        marker.unlink()
    except Exception:
        raise
    return {
        "output": str(output), "file_count": plan["file_count"],
        "files_exported": copied, "bytes_expected": plan["bytes_to_copy"],
        "inventory_only": bool(plan_only),
        "conflicts": counts_conflicts(plan),
        "installed": False,
    }


def counts_conflicts(plan):
    return (plan["status_counts"].get("ambiguous-legacy-source", 0) +
            plan["status_counts"].get("ambiguous-stage2-target", 0))
