"""Read-only browsing and compatible exports of verified Asset Library files.

No function here creates an editable workspace, modifies imported PAK content,
or writes to the game installation. All lookups use manifest-listed virtual paths.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import shutil

from asset_library import LIBRARY_HOME, MANIFEST, _load_manifest, extracted_index
from evolve_pak_workspace import normalized_name

_ARCHIVE_ID = re.compile(r'^[0-9a-f]{20}$')
_PREVIEW_LIMIT = 4 * 1024 * 1024


def archive_folder(identifier, root=None):
    if not isinstance(identifier, str) or not _ARCHIVE_ID.fullmatch(identifier):
        raise ValueError('Choose an imported Asset Library PAK.')
    home = Path(root if root is not None else LIBRARY_HOME).resolve()
    folder = home / identifier
    if (folder.is_symlink() or not folder.is_dir()
            or not folder.resolve().is_relative_to(home)
            or not (folder / MANIFEST).is_file()):
        raise FileNotFoundError('Selected imported PAK is not available.')
    return folder


def archive_entries(identifier, root=None):
    """Safe manifest-listed files only, preserving original case and paths."""
    folder = archive_folder(identifier, root)
    manifest = _load_manifest(folder)
    files_link = folder / 'files'
    files = files_link.resolve()
    if (files_link.is_symlink() or not files.is_relative_to(folder.resolve())):
        raise ValueError('Imported PAK file root is unsafe.')
    records = {}
    for row in manifest['entries']:
        relative = normalized_name(row['path'])
        candidate = files.joinpath(*relative.split('/'))
        if (candidate.is_symlink() or not candidate.is_file()
                or not candidate.resolve().is_relative_to(files)
                or candidate.stat().st_size != row['bytes']):
            continue
        records[relative] = candidate
    return records


def read_asset(identifier, relative, *, root=None, limit=_PREVIEW_LIMIT):
    relative = normalized_name(relative)
    entries = archive_entries(identifier, root)
    path = entries.get(relative)
    if path is None:
        raise FileNotFoundError('This imported asset is missing or has changed.')
    if limit is not None and path.stat().st_size > limit:
        raise ValueError('File exceeds the read-only preview size limit.')
    return path.read_bytes()


def _outside_library(destination, root=None):
    home = Path(root if root is not None else LIBRARY_HOME).resolve()
    target = Path(destination).expanduser().resolve()
    if target.is_relative_to(home):
        raise ValueError('Exports must be outside the read-only Asset Library.')
    return target


def export_file(identifier, relative, destination, *, root=None):
    """Copy a single extracted game file unchanged, never update its source."""
    relative = normalized_name(relative)
    src = archive_entries(identifier, root).get(relative)
    if src is None:
        raise FileNotFoundError('Imported asset is missing or damaged.')
    dst = _outside_library(destination, root)
    if dst == src.resolve() or not dst.parent.is_dir():
        raise ValueError('Choose an existing external export directory.')
    shutil.copyfile(src, dst)
    return dst


def export_archive_layout(identifier, destination_dir, *, root=None):
    """Export exactly the CryEngine virtual folder layout as ordinary files.

    Creates a new named directory; existing exports are never overwritten.
    """
    folder = archive_folder(identifier, root)
    manifest = _load_manifest(folder)
    destination = _outside_library(destination_dir, root)
    if not destination.is_dir():
        raise FileNotFoundError('Choose an existing export directory.')
    raw_name = Path(str(manifest.get('name') or identifier)).stem
    stem = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_name)[:75] or 'PAK'
    output = destination / (stem + '_' + identifier[:8])
    if output.exists():
        raise FileExistsError('That archive export already exists: ' + str(output))
    staging = destination / ('.' + output.name + '.exporting')
    if staging.exists():
        raise FileExistsError('An unfinished export folder exists: ' + str(staging))
    files = archive_entries(identifier, root)
    if not files:
        raise ValueError('The selected imported archive has no intact extracted files.')
    staging.mkdir()
    try:
        for virtual, source in files.items():
            rel = normalized_name(virtual)
            target = staging.joinpath(*rel.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or not target.resolve().is_relative_to(staging.resolve()):
                raise ValueError('Unsafe destination path in archive.')
            shutil.copyfile(source, target)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output, len(files)


def texture_image(identifier, relative, *, root=None):
    """Decode whole or complete streamed DDS from its extracted companions."""
    from material_preview import _decode_texture
    relative = normalized_name(relative)
    entry = archive_entries(identifier, root).get(relative)
    if entry is None:
        raise FileNotFoundError(relative)
    if not (relative.casefold().endswith('.dds')
            or re.search(r'\.dds\.0$', relative, re.I)):
        raise ValueError('Choose a DDS or first .dds.0 streaming fragment.')
    return _decode_texture({'file': entry, 'workspace': None, 'relative': relative})


def export_png(identifier, relative, destination, *, root=None):
    target = _outside_library(destination, root)
    if target.suffix.casefold() != '.png' or not target.parent.is_dir():
        raise ValueError('Export to a PNG in an existing external directory.')
    image = texture_image(identifier, relative, root=root)
    image.save(target, format='PNG')
    return target


def library_model_index(identifier, root=None):
    """Include all imported dependencies while prioritizing the selected PAK."""
    own = archive_entries(identifier, root)
    index = {name: list(records) for name, records in extracted_index(root).items()}
    root_folder = archive_folder(identifier, root)
    for relative, path in own.items():
        key = relative.casefold()
        record = {'archive': root_folder.name, 'workspace': None,
                  'file': path, 'relative': relative, '_asset_library': identifier}
        records = index.setdefault(key, [])
        if not any(str(r.get('file')) == str(path) for r in records):
            records.insert(0, record)
    return index


def export_glb(identifier, relative, destination, *, root=None):
    """Blender-ready glTF with safely resolved library diffuse maps and rig."""
    from model_preview import read_preview_mesh
    from model_blender_export import glb_bytes
    from model_textured_export import embed_diffuse_textures
    from material_preview import load_preview_materials
    from model_rigging import skin_glb, skin_companion_path
    from multi_pak_assets import _resolve
    relative = normalized_name(relative)
    if not relative.casefold().endswith(('.skinm', '.chrm', '.cgfm', '.cgam')):
        raise ValueError('Select a supported CryEngine render mesh for GLB export.')
    target = _outside_library(destination, root)
    if target.suffix.casefold() != '.glb' or not target.parent.is_dir():
        raise ValueError('Export to a .glb file in an existing external directory.')
    files = archive_entries(identifier, root)
    if relative not in files:
        raise FileNotFoundError('The selected model is not present in this PAK.')
    mesh = read_preview_mesh(files[relative].read_bytes(), relative)
    payload = glb_bytes(mesh)
    index = library_model_index(identifier, root)
    report = {'textures': 0, 'rig': 'No compatible compiled skin found'}
    try:
        materials = load_preview_materials(
            archive_folder(identifier, root), relative, index=index)
        payload, count = embed_diffuse_textures(payload, materials)
        report['textures'] = count
    except (OSError, ValueError) as exc:
        report['texture_note'] = str(exc)[:220]
    companion = skin_companion_path(relative)
    if companion:
        hits, status = _resolve(index, companion)
        if status == 'found':
            try:
                payload, rig = skin_glb(
                    payload, files[relative].read_bytes(), Path(hits[0]['file']).read_bytes())
                report['rig'] = '%s bones' % rig['bone_count']
            except (OSError, ValueError) as exc:
                report['rig'] = 'Unsupported: ' + str(exc)[:160]
    target.write_bytes(payload)
    return target, report
