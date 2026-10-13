"""Versioned build inventories. No engine compatibility or installation validation implied.

The JSON catalog only owns associations. Extracted PAK content remains under the
legacy archive IDs; an archive may be referenced by several build snapshots.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

FILENAME = "build-collections.json"
FAMILIES = frozenset(("early", "legacy", "stage2", "unknown"))
UNASSIGNED = "unassigned"


def _root(root=None):
    from asset_library import _home
    return _home(root)


def _path(root=None):
    return _root(root) / FILENAME


def _empty():
    return {"version": 1, "revision": 0, "builds": {
        UNASSIGNED: {"label": "Unassigned", "family": "unknown",
                     "evidence": {}, "location": None, "members": {}}}}


def _read(root=None):
    path = _path(root)
    data = json.loads(path.read_text("utf-8")) if path.exists() else _empty()
    if data.get("version") != 1 or not isinstance(data.get("builds"), dict):
        raise ValueError("Unsupported build catalog.")
    return data


def _write(data, root=None):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as out:
            json.dump(data, out, indent=2, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    from asset_library import _invalidate
    _invalidate()


def catalog(root=None):
    """Return an inventory with legacy imports visible as Unassigned."""
    from asset_library import imported_archives
    data = _read(root)
    known = {aid for build in data["builds"].values()
             for aid in build.get("members", {})}
    unassigned = data["builds"].setdefault(UNASSIGNED, _empty()["builds"][UNASSIGNED])
    for archive in imported_archives(root):
        if archive["id"] not in known:
            unassigned["members"][archive["id"]] = {
                "relative_path": archive["name"], "source_sha256": archive["source_sha256"],
                "provenance": "legacy-unassigned"}
    return data


def register_build(label, family, *, root=None, location=None, evidence=None):
    if family not in FAMILIES or not isinstance(label, str) or not label.strip():
        raise ValueError("Invalid build label or family.")
    data = catalog(root)
    identifier = uuid4().hex
    data["builds"][identifier] = {
        "label": label.strip()[:120], "family": family,
        "location": str(Path(location).resolve()) if location else None,
        "evidence": dict(evidence or {}), "members": {}}
    data["revision"] += 1
    _write(data, root)
    return identifier


def update_location(build_id, location, *, root=None):
    """A relocation is metadata, not a new build identity."""
    data = catalog(root)
    if build_id not in data["builds"] or build_id == UNASSIGNED:
        raise ValueError("Unknown build.")
    data["builds"][build_id]["location"] = str(Path(location).resolve()) if location else None
    data["revision"] += 1
    _write(data, root)


def assign_archive(build_id, archive_id, *, root=None, relative_path=None,
                   provenance="explicit", expected_source_sha256=None):
    from asset_library import imported_archives
    data = catalog(root)
    if build_id not in data["builds"] or build_id == UNASSIGNED:
        raise ValueError("Select a registered build.")
    matches = [x for x in imported_archives(root) if x["id"] == archive_id]
    if len(matches) != 1:
        raise ValueError("Missing imported archive.")
    item = matches[0]
    if expected_source_sha256 and item["source_sha256"] != expected_source_sha256:
        raise ValueError("Archive changed: review the inventory.")
    relative = relative_path or item["name"]
    from evolve_pak_workspace import normalized_name
    relative = normalized_name(relative.replace("\\", "/"))
    old = data["builds"][build_id]["members"].get(archive_id)
    member = {"relative_path": relative, "source_sha256": item["source_sha256"],
              "provenance": provenance}
    if old is not None and old != member:
        raise ValueError("Inventory change requires explicit review.")
    data["builds"][build_id]["members"][archive_id] = member
    data["builds"][UNASSIGNED]["members"].pop(archive_id, None)
    data["revision"] += 1
    _write(data, root)


def remove_membership(build_id, archive_id, *, root=None):
    """Never delete shared extracted content."""
    data = catalog(root)
    if build_id == UNASSIGNED or build_id not in data["builds"]:
        raise ValueError("Unknown build.")
    if archive_id not in data["builds"][build_id]["members"]:
        raise ValueError("Archive is not a member.")
    del data["builds"][build_id]["members"][archive_id]
    data["revision"] += 1
    _write(data, root)



def resolve_archive_build(archive_id, *, root=None, build_id=None):
    """Select the owning build, never combine two memberships implicitly."""
    data = catalog(root)
    owners = [bid for bid, record in data["builds"].items()
              if archive_id in record["members"]]
    if build_id is not None:
        if build_id == "all" or build_id not in owners:
            raise ValueError("Selected archive does not belong to that build.")
        return build_id
    if len(owners) != 1:
        raise ValueError("Archive has multiple build memberships; choose a build explicitly.")
    return owners[0]


def build_index(build_id, *, root=None):
    """Fail-closed index; never fall back to assets from another build."""
    from asset_library import extracted_index
    data = catalog(root)
    if build_id == "all" or build_id not in data["builds"]:
        raise ValueError("An explicit build context is required.")
    allowed = set(data["builds"][build_id]["members"])
    return {name: hits for name, records in extracted_index(root).items()
            if (hits := [dict(record, build_id=build_id) for record in records
                         if record.get("_asset_library") in allowed])}


def compare_builds(source_id, target_id, *, root=None):
    from asset_library import _folder_for_identifier, _load_manifest
    data = catalog(root)
    if source_id not in data["builds"] or target_id not in data["builds"]:
        raise ValueError("Unknown build.")
    def entries(build_id):
        result = {}
        for aid in data["builds"][build_id]["members"]:
            folder = _folder_for_identifier(aid, _root(root))
            if not folder.is_dir():
                continue
            manifest = _load_manifest(folder)
            for entry in manifest["entries"]:
                key = entry["path"].casefold()
                result.setdefault(key, set()).add(entry.get("sha256"))
        return result
    left, right = entries(source_id), entries(target_id)
    return tuple({"path": path, "status": (
        "added" if path not in right else "removed" if path not in left
        else "identical" if len(left[path]) == len(right[path]) == 1 and left[path] == right[path]
        else "changed" if len(left[path]) == len(right[path]) == 1 else "unknown"),
        "hash_type": "normalized-extracted-sha256"}
        for path in sorted(left.keys() | right.keys()))


def prepare_transfer(source_id, target_id, paths, *, root=None,
                     target_baseline_sha256=None, target_project=None,
                     cancelled=None):
    """Build a bounded evidence-only manifest. Never invoke an archive writer."""
    from evolve_pak_workspace import normalized_name
    from asset_library import _folder_for_identifier, _load_manifest
    data = catalog(root)
    if (source_id not in data["builds"] or target_id not in data["builds"]
            or source_id == target_id or source_id == UNASSIGNED
            or target_id == UNASSIGNED):
        raise ValueError("Select distinct registered source and target builds.")
    if data["builds"][target_id]["family"] != "stage2":
        raise ValueError("The target must be a Stage 2 build.")
    if target_baseline_sha256 is not None and (
            len(target_baseline_sha256) != 64 or
            any(ch not in "0123456789abcdefABCDEF" for ch in target_baseline_sha256)):
        raise ValueError("Expected target baseline must be a SHA-256 digest.")
    if len(paths) > 4096:
        raise ValueError("Too many transfer references.")
    source, target = build_index(source_id, root=root), build_index(target_id, root=root)
    from build_transfer_dependencies import discover_dependencies
    discovered = discover_dependencies(source, paths, build_id=source_id,
                                       cancelled=cancelled)
    if discovered["cancelled"]:
        from asset_library import ImportCanceled
        raise ImportCanceled("Transfer planning cancelled.")
    paths = tuple(dict.fromkeys((*paths, *discovered["found"], *discovered["missing"])))
    fingerprints = {}
    for bid in (source_id, target_id):
        for archive_id in data["builds"][bid]["members"]:
            folder = _folder_for_identifier(archive_id, _root(root))
            if not folder.is_dir():
                raise ValueError("Missing content for an assigned archive.")
            manifest = _load_manifest(folder)
            fingerprints[archive_id] = {
                entry["path"].casefold(): entry.get("sha256")
                for entry in manifest["entries"]}
    mappings = []
    for reference in paths:
        virtual = normalized_name(reference)
        key = virtual.casefold()
        a, b = source.get(key, ()), target.get(key, ())
        src = [{"archive_id": r["_asset_library"],
                "extracted_sha256": fingerprints[r["_asset_library"]].get(key)}
               for r in a]
        dst = [{"archive_id": r["_asset_library"],
                "extracted_sha256": fingerprints[r["_asset_library"]].get(key)}
               for r in b]
        src_hashes = {r["extracted_sha256"] for r in src}
        dst_hashes = {r["extracted_sha256"] for r in dst}
        conflicting = len(src_hashes) > 1 or len(dst_hashes) > 1
        action = ("missing-source" if not src else
                  "conflict" if conflicting else
                  "reuse-candidate" if src_hashes == dst_hashes else
                  "review-replace" if dst else "review-add")
        mappings.append({
            "path": virtual, "source": src, "target": dst, "action": action,
            "compatibility": "reference-only" if src else "unsupported",
            "validated_in_game": False, "hash_type": "normalized-extracted-sha256",
            "dependency_closure_complete": discovered["closure_complete"]})
    return {
        "schema": 1, "source_build_id": source_id, "target_build_id": target_id,
        "target_project": str(Path(target_project).resolve()) if target_project else None,
        "inventory_revision": data["revision"],
        "target_baseline_sha256": target_baseline_sha256,
        "baseline_verified": False, "staging": "none", "mappings": mappings,
        "dependency_diagnostics": discovered,
        "warning": "Reference-only plan. Dependency discovery is limited to explicit XML references; no conversion, writer staging or in-game verification."
    }
