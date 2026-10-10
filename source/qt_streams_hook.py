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
