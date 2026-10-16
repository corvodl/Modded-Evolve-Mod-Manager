"""Small, private installation receipts. Never record credentials or raw logs."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid
from mod_profiles import _atomic_json

MAX_RECEIPTS = 300

def _folder(root):
    return Path(root).resolve() / "Receipts"

def write_import_receipt(root, profile_id, bundle, ids, *, existing=False):
    if len(ids) != len(bundle.entries):
        raise ValueError("Receipt must describe every reviewed mod.")
    folder = _folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    entries = [{
        "mod_id": mid, "target": entry.target,
        "sha256": entry.sha256, "changed_assets": len(entry.changed_entries),
        "file": entry.pak_filename
    } for mid, entry in zip(ids, bundle.entries)]
    record = {
        "schema": 1, "id": ident, "profile_id": profile_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "imported_not_staged", "reused_library": bool(existing),
        "entries": entries,
        "note": "Import verified. Staging outcome has not been confirmed."
    }
    _atomic_json(folder / (ident + ".json"), record)
    return ident

def verify_staged_profile(root, profile_id, stage, swap, expected_ids=()):
    """Read-only post-worker verification of real bytes and recovery metadata.

    Both staged PAK hashes and their before.pak backups must agree with
    the complete journal. Reject duplicate/extra/missing applied mod IDs.
    """
    from mod_profiles import ModProfiles
    from universal_stage import digest
    from launch_integration import state_status
    store = ModProfiles(root)
    stage, swap = Path(stage).resolve(), Path(swap).resolve()
    state = store.status()
    if state["in_progress"] or state["pending"] or state["profile_id"] != profile_id:
        raise RuntimeError("Profile staging is incomplete or belongs to another collection.")
    phase = state_status(swap).phase
    if phase not in ("missing", "restored", "prepared"):
        raise RuntimeError("Game swap journal is not in a safe restored/prepared state.")
    profile = next((p for p in store.list_profiles() if p["id"] == profile_id), None)
    if profile is None:
        raise RuntimeError("Applied Mods Collection no longer exists.")
    enabled = {m["id"]: m for m in profile["mods"] if m["enabled"]}
    applied = store._applied(stage, swap)
    records = applied["entries"]
    ids = [r["mod_id"] for r in records]
    if (len(ids) != len(set(ids)) or set(ids) != set(enabled)
            or not set(expected_ids).issubset(set(ids))):
        raise RuntimeError("Applied journal and enabled PAKs do not match.")
    if state["entry_count"] != len(records):
        raise RuntimeError("Applied journal entry count mismatch.")
    for item in records:
        mod = enabled[item["mod_id"]]
        if item.get("relative", "").casefold() != mod["relative"].casefold():
            raise RuntimeError("Profile journal target PAK mismatch.")
        target = stage / "paks" / Path(*mod["relative"].split("/"))
        if not target.is_file() or digest(target) != mod["sha256"]:
            raise RuntimeError("Staged PAK hash verification failed: " + mod["relative"])
        source = Path(mod["source"])
        if not source.is_file() or digest(source) != mod["sha256"]:
            raise RuntimeError("Imported mod source was altered: " + mod["relative"])
        backup = Path(item["backup"]).resolve()
        if not backup.is_relative_to(store.root / "Backups"):
            raise RuntimeError("Backup journal points outside manager recovery directory.")
        meta_path = backup / "metadata.json"
        if not meta_path.is_file():
            raise RuntimeError("Required recovery metadata is missing: " + mod["relative"])
        info = json.loads(meta_path.read_text(encoding="utf-8"))
        if (info.get("status") != "installed"
                or info.get("relative", "").casefold() != mod["relative"].casefold()
                or Path(info.get("stage", "")).resolve() != stage
                or Path(info.get("swap_dir", "")).resolve() != swap
                or info.get("new_sha256") != mod["sha256"]):
            raise RuntimeError("Recovery metadata disagrees with staged mod: " + mod["relative"])
        before = backup / "before.pak"
        if (not before.is_file() or
                digest(before) != info.get("old_sha256")):
            raise RuntimeError("Verified original PAK backup is missing or corrupt.")
        if info.get("has_ready"):
            ready_backup = backup / "ready-before.pak"
            if (not ready_backup.is_file() or
                    digest(ready_backup) != info.get("old_sha256")):
                raise RuntimeError("Prepared swap recovery backup is missing or corrupt.")
    return len(records)


def mark_staged(root, ident, stage, swap):
    """Only a complete, verified staged transaction earns a success receipt."""
    folder = _folder(root)
    path = folder / (ident + ".json")
    if not path.is_file():
        raise FileNotFoundError("Installation receipt missing.")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != 1 or not isinstance(doc.get("entries"), list):
        raise RuntimeError("Installation receipt is damaged.")
    try:
        count = verify_staged_profile(
            root, doc["profile_id"], stage, swap,
            tuple(item["mod_id"] for item in doc["entries"]))
    except Exception as exc:
        doc["status"] = "recovery_needed"
        doc["note"] = "Post-stage integrity check failed. Inspect staged PAKs and recovery backups."
        _atomic_json(path, doc)
        raise RuntimeError("Post-install verification failed; recovery may be needed: "
                           + str(exc)) from exc
    doc["status"] = "staged_verified"
    doc["note"] = f"Verified {count} staged PAKs, source SHA256 hashes and recovery journals."
    _atomic_json(path, doc)
    return count


def list_receipts(root, *, limit=40):
    folder = _folder(root)
    if not folder.is_dir():
        return ()
    found = []
    for path in sorted(folder.glob("*.json"), reverse=True)[:max(1, min(limit, MAX_RECEIPTS))]:
        if path.is_symlink() or path.stat().st_size > 32768:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("schema") == 1 and isinstance(doc.get("entries"), list):
                found.append(doc)
        except (ValueError, OSError):
            continue
    return tuple(found)
