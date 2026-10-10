"""PyInstaller runtime hook: give windowed Qt processes valid text streams.

Some native dependency initialization writes to stdout or stderr before the
application module runs. Windows no-console bootloaders set both to None.
Keep diagnostics in a per-user preview log and never touch the game/Data tree.
"""
import os
import sys
from pathlib import Path
if os.name == 'nt':
    try:
        root = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('TEMP') or '.') / 'EvolveModManagerQtPreview'
        root.mkdir(parents=True, exist_ok=True)
        stream = open(root/'runtime.log', 'a', encoding='utf-8', buffering=1)
    except OSError:
        stream = open(os.devnull, 'w', encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    stream.write('Qt Windows runtime hook reached.\\n')

# Console bootloader is the verified startup path for Qt on the CI runner.
# When Explorer creates a private console for this GUI, hide only that private
# console. Never hide a shared PowerShell / cmd window opened by the user.
if os.name == 'nt' and Path(sys.executable).stem.casefold() == 'evolvemodmanager':
    try:
        import ctypes as ct
        kernel = ct.windll.kernel32
        attached = (ct.c_ulong * 8)()
        total = kernel.GetConsoleProcessList(attached, 8)
        if total == 1:
            handle = kernel.GetConsoleWindow()
            if handle:
                ct.windll.user32.ShowWindow(handle, 0)
    except (AttributeError, OSError):
        pass
