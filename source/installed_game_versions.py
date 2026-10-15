"""Read-only installed Evolve game/client executable version inspection.

Reads Windows VERSIONINFO resource metadata; does not run, patch, hash or
enumerate a game process. The normal Modded Evolve client EXE is read from
the existing Setup/swap/launcher_config.json created during first-run setup.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path


def windows_file_version(executable):
    """Return a dotted fixed-file version or None if unreported/unavailable."""
    path = Path(executable)
    if os.name != "nt" or not path.is_file() or path.suffix.casefold() != ".exe":
        return None
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        get_size = version.GetFileVersionInfoSizeW
        get_size.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint))
        get_size.restype = ctypes.c_uint
        get_info = version.GetFileVersionInfoW
        get_info.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint,
                             ctypes.c_void_p)
        get_info.restype = ctypes.c_int
        query = version.VerQueryValueW
        query.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p,
                          ctypes.POINTER(ctypes.c_void_p),
                          ctypes.POINTER(ctypes.c_uint))
        query.restype = ctypes.c_int
        size = get_size(str(path), None)
        if not 1 <= size <= 8 * 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not get_info(str(path), 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint(0)
        if not query(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        if length.value < 16 or not pointer.value:
            return None
        words = (ctypes.c_uint32 * 4).from_address(pointer.value)
        # VS_FIXEDFILEINFO signature followed by dwStrucVersion,
        # dwFileVersionMS and dwFileVersionLS.
        if words[0] != 0xFEEF04BD:
            return None
        major, minor = words[2] >> 16, words[2] & 65535
        patch, build = words[3] >> 16, words[3] & 65535
        if (major, minor, patch, build) == (0, 0, 0, 0):
            return None
        return f"{major}.{minor}.{patch}.{build}"
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def client_executable(swap_dir):
    """Read the explicitly configured launcher, never guess random EXEs."""
    config = Path(swap_dir) / "launcher_config.json"
    try:
        if not config.is_file() or config.stat().st_size > 16384:
            return None
        data = json.loads(config.read_text(encoding="utf-8"))
        candidate = Path(data.get("launcher", ""))
        if (candidate.suffix.casefold() != ".exe"
                or candidate.name.casefold() == "evolve.exe"
                or not candidate.is_file()):
            return None
        return candidate
    except (OSError, ValueError, TypeError):
        return None


def installed_versions(game_root, swap_dir):
    """Safe, separately labelled game/client version metadata for Home."""
    game = (Path(game_root) / "bin64_SteamRetail" / "Evolve.exe"
            if game_root else None)
    launcher = client_executable(swap_dir)
    return {
        "game": (windows_file_version(game) if game and game.is_file() else None),
        "client": windows_file_version(launcher) if launcher else None,
        "game_found": bool(game and game.is_file()),
        "client_found": launcher is not None,
        "client_name": launcher.name if launcher else "",
    }
