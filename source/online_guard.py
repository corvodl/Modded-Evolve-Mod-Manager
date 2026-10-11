"""Fail-closed, reversible outbound game isolation for modded launches."""
import hashlib
import os
from pathlib import Path
import subprocess


def target_executable(game_root):
    exe = Path(game_root).resolve() / "bin64_SteamRetail" / "Evolve.exe"
    if not exe.is_file():
        raise FileNotFoundError("Selected EvolveGame folder has no Evolve.exe.")
    return exe


def rule_name(exe):
    exe = Path(exe).resolve()
    if exe.name.casefold() != "evolve.exe":
        raise ValueError("Only Evolve.exe may be isolated.")
    digest = hashlib.sha256(str(exe).casefold().encode("utf-8")).hexdigest()[:20]
    return "EvolveModManager-Offline-" + digest


def _netsh(args, runner):
    tool = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "netsh.exe")
    return runner([tool, "advfirewall", "firewall", *args],
                  capture_output=True, text=True, timeout=25)


def set_offline(game_root, block, *, runner=None, platform=None):
    """Block if true; remove only our rule if false.

    Requires administrator rights. An error aborts a modded launch before the
    launcher runs. The rule stays installed until restoration or explicit override.
    """
    if (os.name if platform is None else platform) != "nt":
        raise OSError("Offline game isolation requires Windows.")
    runner = runner or subprocess.run
    exe = target_executable(game_root)
    name = rule_name(exe)
    show = ["show", "rule", "name=" + name]
    exists = _netsh(show, runner).returncode == 0
    if block:
        if exists:
            result = _netsh(["delete", "rule", "name=" + name], runner)
            if result.returncode:
                raise RuntimeError("Cannot refresh existing offline firewall rule.")
        result = _netsh(["add", "rule", "name=" + name, "dir=out",
                         "action=block", "program=" + str(exe),
                         "enable=yes", "profile=any"], runner)
        if result.returncode or _netsh(show, runner).returncode:
            details = ((result.stderr or "") + " " + (result.stdout or "")).strip()[:300]
            raise RuntimeError(
                "Could not enforce offline mode. Modded launch cancelled. "
                "Run the manager with administrator permissions. " + details)
        return True
    if not exists:
        return False
    result = _netsh(["delete", "rule", "name=" + name], runner)
    if result.returncode or _netsh(show, runner).returncode == 0:
        raise RuntimeError("Could not remove the manager's outbound firewall rule.")
    return False
