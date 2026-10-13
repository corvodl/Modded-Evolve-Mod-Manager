"""Persistent project identities and exact verified target archive ownership.

Only editable workspaces are registered; immutable library imports are never
used as output workspaces. Registry metadata cannot grant writer permissions.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from uuid import uuid4

VERSION = 1
CATALOG = "mod-projects.json"
MAX_PROJECTS = 4096


def _home(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _catalog(root):
    path = _home(root) / CATALOG
    if path.is_symlink():
        raise ValueError("Project catalog cannot be a symlink.")
    data = json.loads(path.read_text("utf-8")) if path.is_file() else {
        "version": VERSION, "projects": {}}
    if data.get("version") != VERSION or not isinstance(data.get("projects"), dict):
        raise ValueError("Unsupported project catalog.")
    return data


def _save(root, data):
    path = _home(root) / CATALOG
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with tmp.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def verified_target(workspace, stage):
    """Resolve the workspace's exact signing baseline, never current table row."""
    from evolve_pak_workspace import load_workspace
    info = load_workspace(Path(workspace))
    source = Path(info["source_pak"]).resolve(strict=True)
    base = (Path(stage).resolve(strict=True) / "paks").resolve(strict=True)
    if not source.is_file() or source.is_symlink() or not source.is_relative_to(base):
        raise ValueError("Project is not backed by a signed staged target archive.")
    relative = source.relative_to(base).as_posix()
    from universal_stage import allowed_paks
    allowed = {name.casefold() for name in allowed_paks(stage).values()}
    if relative.casefold() not in allowed:
        raise ValueError("Target archive is not in the verified staging plan.")
    return relative, source


def register_project(projects_root, workspace, stage, *,
                     source_build_id=None, archive_id=None, label=None):
    """Track a separate editable workspace without altering source content."""
    from evolve_pak_workspace import load_workspace
    root = _home(projects_root)
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir() or workspace == root or not workspace.is_relative_to(root):
        raise ValueError("Project must be an editable workspace inside Projects.")
    load_workspace(workspace)  # validate workspace manifest
    relative, source = verified_target(workspace, stage)
    data = _catalog(root)
    existing = next((key for key, row in data["projects"].items()
                     if row["workspace"] == str(workspace)), None)
    if len(data["projects"]) >= MAX_PROJECTS and existing is None:
        raise ValueError("Maximum project catalog size reached.")
    project_id = existing or uuid4().hex
    data["projects"][project_id] = {
        "workspace": str(workspace), "label": str(label or workspace.name)[:120],
        "target_archive": relative, "target_baseline": str(source),
        "source_build_id": source_build_id, "source_archive_id": archive_id,
        "target_build_family": "stage2"}
    _save(root, data)
    return project_id


def project_rows(projects_root, stage):
    """Include unregistered legacy editable workspaces without trusting labels."""
    from evolve_pak_workspace import load_workspace
    root = _home(projects_root)
    data = _catalog(root)
    rows = []
    seen = set()
    candidates = list(data["projects"].items())
    # Batch projects contain independent signed-archive workspaces in nested
    # directories. Discover each owner without treating a collection as one PAK.
    stack = [(root, 0)]
    seen_folders = 0
    registered = {row["workspace"] for _, row in candidates}
    while stack:
        directory, depth = stack.pop()
        seen_folders += 1
        if seen_folders > MAX_PROJECTS * 8:
            raise ValueError("Project directory scan limit exceeded.")
        if (directory / ".evolve-pak-workspace.json").is_file():
            workspace = directory.resolve()
            if str(workspace) not in registered:
                candidates.append(("legacy-" + uuid4().hex, {
                    "workspace": str(workspace), "label": directory.name,
                    "source_build_id": None, "source_archive_id": None}))
                registered.add(str(workspace))
            continue
        if depth >= 4:
            continue
        for child in directory.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            if child.name.casefold() in ("built", "backups", "editorbackups", ".build"):
                continue
            if child.resolve().is_relative_to(root):
                stack.append((child, depth + 1))
    if len(candidates) > MAX_PROJECTS:
        raise ValueError("Too many projects to display.")
    for project_id, entry in candidates:
        workspace = Path(entry["workspace"])
        if str(workspace) in seen:
            continue
        seen.add(str(workspace))
        row = dict(entry, id=project_id, status="missing", changed=0,
                   details="Workspace moved or deleted")
        if workspace.is_dir() and not workspace.is_symlink() and workspace.is_relative_to(root):
            try:
                info = load_workspace(workspace)
                relative, source = verified_target(workspace, stage)
                from asset_intelligence import validate_project
                changed, problems = validate_project(workspace)
                row.update(target_archive=relative, target_baseline=str(source),
                           changed=len(changed), status="needs-review" if problems else
                           "modified" if changed else "draft",
                           details="; ".join(problems[:3]))
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                row.update(status="unavailable", details=str(exc)[:240])
        rows.append(row)
    return tuple(rows)
