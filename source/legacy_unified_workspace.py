"""Disk-efficient adapter for historical PAKs in the regular Stage 2 Qt editor.

The user interface is the SAME QtWorkspaceEditor used for Stage 2. This adapter
only supplies bytes on demand and sends modifications to the verified legacy
override backend. It never registers an unsigned archive as a Stage 2 mod.
"""
from __future__ import annotations

import json
from pathlib import Path

from legacy_pak_workspace import entries_by_name, load_project, read_member

VIEW_MANIFEST = ".evolve-pak-workspace.json"
MAX_ONE_FILE = 128 * 1024 * 1024
MAX_COMPANIONS = 180
MAX_COMPANION_BYTES = 320 * 1024 * 1024


def unified_view_for_project(project):
    """Create a sparse workspace manifest readable by the Stage 2 file explorer."""
    folder = Path(project["folder"])
    view = folder / ".unified-editor-cache"
    files = view / "files"
    if view.is_symlink() or files.is_symlink():
        raise ValueError("Linked legacy editor cache is not supported.")
    files.mkdir(parents=True, exist_ok=True)
    manifest = view / VIEW_MANIFEST
    if manifest.is_symlink():
        raise ValueError("Linked legacy editor manifest is not supported.")
    # The base editor expects an extracted-file manifest; it is intentionally
    # NOT a signed mod project and has no registered staging or signing key.
    inventory = {
        "format": "legacy-zip-view-v1",
        "source": str(project["source_path"]),
        "entries": [
            {"path": item["path"], "format": "raw",
             "size": item["size"], "legacy_zip_crc": item["crc"]}
            for item in project["entries"]
        ],
    }
    manifest.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return view


def materialize_legacy_member(project, virtual, *, root=None, limit=MAX_ONE_FILE):
    """Extract only the requested safe virtual file and verify its ZIP CRC."""
    row = entries_by_name(project).get(virtual)
    if row is None:
        raise ValueError("Unknown source member.")
    if row["size"] > limit:
        raise ValueError("Asset exceeds the on-demand preview limit; use raw export.")
    view = Path(root) if root else unified_view_for_project(project)
    dest_root = view / "files"
    target = dest_root.joinpath(*virtual.split("/"))
    if not target.resolve().is_relative_to(dest_root.resolve()):
        raise ValueError("Unsafe legacy virtual path.")
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if
                                  p == dest_root or dest_root in p.parents):
        raise ValueError("Linked legacy file path is not allowed.")
    try:
        data = read_member(project, virtual, max_bytes=limit)
    except Exception as exc:
        # In particular, zipfile.BadZipFile means a real member CRC failure.
        raise ValueError("Legacy asset failed byte/CRC verification: " + str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def materialize_model_companions(project, virtual, *, root):
    """Lazy same-folder dependency retrieval; skips corrupt/huge siblings."""
    parent = virtual.rpartition("/")[0]
    siblings = [r for r in project["entries"]
                if r["path"] != virtual and r["path"].rpartition("/")[0] == parent]
    fetched = 0
    bytes_used = 0
    warnings = []
    for record in siblings[:MAX_COMPANIONS]:
        if record["size"] > MAX_ONE_FILE or bytes_used + record["size"] > MAX_COMPANION_BYTES:
            continue
        try:
            materialize_legacy_member(project, record["path"], root=root)
            fetched += 1
            bytes_used += record["size"]
        except ValueError as exc:
            warnings.append(record["path"] + ": " + str(exc))
    return {"materialized": fetched, "warnings": warnings}
