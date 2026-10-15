"""Read-only guard for an orphaned custom-key inject.dll after modded play.

Do not silently trust a .customkey-original suffix as proof of provenance:
the manager must preserve both copies and require an explicit restore action.
This check is fast (two 4 KiB reads) and deliberately never writes game files.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

SHIM_SIZE = 4096
KEY_OFFSET = 0x870
KEY_SIZE = 140


@dataclass(frozen=True)
class InjectorCheck:
    state: str
    current_sha256: str = ""
    backup_sha256: str = ""
    message: str = ""

    @property
    def blocks_normal_launch(self) -> bool:
        return self.state in {"key-patched", "different", "backup-present", "unsafe"}


def inspect_injector(game_root: Path) -> InjectorCheck:
    """Inspect ONLY bin64_SteamRetail/inject.dll and its protected original.

    Identical backups still need to be handled as leftover swap state. A
    difference confined to the 140-byte RSA key is diagnostically useful,
    but is NOT authorization to delete, overwrite or restore automatically.
    """
    root = Path(game_root)
    folder = root / "bin64_SteamRetail"
    active = folder / "inject.dll"
    original = folder / "inject.dll.customkey-original"
    if not original.exists() and not original.is_symlink():
        return InjectorCheck("no-backup", message="No protected injector backup found.")
    if (folder.is_symlink() or active.is_symlink() or original.is_symlink() or
            not original.is_file() or not active.is_file()):
        return InjectorCheck("unsafe", message="Injector backup or installed file is missing or linked; inspect before restoring.")
    try:
        current = active.read_bytes()
        backup = original.read_bytes()
    except OSError as error:
        return InjectorCheck("unsafe", message=f"Cannot read injector and backup: {error}")
    if len(current) != SHIM_SIZE or len(backup) != SHIM_SIZE:
        return InjectorCheck("unsafe", message="Injector or protected backup is not 4096 bytes; no automatic restoration.")
    current_hash = hashlib.sha256(current).hexdigest()
    backup_hash = hashlib.sha256(backup).hexdigest()
    if current == backup:
        return InjectorCheck("backup-present", current_hash, backup_hash,
                             "Protected original backup remains after a swap; inspect and restore the swap journal.")
    outside_key_equal = (current[:KEY_OFFSET] == backup[:KEY_OFFSET] and
                         current[KEY_OFFSET + KEY_SIZE:] == backup[KEY_OFFSET + KEY_SIZE:])
    state = "key-patched" if outside_key_equal else "different"
    why = ("Only the injector RSA public-key region differs." if outside_key_equal else
           "Injector differs outside the expected RSA key region.")
    return InjectorCheck(state, current_hash, backup_hash,
                         why + " Restore originals with Play & Restore after closing the game and launcher.")


def require_clean_injector(game_root: Path) -> InjectorCheck:
    """Fail closed before another modded launch when a backup was left behind."""
    result = inspect_injector(game_root)
    if result.blocks_normal_launch:
        raise RuntimeError(result.message)
    return result
