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

def mark_staged(root, ident, stage, swap):
    """Called only after the journalled ModProfiles worker reports success."""
    from mod_profiles import ModProfiles
    folder = _folder(root)
    path = folder / (ident + ".json")
    if not path.is_file():
        raise FileNotFoundError("Installation receipt missing.")
    doc = json.loads(path.read_text(encoding="utf-8"))
    status = ModProfiles(root).status()
    if (status["in_progress"] or status["pending"] or
            status["profile_id"] != doc["profile_id"]):
        raise RuntimeError("Cannot mark receipt staged: profile apply is incomplete.")
    entries = ModProfiles(root)._applied(Path(stage).resolve(), Path(swap).resolve())["entries"]
    applied_ids = {r["mod_id"] for r in entries}
    if not all(item["mod_id"] in applied_ids for item in doc["entries"]):
        raise RuntimeError("Cannot mark receipt staged: not all bundle PAKs are applied.")
    doc["status"] = "staged_verified"
    doc["note"] = "Profile worker completed; journal contains all bundle PAKs."
    _atomic_json(path, doc)

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
