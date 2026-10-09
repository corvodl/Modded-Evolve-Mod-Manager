"""Separate bundled resources, writable settings and child execution."""
from pathlib import Path
import os
import sys

BUNDLE = Path(__file__).resolve().parent
APP_HOME = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
            else BUNDLE.parent if BUNDLE.name == 'source' else BUNDLE)
DATA_HOME = APP_HOME / 'Data'
SETTINGS_FILE = DATA_HOME / 'Settings' / 'manager_settings.json'

def settings_source():
    # Read old settings without moving projects or invalidating journal paths.
    if SETTINGS_FILE.is_file(): return SETTINGS_FILE
    return APP_HOME / 'manager_settings.json'

def save_settings(data):
    import json
    import tempfile
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='settings-', suffix='.tmp', dir=SETTINGS_FILE.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, SETTINGS_FILE)
    finally:
        Path(name).unlink(missing_ok=True)

def worker_command(command):
    if getattr(sys, 'frozen', False) and command[0] == sys.executable:
        worker = APP_HOME / 'EvolveModWorker.exe'
        if not worker.is_file():
            raise FileNotFoundError('Keep EvolveModWorker.exe beside EvolveModManager.exe.')
        args = command[1:]
        if args and args[0] == '-u':
            args = args[1:]
        return [str(worker), *args]
    return command
