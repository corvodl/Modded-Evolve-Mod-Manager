"""Export reviewed Legacy-to-Stage-2 candidates without touching either build.

This is NOT a CryPak writer: files are copied from validated extracted
Asset Library manifests into a new external review bundle, with SHA-256 and
explicit provenance. Signed PAK generation and in-game compatibility are
separate, unimplemented gates.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

MAX_FILES = 512
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
COPY_CHUNK = 1024 * 1024


def _hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(COPY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _checked_path(base, rel):
    from evolve_pak_workspace import normalized_name
    virtual = normalized_name(rel)
    base = Path(base).absolute()
    candidate = base.joinpath(*virtual.split("/"))
    if (not candidate.is_relative_to(base)
            or any(parent.is_symlink() for parent in (candidate, *candidate.parents)
                   if parent == base or base in parent.parents)
            or not candidate.resolve().is_relative_to(base.resolve())):
        raise ValueError("Unsafe linked candidate path: " + virtual)
    return candidate


def _check_destination(destination, root, target_project):
    output = Path(destination).absolute()
    home = Path(root).resolve()
    resolved = output.resolve(strict=False)
    if (output.exists() or output.is_symlink()
            or any(p.is_symlink() for p in output.parents)
            or resolved == home or resolved.is_relative_to(home)
            or home.is_relative_to(resolved)):
        raise ValueError("Choose a new external directory, not the Asset Library.")
    if target_project is not None:
        target = Path(target_project).resolve()
        if output.is_relative_to(target) or target.is_relative_to(output):
            raise ValueError("Candidate exports cannot modify the target project.")
    return output


def export_candidate(plan, destination, *, root=None, cancelled=None):
    """Create a self-describing external *reference only* candidate bundle.

    Only a single unambiguous indexed source for each selected path qualifies.
    Binary dependencies can remain unverified; these are clearly marked and
    prevent the manifest being mistaken for an importable/signed PAK package.
    """
    from asset_library import (_home, _folder_for_identifier, _load_manifest)
    from build_collections import catalog
    from evolve_pak_workspace import normalized_name
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("staging") != "none":
        raise ValueError("Expected a reference-only transfer plan.")
    inventory = catalog(root)
    if inventory["revision"] != plan.get("inventory_revision"):
        raise ValueError("Build inventory changed; regenerate the transfer plan.")
    origin = plan.get("source_build_id")
    target = plan.get("target_build_id")
    if (origin not in inventory["builds"] or target not in inventory["builds"]
            or inventory["builds"][origin]["family"] == "stage2"
            or inventory["builds"][target]["family"] != "stage2"):
        raise ValueError("Select a historical source and a Stage 2 target build.")
    mappings = plan.get("mappings")
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= MAX_FILES:
        raise ValueError("Transfer export requires between 1 and 512 assets.")
    output = _check_destination(destination, _home(root), plan.get("target_project"))
    records = []
    total = 0
    seen = set()
    for mapping in mappings:
        if cancelled and cancelled():
            raise RuntimeError("Transfer candidate export cancelled.")
        if mapping.get("action") not in ("review-add", "review-replace", "reuse-candidate"):
            raise ValueError("Missing or conflicting source assets must be reviewed first.")
        name = normalized_name(mapping["path"])
        if name.casefold() in seen:
            raise ValueError("Duplicate source virtual asset.")
        seen.add(name.casefold())
        source = mapping.get("source")
        if not isinstance(source, list) or len(source) != 1:
            raise ValueError("Explicit single-archive source provenance is required.")
        aid = source[0].get("archive_id")
        if aid not in inventory["builds"][origin]["members"]:
            raise ValueError("Source archive no longer belongs to the selected build.")
        folder = _folder_for_identifier(aid, _home(root))
        manifest = _load_manifest(folder)
        matches = [entry for entry in manifest["entries"]
                   if entry["path"].casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError("Source asset is missing or ambiguous.")
        entry = matches[0]
        expected = source[0].get("extracted_sha256")
        if expected != entry.get("sha256") or not re.fullmatch(r"[0-9a-fA-F]{64}", expected or ""):
            raise ValueError("Source SHA-256 changed; refresh the plan.")
        src = _checked_path(folder / "files", entry["path"])
        if not src.is_file() or src.stat().st_size != entry["bytes"] or _hash(src) != expected:
            raise ValueError("Source asset contents differ from extracted manifest.")
        total += src.stat().st_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Candidate bundle exceeds 1 GiB safety limit.")
        records.append({"path": name, "archive_id": aid,
                        "sha256": expected.lower(), "bytes": src.stat().st_size,
                        "action": mapping["action"], "file": src})
    output.mkdir(parents=True, exist_ok=False)
    succeeded = False
    try:
        assets = output / "assets"
        for record in records:
            if cancelled and cancelled():
                raise RuntimeError("Transfer candidate export cancelled.")
            dst = _checked_path(assets, record["path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            # exclusive mode; a race cannot silently overwrite external files
            with record["file"].open("rb") as src, dst.open("xb") as out:
                shutil.copyfileobj(src, out, COPY_CHUNK)
                out.flush()
                os.fsync(out.fileno())
            if _hash(dst) != record["sha256"]:
                raise ValueError("Candidate export integrity verification failed.")
        exported = [{key: row[key] for key in ("path", "archive_id", "sha256", "bytes", "action")}
                    for row in records]
        manifest = {
            "schema": 1, "kind": "legacy-to-stage2-review-bundle",
            "signed": False, "installable": False,
            "game_compatibility_verified": False,
            "source_build_id": origin, "target_build_id": target,
            "inventory_revision": plan["inventory_revision"],
            "dependency_diagnostics": plan.get("dependency_diagnostics"),
            "assets": exported,
            "warning": "Reference files only. No PAK signature, engine conversion, new entries, map registration, or in-game validation."
        }
        with (output / "candidate-manifest.json").open("x", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        succeeded = True
        return output / "candidate-manifest.json"
    finally:
        if not succeeded:
            # Delete only the newly allocated external folder, never source.
            shutil.rmtree(output)
