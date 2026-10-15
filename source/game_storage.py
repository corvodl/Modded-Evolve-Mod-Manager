"""Read-only storage accounting and conservative old-setup lifecycle checks.

Storage root is a *preference for new setups*, not an instruction to move the
active signing stage or swap journal. Never delete unrecognized directories.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil

CATEGORIES = ("Active setup", "Old setups", "Projects", "Asset Library", "Other")
MAX_FILES = 400000
MAX_REFERENCE_FILES = 20000
MANIFEST_NAMES = (".evolve-pak-workspace.json", "profiles.json", "applied.json")


def canonical(path):
    return Path(path).expanduser().resolve()


def is_within(path, folder):
    path, folder = canonical(path), canonical(folder)
    return path == folder or folder in path.parents


def data_location(stage, swap, default):
    """Prefer shared parent only if the two active paths are a matched pair."""
    s, w = canonical(stage), canonical(swap)
    return s.parent if s.name == "staged" and w.name == "swap" and s.parent == w.parent else canonical(default)


def validate_storage_root(root, *, game=None):
    path = canonical(root)
    if path == path.parent:
        raise ValueError("Choose a dedicated Game Storage folder, not a drive root.")
    if game and (is_within(path, game) or is_within(game, path)):
        raise ValueError("Keep mod storage separate from the installed game folder.")
    if path.is_symlink():
        raise ValueError("Mod storage folder may not be a symbolic link.")
    return path


def directory_size(folder):
    """No symlink traversal; detect incomplete scans instead of guessing size."""
    path = Path(folder)
    if not path.exists():
        return 0
    if path.is_symlink():
        raise ValueError("Refusing a linked storage path.")
    total, count = 0, 0
    for parent, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(parent) / name).is_symlink()]
        for name in files:
            entry = Path(parent) / name
            count += 1
            if count > MAX_FILES:
                raise RuntimeError("Storage contains too many files to scan safely.")
            if entry.is_symlink():
                continue
            try:
                total += entry.stat().st_size
            except OSError as exc:
                raise RuntimeError("Storage scan could not read " + str(entry)) from exc
    return total


def _setup_folders(storage_root):
    folder = canonical(storage_root)
    if not folder.is_dir():
        return ()
    found = []
    for path in folder.iterdir():
        if (path.is_dir() and not path.is_symlink() and
                path.name.startswith(("Setup_", "EvolveRefresh_"))):
            found.append(path)
    return tuple(sorted(found, key=lambda item: item.name.casefold()))


def _active_root(stage, swap):
    stage, swap = canonical(stage), canonical(swap)
    if (stage.name != "staged" or swap.name != "swap"
            or stage.parent != swap.parent):
        return None
    return stage.parent


def measure_storage(data_home, storage_root, stage, swap, projects):
    """Return exact measured bytes; categories do not double-count active GameSets."""
    data = canonical(data_home)
    storage = canonical(storage_root)
    active = _active_root(stage, swap)
    buckets = {key: 0 for key in CATEGORIES}
    setups = []
    measured = set()

    # Count active even when it lives in a different, custom storage location.
    if active and active.is_dir():
        buckets["Active setup"] += directory_size(active)
        measured.add(active)
    for setup in _setup_folders(storage):
        if setup in measured:
            continue
        if active is not None and setup == active:
            continue
        count = directory_size(setup)
        buckets["Old setups"] += count
        setups.append((setup, count))
        measured.add(setup)
    for label, folder in (("Projects", projects),
                          ("Asset Library", data / "AssetLibrary")):
        dest = canonical(folder)
        if dest in measured or any(is_within(dest, used) for used in measured):
            continue
        buckets[label] += directory_size(dest)
        measured.add(dest)
    # Current Roots and GameSets that are not setup folders are included in Other.
    for root in (data / "Profiles", data / "Logs", data / "Setup",
                 data / "SetupSeeds", data / "RecoveryLogs", data / "Backups"):
        folder = canonical(root)
        if folder not in measured and not any(is_within(folder, used) for used in measured):
            buckets["Other"] += directory_size(folder)
            measured.add(folder)
    # Unknown non-setup entries in selected GameSets count as Other, not cleanups.
    if storage.is_dir() and storage not in measured:
        for item in storage.iterdir():
            if item not in measured and not any(is_within(item, used) for used in measured):
                buckets["Other"] += directory_size(item)
    return {"bytes": buckets, "old_setups": tuple(setups),
            "total": sum(buckets.values()), "active": str(active or "")}


def _references(folder, *, max_files=MAX_REFERENCE_FILES):
    """Scan only known metadata; fail closed when a catalog is damaged."""
    root = Path(folder)
    if not root.is_dir():
        return ()
    documents, examined = [], 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(parent) / name).is_symlink()]
        for name in files:
            if name not in MANIFEST_NAMES:
                continue
            examined += 1
            if examined > max_files:
                raise RuntimeError("Too many project references to verify old setups.")
            file = Path(parent) / name
            if file.is_symlink() or file.stat().st_size > 4 * 1024 * 1024:
                raise RuntimeError("Unsafe or oversized project/profile manifest: " + str(file))
            try:
                documents.append(json.loads(file.read_text(encoding="utf-8")))
            except (ValueError, OSError) as exc:
                raise RuntimeError("Cannot verify project/profile reference " + str(file)) from exc
    return tuple(documents)


def _mentioned(value, target):
    if isinstance(value, str):
        # Compare directory separators and case on Windows, but not arbitrary
        # labels containing the setup's name.
        if not (":" in value or "/" in value or "\\" in value):
            return False
        try:
            return is_within(value, target)
        except (OSError, ValueError):
            return False
    if isinstance(value, dict):
        return any(_mentioned(item, target) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentioned(item, target) for item in value)
    return False


def cleanup_candidates(storage_root, active_stage, active_swap, projects_root,
                       profiles_root, *, game_root=None):
    """Return (folder, measured bytes, reason); reason=None means eligible."""
    from launch_integration import state_status
    root = canonical(storage_root)
    active = _active_root(active_stage, active_swap)
    # Any references in current projects and profiles protect previous stage.
    docs = _references(projects_root) + _references(profiles_root)
    result = []
    for path in _setup_folders(root):
        reason = None
        if path == active or is_within(active_stage, path) or is_within(active_swap, path):
            reason = "Active signing or swap setup"
        elif any(_mentioned(doc, path) for doc in docs):
            reason = "A project or Mod Profile still references this setup"
        elif not (path / "staged" / "rekey_plan.json").is_file():
            reason = "Missing signed-stage metadata"
        elif not (path / "refresh_report.json").is_file():
            reason = "Missing completed setup report"
        else:
            try:
                report = json.loads((path / "refresh_report.json").read_text("utf-8"))
                state = state_status(path / "swap")
                if report.get("status") != "complete":
                    reason = "Setup build/recovery is incomplete"
                elif canonical(report["stage"]) != canonical(path / "staged") or canonical(
                        report["swap"]) != canonical(path / "swap"):
                    reason = "Setup report belongs to another directory"
                elif state.phase != "restored":
                    reason = "Swap journal is not cleanly restored (" + state.phase + ")"
                elif state.backups:
                    reason = "Original-file recovery backups remain"
                elif (path / "swap" / "auto_restore_watch.lock").exists():
                    reason = "Auto-restore watcher lock is present"
                elif (path / "staged" / "REFRESHED-TO.json").exists():
                    marker = json.loads((path / "staged" / "REFRESHED-TO.json").read_text("utf-8"))
                    if marker.get("status") != "complete":
                        reason = "A refresh activation is unfinished"
                if game_root and report.get("game") and canonical(report["game"]) != canonical(game_root):
                    # Foreign installs need manual ownership inspection.
                    reason = reason or "Belongs to a different game installation"
                elif not report.get("game"):
                    reason = reason or "Game ownership cannot be verified"
            except (OSError, KeyError, TypeError, ValueError) as exc:
                reason = "Cannot verify recovery metadata: " + str(exc)
        result.append((path, directory_size(path), reason))
    return tuple(result)


def delete_old_setup(target, storage_root, active_stage, active_swap,
                     projects_root, profiles_root, *, game_root=None):
    """Second, immediate pre-delete validation; user must confirm in UI."""
    from universal_stage import ensure_closed
    from orphaned_swap_recovery import plan_recovery
    folder = canonical(target)
    root = canonical(storage_root)
    if folder.parent != root or folder.is_symlink():
        raise RuntimeError("Can only remove a reviewed, direct GameSets setup.")
    ensure_closed()
    choices = cleanup_candidates(root, active_stage, active_swap, projects_root,
                                 profiles_root, game_root=game_root)
    entry = next((v for v in choices if v[0] == folder), None)
    if entry is None or entry[2] is not None:
        raise RuntimeError("Setup is no longer safe to remove: " +
                           (entry[2] if entry else "not found"))
    report = json.loads((folder / "refresh_report.json").read_text("utf-8"))
    game = canonical(report["game"])
    if not game.is_dir() or plan_recovery(game):
        raise RuntimeError("Original game recovery is not verified; cleanup cancelled.")
    # Conservative: don't remove any old set while the game's prepared/backup
    # markers are present, even if the current swap journal is clean.
    from old_prepared import scan_prepared
    pending = scan_prepared(game)
    if any(pending.get(key) for key in ("backups", "partial", "ready")):
        raise RuntimeError("Original, prepared or interrupted game files still require recovery.")
    shutil.rmtree(folder)
    return folder


def reusable_setup(stage, swap, game, launcher):
    """Read-only decision: do not generate duplicate PAK sets on repeat setup."""
    from launch_integration import state_status, missing_scripts
    from universal_stage import allowed_paks
    from orphaned_swap_recovery import plan_recovery
    stage, swap, game = map(canonical, (stage, swap, game))
    launcher = canonical(launcher)
    if not (stage / "stage_status.json").is_file() or not (
            stage / "mykeys" / "private_key.pem").is_file():
        return False
    if (stage / "REFRESHED-TO.json").exists() or missing_scripts(swap):
        return False
    status = state_status(swap)
    if status.phase not in ("restored", "prepared") or status.backups:
        return False
    if canonical(status.stage) != stage or canonical(status.game) != game:
        return False
    if plan_recovery(game):
        return False
    try:
        plan = json.loads((stage / "rekey_plan.json").read_text("utf-8"))
        config = json.loads((swap / "launcher_config.json").read_text("utf-8"))
        signed = allowed_paks(stage)
        if canonical(plan["source_root"]) != game or canonical(config["launcher"]) != launcher:
            return False
        if not signed or any(not (stage / "paks" / Path(*rel.split("/"))).is_file()
                             for rel in signed.values()):
            return False
    except (KeyError, ValueError, OSError, TypeError):
        return False
    return True
