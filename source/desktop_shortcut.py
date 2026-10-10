"""User-requested Windows desktop shortcut for the portable manager.

Never writes into an EvolveGame installation or Data/Projects. Desktop is
resolved by Windows, so redirected Desktop folders and OneDrive are honored.
The icon is the same bundled red/black hunt.ico used by EvolveModManager.exe.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

SHORTCUT_NAME = 'Evolve Mod Manager.lnk'
_WINDOWS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'Windows could not locate your Desktop folder.'
}
$target = $env:EVOLVE_SHORTCUT_TARGET
$icon = $env:EVOLVE_SHORTCUT_ICON
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw 'The installed EvolveModManager.exe could not be found.'
}
if (-not (Test-Path -LiteralPath $icon -PathType Leaf)) {
    throw 'The red/black application icon could not be found.'
}
$path = Join-Path $desktop 'Evolve Mod Manager.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $target
$link.WorkingDirectory = Split-Path -Path $target -Parent
$link.IconLocation = "$icon,0"
$link.Description = 'Evolve Mod Manager'
$link.Save()
[Console]::WriteLine($path)
"""


def create_desktop_shortcut(executable=None, icon=None, *, timeout=25):
    """Create/rewrite one .lnk only when explicitly invoked.

    On normal packaged runs, executable defaults to the local manager EXE and
    icon defaults to its included hunt.ico, even when the exe lives in a
    portable folder with spaces. Unfrozen source runs require explicit paths.
    """
    if os.name != 'nt':
        raise OSError('Desktop shortcut creation is supported on Windows only.')
    if executable is None:
        if not getattr(sys, 'frozen', False):
            raise RuntimeError('Build the portable EvolveModManager.exe before creating a shortcut.')
        executable = Path(sys.executable)
    exe = Path(executable).resolve(strict=True)
    if exe.name.casefold() != 'evolvemodmanager.exe' or not exe.is_file():
        raise ValueError('Expected an installed EvolveModManager.exe, not a game executable.')
    if icon is None:
        icon = Path(__file__).resolve().parent / 'assets' / 'hunt.ico'
    ico = Path(icon).resolve(strict=True)
    if not ico.is_file() or ico.suffix.casefold() != '.ico':
        raise ValueError('Bundled Evolve Mod Manager icon is missing or invalid.')

    # Static encoded PowerShell script plus subprocess environment variables:
    # no executable path is interpolated into command source or shell syntax.
    import base64
    script = base64.b64encode(_WINDOWS_SCRIPT.encode('utf-16le')).decode('ascii')
    environment = os.environ.copy()
    environment['EVOLVE_SHORTCUT_TARGET'] = str(exe)
    environment['EVOLVE_SHORTCUT_ICON'] = str(ico)
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
         'Bypass', '-EncodedCommand', script],
        env=environment, capture_output=True, text=True, timeout=timeout,
        check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Windows could not create the desktop shortcut: ' +
                           (result.stderr.strip() or result.stdout.strip() or
                            f'PowerShell exited with {result.returncode}'))
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError('Shortcut creation completed without reporting its location.')
    link = Path(lines[-1])
    if link.name != SHORTCUT_NAME or not link.is_file():
        raise RuntimeError('Windows did not create the expected Desktop shortcut: ' + str(link))
    return link
