"""Offline account-profile inspection and copy-only editing.

Reads only explicitly selected files. Neither the installed launcher profile
nor the account service is written by this module. Encoded TRSEVLVPC caches
are recognized but not decoded or silently substituted with old backups.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

MAX_BYTES = 16 * 1024 * 1024
MAX_OWNABLES = 20000
MAX_PROGRESS = 2000
PROFILE_HEADER = b"TRSEVLVPC"


def load_snapshot(filename):
    path = Path(filename).resolve(strict=True)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Not a supported, bounded profile file.")
    raw = path.read_bytes()
    if raw.startswith(PROFILE_HEADER):
        raise ValueError(
            "This is the encoded TRSEVLVPC profile cache, not editable JSON. "
            "Choose an explicitly exported readable backup for inspection.")
    try:
        obj = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Profile is not readable JSON; no changes were made.") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("Ownables"), list) or not isinstance(obj.get("XPProgression"), list):
        raise ValueError("Expected a profile with Ownables and XPProgression arrays.")
    owned, levels = obj["Ownables"], obj["XPProgression"]
    if (len(owned) > MAX_OWNABLES or len(levels) > MAX_PROGRESS or
            any(not isinstance(x, str) or len(x) > 200 for x in owned) or
            any(not isinstance(x, dict) or not isinstance(x.get("Category"), str)
                or not isinstance(x.get("Level"), (int, float))
                or not isinstance(x.get("XP"), (int, float))
                for x in levels)):
        raise ValueError("Profile exceeds limits or contains invalid progression.")
    return obj


def read_catalog(filename):
    """Read explicitly selected ownable lists (one identifier per line)."""
    path = Path(filename).resolve(strict=True)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 512000:
        raise ValueError("Invalid ownables catalog.")
    rows = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        name = raw.strip()
        if name and not name.startswith("#"):
            if not re.fullmatch(r"[\w.-]{1,160}", name):
                raise ValueError("Unexpected catalog identifier.")
            rows.append(name)
    return tuple(dict.fromkeys(rows))


def classification(ownable, skin_catalog=(), perk_catalog=()):
    if ownable in skin_catalog:
        return "Skin / outfit"
    if ownable in perk_catalog:
        return "Perk"
    if ownable.casefold().endswith(("skin", "outfit")):
        return "Skin / outfit (inferred)"
    if "perk" in ownable.casefold():
        return "Perk (inferred)"
    return "Other"


def compare_snapshots(left, right):
    """Compare readable snapshots without treating them as server account state."""
    previous = set(left["Ownables"])
    current = set(right["Ownables"])
    a = {x["Category"]: (x["Level"], x["XP"]) for x in left["XPProgression"]}
    b = {x["Category"]: (x["Level"], x["XP"]) for x in right["XPProgression"]}
    return {
        "added": tuple(sorted(current - previous)),
        "removed": tuple(sorted(previous - current)),
        "progression": tuple((name, a.get(name), b.get(name)) for name in sorted(a.keys() | b.keys()) if a.get(name) != b.get(name)),
    }


def build_copy(profile, ownables, progression):
    """Preserve every unrecognized field while replacing selected known lists."""
    result = deepcopy(profile)
    if len(ownables) > MAX_OWNABLES or len(progression) > MAX_PROGRESS:
        raise ValueError("Proposed profile exceeds safety limits.")
    for x in ownables:
        if not isinstance(x, str) or len(x) > 200:
            raise ValueError("Invalid ownable identifier.")
    result["Ownables"] = sorted(set(ownables), key=str.casefold)
    lookup = {str(x["Category"]): x for x in result["XPProgression"]}
    for category, level, xp in progression:
        if category not in lookup or not (0 <= level <= 1000 and 0 <= xp <= 1_000_000_000):
            raise ValueError("Invalid or unknown progression category.")
        lookup[category]["Level"] = int(level)
        lookup[category]["XP"] = int(xp)
    return result


def export_new_copy(profile, destination):
    """Never overwrite existing files; callers select a separate test location."""
    path = Path(destination).expanduser()
    if path.suffix.casefold() != ".json" or path.exists():
        raise ValueError("Choose a new, non-existing .json file for the offline copy.")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("Select an existing, ordinary destination directory.")
    payload = json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("Proposed profile copy exceeds file size limit.")
    with path.open("xb") as out:
        out.write(payload)
    return path
