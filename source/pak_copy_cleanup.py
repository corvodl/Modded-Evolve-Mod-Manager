"""Conservative review-first game PAK backup cleanup.

Never selects live .pak files, *.customkey-original, active ready copies,
incomplete copies, signing archives or unverified files for deletion.
Only a verified byte-identical redundant copy is eligible for explicit
Recycle Bin relocation, after journal and game-process checks.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re

MAX_CANDIDATES = 1800
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
RECOVERED = re.compile(r"\.customkey-recovery-mod-[a-zA-Z0-9_-]+$", re.I)
PRESERVED = re.compile(r"(?:\.customkey-ready)?\.saved-[a-zA-Z0-9_-]+$", re.I)


@dataclass(frozen=True)
class PakCopy:
    path: Path
    original: Path
    bytes: int
    kind: str
    status: str
    detail: str
    sha256: str = ""
    retained_reference: Path | None = None

    @property
    def reclaimable(self):
        return self.status == "identical-copy"


def _game_root(game):
    root = Path(game).resolve(strict=True)
    if not (root / "bin64_SteamRetail" / "Evolve.exe").is_file():
        raise ValueError("Choose EvolveGame containing bin64_SteamRetail/Evolve.exe.")
    return root


def _safe_file(root, file):
    path = Path(file)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Symlink or non-file in game folder: " + str(path))
    relative = path.relative_to(root)
    parent = root
    for component in relative.parts[:-1]:
        parent = parent / component
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("Linked game directory requires manual review.")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("Game file escapes installation.")


def _snapshot_sha(file):
    h = hashlib.sha256()
    with file.open("rb") as stream:
        while True:
            block = stream.read(4 * 1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _matches_live(file, original):
    if not original.is_file() or original.is_symlink():
        return False, ""
    if file.stat().st_size != original.stat().st_size:
        return False, ""
    digest = _snapshot_sha(file)
    return digest == _snapshot_sha(original), digest


def _journal_references(paths, roots=()):
    """Find known manager manifests; fail closed on unreadable/huge manifests.

    Journals may be held elsewhere. A caller cannot claim a file is
    universally unreferenced, so the UI requires explicit user acknowledgement.
    """
    targets = {str(Path(p).resolve()).casefold() for p in paths}
    found = set()
    visited = 0
    for root in roots:
        if not root:
            continue
        base = Path(root)
        if not base.is_dir():
            continue
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs
                       if not (Path(directory) / d).is_symlink()
                       and d.casefold() not in ("cache", ".asset-cache",
                                               "files", "node_modules", ".git")]
            for name in names:
                if (name.casefold().startswith(("swaprecovery-", "preserved_files"))
                        or name.casefold() == "controlled_swap_state.json"):
                    visited += 1
                    if visited > 300:
                        raise ValueError("Too many recovery journals; inspect manually.")
                    item = Path(directory) / name
                    if item.is_symlink() or item.stat().st_size > MAX_JOURNAL_BYTES:
                        raise ValueError("Recovery journal is untrusted or too large: " + name)
                    data = item.read_text("utf-8").casefold().replace("\\\\", "\\")
                    for target in targets:
                        if target in data or target.replace("\\", "\\\\") in data:
                            found.add(target)
    return found


def scan_duplicate_paks(game, *, journal_roots=()):
    """Read-only scan; potentially expensive hashes should run in a worker."""
    root = _game_root(game)
    rows = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in names:
            lower = name.casefold()
            if ".pak." not in lower:
                continue
            name_pak = lower.find(".pak.")
            original = Path(base) / name[:name_pak+4]
            file = Path(base) / name
            _safe_file(root, file)
            if original.is_symlink():
                raise ValueError("Active PAK is a link: " + str(original))
            kind = name[name_pak+5:]
            if lower.endswith(".customkey-original"):
                status, why = "protected", "Original restoration backup. Never clean here."
            elif lower.endswith((".customkey-ready", ".customkey-ready.partial")):
                status, why = "protected", "Required/partial prepared copy. Restore or inspect."
            elif RECOVERED.search(name) or PRESERVED.search(name):
                status, why = "review", "Historical recovery copy; checking exact bytes."
            else:
                status, why = "protected", "Unrecognized backup suffix. Manual review."
            size = file.stat().st_size
            rows.append(PakCopy(file, original, size, kind, status, why))
            if len(rows) > MAX_CANDIDATES:
                raise ValueError("More than 1,800 backup-like files. Narrow scan.")
    # Group historic files by canonical live PAK and EXACT SHA-256. The live
    # file is the preferred keeper. If a historic version differs from live,
    # retain one intact backup of that exact version and offer ONLY further
    # identical backups for recycling. Never discard the last copy of a version.
    from collections import defaultdict
    groups = defaultdict(list)
    live_digests = {}
    for i, record in enumerate(rows):
        if record.status != "review":
            continue
        _safe_file(root, record.path)
        digest = _snapshot_sha(record.path)
        groups[(str(record.original).casefold(), record.bytes, digest)].append(i)
    eligible = []
    for (original_name, size, digest), candidates in groups.items():
        first = rows[candidates[0]]
        original = first.original
        if not original.is_file() or original.is_symlink():
            for i in candidates:
                record = rows[i]
                rows[i] = PakCopy(record.path, record.original, record.bytes,
                    record.kind, "protected", "Active PAK missing. Preserve backups.")
            continue
        _safe_file(root, original)
        token = (str(original).casefold(), size)
        if token not in live_digests:
            live_digests[token] = (
                _snapshot_sha(original) if original.stat().st_size == size else "")
        live_matches = bool(live_digests[token] == digest)
        # For alternate historic versions choose the oldest stable copy as the
        # keeper. Its exact bytes must be retained even after other copies go.
        stable = sorted(candidates, key=lambda i: (
            rows[i].path.stat().st_mtime_ns, str(rows[i].path).casefold()))
        keeper = original if live_matches else rows[stable[0]].path
        for i in candidates:
            record = rows[i]
            if not live_matches and i == stable[0]:
                rows[i] = PakCopy(record.path, record.original, record.bytes,
                    record.kind, "protected",
                    "Retained copy of a distinct historical PAK version.", digest)
                continue
            rows[i] = PakCopy(record.path, record.original, record.bytes,
                record.kind, "identical-copy",
                "Identical to retained " +
                ("active PAK." if live_matches else "historical version.") +
                " Explicit confirmation required.", digest, keeper)
            eligible.append(record.path)
    if eligible:
        referenced = _journal_references(eligible, journal_roots)
        for i, record in enumerate(rows):
            if str(record.path.resolve()).casefold() in referenced:
                rows[i] = PakCopy(record.path, record.original, record.bytes,
                                  record.kind, "protected",
                                  "Referenced by a known recovery journal. Preserve.")
    return tuple(sorted(rows, key=lambda row: str(row.path).casefold()))


def validate_recycle(game, swap, rows, *, journal_roots=()):
    """Revalidate before each user-approved Recycle Bin move.

    Does not delete or move anything; caller uses QFile.moveToTrash on Windows.
    """
    from launch_integration import state_status
    from orphaned_swap_recovery import ensure_game_closed
    root = _game_root(game)
    ensure_game_closed()
    state = state_status(Path(swap))
    if state.phase not in ("missing", "restored"):
        raise ValueError("Swap is not restored. Restore Game Files first: " + state.phase)
    if state.game and Path(state.game).resolve() != root:
        raise ValueError("Swap journal belongs to another game installation.")
    # Even with no journal, refuse cleanup if original backups are present.
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        if any(name.casefold().endswith((".customkey-original",
                                        ".customkey-ready.partial")) for name in names):
            raise ValueError("Original or incomplete swap backups exist; restore first.")
    selected = []
    for row in rows:
        if not isinstance(row, PakCopy) or not row.reclaimable or not row.sha256:
            raise ValueError("Only verified, user-selected duplicate copies may be recycled.")
        _safe_file(root, row.path)
        if row.retained_reference is None:
            raise ValueError("No retained identical copy was verified.")
        _safe_file(root, row.original)
        _safe_file(root, row.retained_reference)
        if (row.path in (row.original, row.retained_reference)
                or row.path.stat().st_size != row.bytes
                or row.retained_reference.stat().st_size != row.bytes):
            raise ValueError("Backup changed since scan. Rescan.")
        # Both candidate and retained version must still have the same bytes.
        if (_snapshot_sha(row.path) != row.sha256
                or _snapshot_sha(row.retained_reference) != row.sha256):
            raise ValueError("Backup is no longer identical to retained version. Rescan.")
        selected.append(row.path)
    if len({str(p).casefold() for p in selected}) != len(selected):
        raise ValueError("Duplicate selection")
    if selected:
        referenced = _journal_references(selected, journal_roots)
        if referenced:
            raise ValueError("A selected copy is referenced by a recovery journal.")
    return tuple(selected)
