"""Bridge to the separately executed, bundled GPLv2 Cryengine Converter.

The independent executable and corresponding source are packaged at build time.
No converter code is incorporated into the manager runtime.
The converter reads real CAF/DBA tracks referenced by the chosen native
character; this manager plays only validated animated GLB output.
"""
from __future__ import annotations
from pathlib import Path


def bundled_converter(root=None):
    """Return the pinned independent converter shipped with Windows builds.

    No arbitrary downloaded executables or PATH lookup; the file must be in
    the installation's own packaged third_party folder.
    """
    bundle = Path(root if root is not None else Path(__file__).resolve().parent)
    executable = bundle / 'third_party' / 'Cryengine-Converter' / 'cgf-converter.exe'
    if not executable.is_file() or executable.is_symlink():
        raise FileNotFoundError(
            'The bundled Cryengine-Converter is missing from this installation. '
            'Reinstall the complete Windows beta package.')
    return executable.resolve()


def extracted_asset_root(source):
    """Derive the original, virtual-path extraction root (no folder prompt)."""
    path = Path(source).resolve(strict=True)
    for ancestor in path.parents:
        if ancestor.name.casefold() != 'files':
            continue
        manifest = ancestor.parent / 'asset-library.json'
        workspace = ancestor.parent / '.evolve-pak-workspace.json'
        if manifest.is_file() or workspace.is_file():
            return ancestor.resolve()
    return None


def preview_output_folder(base=None):
    from tempfile import mkdtemp
    root = Path(base if base is not None else (
        Path.home() / 'AppData' / 'Local' / 'EvolveModManager' /
        'Data' / 'AnimationPreviewCache'))
    root.mkdir(parents=True, exist_ok=True)
    return Path(mkdtemp(prefix='converted-', dir=root))


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
