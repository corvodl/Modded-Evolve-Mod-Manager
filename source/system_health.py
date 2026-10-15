"""Opt-in, bounded Windows diagnostics for the collapsed System Health panel.

Reads never change the game, firewall, registry or projects. Only explicitly
confirmed GUI actions call the narrow write functions below.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import zipfile

from launch_integration import state_status
from online_guard import offline_rule_active, set_offline, target_executable

try:
    import psutil
except ImportError:
    psutil = None

GAME = "evolve.exe"
LAUNCHER = "moddedevolvelauncher.exe"
TMP_SUFFIXES = frozenset((".tmp", ".part", ".partial"))
MIN_FREE = 2 * 1024**3
WER_KEY = r"SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\Evolve.exe"
WER_MARKER = "EvolveModManagerManaged"


def _mb(value):
    return f"{value / 1048576:.1f} MiB"


def _root(game_root):
    return Path(game_root).resolve() if game_root else None


def process_snapshot():
    """On-demand process snapshot; psutil ships with the manager on Windows."""
    if psutil is None:
        return ("Unavailable", "Process metrics library not installed.")
    found = {}
    for p in psutil.process_iter(["name", "pid", "create_time", "memory_info"]):
        try:
            name = (p.info["name"] or "").casefold()
            if name in (GAME, LAUNCHER):
                found[name] = p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not found:
        return ("Not running", "Evolve and its launcher are not running.")
    lines = []
    now = time.time()
    for name, proc in found.items():
        try:
            proc.cpu_percent(None)
            # One bounded sample; executed only by an explicit panel refresh.
            cpu = proc.cpu_percent(interval=0.08)
            ram = proc.memory_info().rss
            uptime = max(0, int(now - proc.create_time()))
            lines.append(f"{name} · PID {proc.pid} · CPU {cpu:.1f}% · RAM {_mb(ram)} · uptime {uptime//60}m")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            lines.append(f"{name} · Running (performance counters unavailable)")
    return ("Running", "\n".join(lines))


def game_running():
    if psutil is None:
        # Fail closed: do not modify firewall rules if process detection is unavailable.
        return True
    return any((p.info.get("name") or "").casefold() in (GAME, LAUNCHER)
               for p in psutil.process_iter(["name"]))


def firewall_snapshot(game_root):
    if os.name != "nt":
        return ("Unavailable", "Windows Firewall is only supported on Windows.")
    try:
        exe = target_executable(game_root)
        blocked = offline_rule_active(game_root)
        return (("Offline rule active" if blocked else "Manager rule absent"),
                ("Manager-owned outbound block is installed for " if blocked else
                 "No manager-owned outbound block is installed for ") + str(exe) +
                ". Standard game launch can be unblocked; a modded launch must enforce its own offline protection.")
    except (OSError, ValueError) as exc:
        return ("Not configured", str(exc))


def change_offline_rule(game_root, enable):
    """Mutate only EvolveModManager-Offline-*; never alter arbitrary rules."""
    if game_running():
        raise RuntimeError("Close Evolve and its launcher before changing an offline firewall rule.")
    return set_offline(game_root, bool(enable))


def storage_candidates(data_home, *, now=None, limit=1000):
    """Only stale partial downloads in owned Temp/Downloads. No PAKs or backups."""
    data = Path(data_home).resolve()
    result = []
    cutoff = (time.time() if now is None else now) - 86400
    for name in ("Temp", "Downloads"):
        folder = data / name
        if not folder.is_dir() or folder.is_symlink():
            continue
        for directory, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for file in files:
                candidate = Path(directory) / file
                if candidate.suffix.casefold() not in TMP_SUFFIXES or candidate.is_symlink():
                    continue
                try:
                    if not candidate.is_file() or candidate.stat().st_mtime > cutoff:
                        continue
                    result.append(candidate)
                except OSError:
                    continue
                if len(result) >= limit:
                    return result
    return result


def cleanup_stale_temps(data_home):
    """Must only be called after user confirmation; recheck each candidate."""
    files = storage_candidates(data_home)
    removed, freed = 0, 0
    for path in files:
        if path.is_symlink() or path.suffix.casefold() not in TMP_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            freed += size
        except OSError:
            pass
    return removed, freed


def storage_snapshot(data_home, projects_root, stage_root, swap_root):
    paths = [("Projects", projects_root), ("Staging", stage_root),
             ("Backup/swap", swap_root)]
    lines = []
    low = False
    for name, location in paths:
        path = Path(location).resolve()
        anchor = path
        while not anchor.exists() and anchor != anchor.parent:
            anchor = anchor.parent
        try:
            space = shutil.disk_usage(anchor)
            low |= space.free < MIN_FREE
            lines.append(f"{name}: {space.free / (1024**3):.1f} GiB available")
        except OSError as exc:
            lines.append(f"{name}: unavailable ({exc})")
    swap = state_status(Path(swap_root))
    lines.append(f"Swap journal: {swap.phase} · {swap.backups} original backup(s)")
    candidates = storage_candidates(data_home)
    lines.append(f"Manager-owned stale partial files: {len(candidates)}")
    critical = low or swap.phase in ("unsafe", "interrupted", "swapping", "restoring")
    return ("Attention needed" if critical else "Ready", "\n".join(lines))


def environment_snapshot(game_root):
    game = _root(game_root)
    if os.name != "nt":
        return ("Unavailable", f"Windows-only checks. Current platform: {platform.system()}.")
    system = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    needed = ("msvcr110.dll", "msvcp110.dll", "msvcr120.dll",
              "msvcp120.dll", "vcruntime140.dll", "d3dx9_43.dll",
              "xinput1_3.dll")
    missing = [file for file in needed if not (system / file).is_file()]
    try:
        target_executable(game_root)
        game_state = "Evolve.exe found"
    except (ValueError, OSError):
        game_state = "Game executable missing"
    lines = [game_state, "Windows: " + platform.platform(),
             "Windows runtime DLLs: " +
             (", ".join(missing) + " not found in System32" if missing else
              "listed components found in System32"),
             "DirectX components are presence checks, not a full runtime validation.",
             "Firewall: see the separate manager-owned rule check."]
    return ("Attention needed" if missing or game_state != "Evolve.exe found"
            else "Ready", "\n".join(lines))


def _wer_read():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WER_KEY, 0, winreg.KEY_READ) as key:
            values = {}
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = (value, kind)
                index += 1
            return values
    except FileNotFoundError:
        return {}


def dump_snapshot(data_home):
    if os.name != "nt":
        return ("Unavailable", "Windows Error Reporting LocalDumps requires Windows.")
    try:
        values = _wer_read()
        if not values:
            return ("Not configured", "Crash dumps are off. Enable only if diagnosing a launch failure.")
        if WER_MARKER not in values:
            return ("Externally configured", "Existing Evolve.exe crash-dump settings are managed outside this app; they will not be changed.")
        return ("Enabled", "Manager-owned WER minidumps save under " + str(Path(data_home) / "CrashDumps"))
    except (OSError, PermissionError) as exc:
        return ("Unknown", str(exc))


def configure_dumps(data_home, enabled):
    """Opt-in HKLM WER LocalDumps, strictly owned values; no global settings."""
    if os.name != "nt":
        raise OSError("Requires Windows.")
    import winreg
    folder = Path(data_home).resolve() / "CrashDumps"
    values = _wer_read()
    if enabled:
        if values and WER_MARKER not in values:
            raise RuntimeError("Existing crash dump settings are not manager-owned. No changes made.")
        if values and values.get(WER_MARKER, (None,))[0] != 1:
            raise RuntimeError("Unknown dump settings owner; refusing to overwrite.")
        folder.mkdir(parents=True, exist_ok=True)
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, WER_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DumpFolder", 0, winreg.REG_EXPAND_SZ, str(folder))
            winreg.SetValueEx(key, "DumpType", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "DumpCount", 0, winreg.REG_DWORD, 5)
            winreg.SetValueEx(key, WER_MARKER, 0, winreg.REG_DWORD, 1)
        return "Windows Error Reporting minidumps enabled for Evolve.exe."
    if not values:
        return "No manager-owned dump settings to remove."
    if values.get(WER_MARKER, (None,))[0] != 1:
        raise RuntimeError("Dump configuration is not manager-owned.")
    expected = {"DumpFolder": str(folder), "DumpType": 1, "DumpCount": 5}
    if any(values.get(k, (None,))[0] != v for k, v in expected.items()):
        raise RuntimeError("Dump settings were changed externally; refusing to overwrite them.")
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WER_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        for name in (*expected, WER_MARKER):
            winreg.DeleteValue(key, name)
    if len(values) == 4:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, WER_KEY)
    return "Manager-owned Evolve.exe dump configuration removed."


def capture_minidump(destination):
    """Explicit, on-demand MiniDumpWriteDump of the first Evolve.exe process."""
    if os.name != "nt" or psutil is None:
        raise RuntimeError("A Windows installation with process diagnostics is required.")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    target = next((p for p in psutil.process_iter(["name"])
                   if (p.info["name"] or "").casefold() == GAME), None)
    if target is None:
        raise RuntimeError("Evolve.exe is not running.")
    output = Path(destination)
    if output.exists():
        raise FileExistsError("Choose a new dump filename; existing files are not overwritten.")
    output.parent.mkdir(parents=True, exist_ok=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    dbg = ctypes.WinDLL("DbgHelp", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    dbg.MiniDumpWriteDump.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
    dbg.MiniDumpWriteDump.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, target.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with output.open("xb") as file:
            ok = dbg.MiniDumpWriteDump(handle, target.pid, msvcrt.get_osfhandle(file.fileno()),
                                       0, None, None, None)
            if not ok:
                error = ctypes.get_last_error()
                raise ctypes.WinError(error)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        kernel.CloseHandle(handle)
    return str(output)


def support_zip(destination, data_home, game_root, stage_root, swap_root, projects_root):
    """Only bounded logs and a read-only report. No dumps, keys or game assets."""
    out = Path(destination)
    if out.exists():
        raise FileExistsError("Choose a new support ZIP name.")
    logs = []
    data = Path(data_home)
    for folder in (data / "Logs",):
        if folder.is_dir():
            logs.extend(sorted(folder.glob("*.log"))[-12:])
    root = _root(game_root)
    if root:
        for candidate in (root / "Game.log", root / "log" / "Game.log"):
            if candidate.is_file():
                logs.append(candidate)
    report = {
        "generated_utc": datetime.utcnow().isoformat() + "Z",
        "process": process_snapshot(),
        "firewall": firewall_snapshot(game_root),
        "storage": storage_snapshot(data, projects_root, stage_root, swap_root),
        "environment": environment_snapshot(game_root),
        "dump_configuration": dump_snapshot(data),
        "privacy": "Logs may contain usernames and paths. Review before sharing. Dumps are not included.",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(out, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("system-health.json", json.dumps(report, indent=2))
            for i, file in enumerate(logs):
                try:
                    if not file.is_symlink() and file.stat().st_size <= 16 * 1024**2:
                        archive.write(file, f"logs/{i:02d}-{file.name}")
                except OSError:
                    pass
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return str(out)


def collect_snapshot(game_root, data_home, projects_root, stage_root, swap_root):
    """Stable map of five read-only checks, called only by the GUI worker."""
    checks = (
        ("firewall", lambda: firewall_snapshot(game_root)),
        ("process", process_snapshot),
        ("crash", lambda: dump_snapshot(data_home)),
        ("storage", lambda: storage_snapshot(data_home, projects_root, stage_root, swap_root)),
        ("environment", lambda: environment_snapshot(game_root)),
    )
    result = {}
    for key, check in checks:
        try:
            result[key] = check()
        except Exception as exc:
            result[key] = ("Unavailable", f"{type(exc).__name__}: {exc}")
    return result
