"""PyInstaller runtime hook for Qt GUI-subsystem startup.

A --windowed/console=False executable has no attached command prompt, and
PyInstaller sets sys.stdout/sys.stderr to None. Provide real streams before any
Qt/Python packages import so import-time diagnostics survive in the installed
manager's per-user Data/Logs folder. Worker console output is unchanged.
"""
from pathlib import Path
import os
import sys


def _log_directory():
    app_dir = Path(sys.executable).resolve().parent
    # Packaging tests run the unpacked EXE under .build/app-dist. Never
    # contaminate the validated redistributable directory with runtime logs.
    if app_dir.parent.name.casefold() == "app-dist":
        app_dir = Path(os.environ.get("LOCALAPPDATA") or
                       os.environ.get("TEMP") or ".") / "EvolveModManager"
    return app_dir / "Data" / "Logs"


if os.name == "nt" and (sys.stdout is None or sys.stderr is None):
    try:
        destination = _log_directory()
        destination.mkdir(parents=True, exist_ok=True)
        stream = open(destination / "runtime.log", "a",
                      encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    stream.write("Qt windowed startup initialized.\n")
