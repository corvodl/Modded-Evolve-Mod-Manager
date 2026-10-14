"""Guarded Legacy model replacements into an existing Stage 2 *workspace*.

Opt-in, single existing-path model replacement only. Never invent new entries,
patch running processes, create archives, rewrite source imports, or install a
PAK. Compatible chunk tables are a necessary condition, not gameplay proof.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

from asset_library import _folder_for_identifier, _home, _load_manifest
from build_collections import build_index
from evolve_pak_workspace import load_workspace, normalized_name, sha_file
from legacy_stage2_transfer import _context
from model_asset import is_model, validate_model_replacement, replace_model, MAX_MODEL_BYTES
from project_catalog import verified_target


def _provider(index, virtual, build_id, *, selected_archive=None):
    from multi_pak_assets import _resolve
    hits, state = _resolve(index, virtual, build_id=build_id)
    if state != "found" or len(hits) != 1:
        raise ValueError("Asset cannot be resolved uniquely within its own build: " + virtual)
    hit = hits[0]
    if selected_archive and hit.get("_asset_library") != selected_archive:
        raise ValueError("The chosen Legacy archive does not own this exact model path.")
    return hit


def _library_model(hit, virtual, library):
    aid = hit["_asset_library"]
    folder = _folder_for_identifier(aid, library)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Source archive is not a safe library import.")
    manifest = _load_manifest(folder)
    matching = [e for e in manifest["entries"]
                if normalized_name(e["path"]).casefold() == virtual.casefold()]
    if len(matching) != 1:
        raise ValueError("Model entry is missing or duplicated in the import manifest.")
    item = matching[0]
    name = normalized_name(item["path"])
    base = folder / "files"
    if base.is_symlink():
        raise ValueError("Library import is a symbolic link.")
    base = base.resolve(strict=True)
    path = base.joinpath(*name.split("/"))
    if (path.is_symlink() or not path.is_file() or
            not path.resolve().is_relative_to(base) or
            path.stat().st_size > MAX_MODEL_BYTES):
        raise ValueError("Imported model is missing, oversized or unsafe.")
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != item.get("sha256") or len(payload) != item.get("bytes"):
        raise ValueError("Imported model bytes no longer match provenance.")
    return payload, digest


def review_model_replacement(source_build, target_build, virtual, project,
                             stage, *, root=None, source_archive=None):
    """Verify Stage 2 target owner, both SHA-256s and model chunk compatibility.

    The existing target Stage 2 *project's* archive and entry must match the
    registered target build. Source assets are never placed in the live game.
    """
    library = _home(root)
    inventory, source, target = _context(source_build, target_build, root=library)
    virtual = normalized_name(virtual)
    if (not is_model(virtual) or virtual.casefold().startswith("levels/")):
        raise ValueError("Only an existing-path static/character model may use this executor.")
    from level_asset_mount import map_relative_asset
    if map_relative_asset(virtual):
        raise ValueError("Map-local geometry requires a scoped map review, not a global replacement.")
    origin = _provider(build_index(source_build, root=library),
                       virtual, source_build, selected_archive=source_archive)
    destination = _provider(build_index(target_build, root=library),
                            virtual, target_build)
    source_bytes, source_hash = _library_model(origin, virtual, library)
    target_bytes, target_hash = _library_model(destination, virtual, library)

    workspace = Path(project).resolve(strict=True)
    stage = Path(stage).resolve(strict=True)
    signed_relative, staged_pak = verified_target(workspace, stage)
    workspace_info = load_workspace(workspace)
    if workspace_info.get("reference_only"):
        raise ValueError("Reference-only projects cannot receive editable transfers.")
    if sha_file(staged_pak) != workspace_info.get("source_sha256"):
        raise ValueError("The staged signing baseline has changed since project extraction.")
    matched = [
        (aid, rec) for aid, rec in target["members"].items()
        if aid == destination["_asset_library"]
    ]
    if len(matched) != 1:
        raise ValueError("Target model has no unique registered Stage 2 archive owner.")
    member = matched[0][1]
    # Require a recorded exact stage-relative owner; mere basename matching
    # could substitute a different signed PAK with a colliding filename.
    if normalized_name(member["relative_path"]).casefold() != signed_relative.casefold():
        raise ValueError("Stage 2 workspace does not belong to the target build archive.")
    records = [record for record in workspace_info["entries"]
               if normalized_name(record["path"]).casefold() == virtual.casefold()]
    if len(records) != 1 or records[0].get("format") != "raw":
        raise ValueError("Target model does not exist as a raw entry in this project.")
    record = records[0]
    files_root = workspace / "files"
    if files_root.is_symlink():
        raise ValueError("Project files directory cannot be a symbolic link.")
    file = files_root.joinpath(*normalized_name(record["path"]).split("/"))
    if (file.is_symlink() or not file.is_file() or
            not file.resolve().is_relative_to(files_root.resolve())):
        raise ValueError("Project target file is missing or unsafe.")
    current = file.read_bytes()
    actual_hash = hashlib.sha256(current).hexdigest()
    if actual_hash != record["sha256"]:
        raise ValueError("Target project entry was edited; review and restore it first.")
    if current != target_bytes or actual_hash != target_hash:
        raise ValueError("Target project baseline differs from the registered Stage 2 model.")
    model_info = validate_model_replacement(current, source_bytes)
    return {
        "schema": 1, "type": "existing-path-model-workspace-replacement",
        "source_build_id": source_build, "target_build_id": target_build,
        "source_archive_id": origin["_asset_library"],
        "target_archive_id": destination["_asset_library"],
        "inventory_revision": inventory["revision"], "path": virtual,
        "project": str(workspace), "staged_archive": signed_relative,
        "staged_archive_sha256": workspace_info["source_sha256"],
        "source_sha256": source_hash, "target_sha256": target_hash,
        "model_format": model_info.format_name,
        "model_chunks": model_info.chunks, "model_bytes": model_info.file_length,
        "action": "review-compatible-existing-path",
        "requires_explicit_approval": True,
        "staging": "isolated-project-only",
        "signed_pak_written": False,
        "validated_in_game": False,
        "warning": "Chunk compatibility does not verify materials, rigs, animations, collision or in-game behavior. Build and sign through existing Stage 2 tools after review."
    }


def apply_reviewed_model(plan, stage, *, root=None):
    """Apply a *re-reviewed* model via existing atomic editor backup flow.

    User confirmation must occur outside this function. Recompute all evidence
    and refuse any changed source, target, build membership or stage baseline.
    """
    if plan.get("type") != "existing-path-model-workspace-replacement":
        raise ValueError("Not an existing-path model review.")
    latest = review_model_replacement(
        plan["source_build_id"], plan["target_build_id"], plan["path"],
        plan["project"], stage, root=root, source_archive=plan["source_archive_id"])
    if latest != plan:
        raise ValueError("Transfer evidence changed since approval; review again.")
    library = _home(root)
    src = _provider(build_index(plan["source_build_id"], root=library),
                    plan["path"], plan["source_build_id"],
                    selected_archive=plan["source_archive_id"])
    # Freeze validated source bytes in a short-lived private snapshot. This
    # prevents an external library edit between review and the existing native
    # replacement code's later file read (a source-side TOCTOU).
    payload, verified_hash = _library_model(src, plan["path"], library)
    if verified_hash != plan["source_sha256"]:
        raise ValueError("Legacy model changed since the user approved it.")
    with tempfile.TemporaryDirectory(prefix="legacy-model-reviewed-") as scratch:
        copy = Path(scratch) / Path(plan["path"]).name
        copy.write_bytes(payload)
        # replace_model rechecks the unchanged target hash, creates its own
        # EditorBackups snapshot, and swaps only this one workspace file.
        changed = replace_model(plan["project"], plan["path"], copy,
                                plan["target_sha256"])
    return {"project": plan["project"], "path": plan["path"],
            "changed": hashlib.sha256(changed).hexdigest() != plan["target_sha256"],
            "source_sha256": plan["source_sha256"],
            "signed_pak_written": False, "installed": False}
