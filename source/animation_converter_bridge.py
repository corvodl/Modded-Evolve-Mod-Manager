"""Explicit, optional bridge to the independently installed Cryengine Converter.

This does NOT contain, download, bundle, or relicense its GPLv2 code.
The user chooses a trusted local executable and source asset directory.
The converter reads real CAF/DBA tracks referenced by the chosen native
character; this manager plays only validated animated GLB output.
"""
from __future__ import annotations
from pathlib import Path


def validate_conversion(executable, source, asset_root, destination):
    exe = Path(executable).resolve()
    file = Path(source).resolve()
    root = Path(asset_root).resolve()
    out = Path(destination).resolve()
    if (exe.suffix.lower() != '.exe' or not exe.is_file()
            or not out.is_dir() or not root.is_dir()):
        raise ValueError('Select an installed converter EXE and existing asset/output folders.')
    if file.suffix.lower() not in ('.chr','.skin','.caf','.dba') or not file.is_file():
        raise ValueError('Choose a valid original CryEngine CHR, SKIN, CAF or DBA file.')
    if not file.is_relative_to(root):
        raise ValueError('The selected source must be inside the unpacked asset root.')
    if out==root or out.is_relative_to(root):
        raise ValueError('Choose a separate export folder outside the original assets.')
    if exe == file or exe.is_relative_to(root):
        raise ValueError('Converter must be installed outside the original assets.')
    # No shell is used. Program runs only after the user accepts the Qt
    # confirmation and only on the files and folders explicitly selected.
    args = [str(file), '-anim', '-glb',
            '-objectdir', str(root), '-out', str(out), '-maxthreads', '2']
    return str(exe), args
