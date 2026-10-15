"""Flat, atomic, read-only export of a complete character's original assets.

Outputs just <chosen directory>/<character folder>/models, textures, etc. No
characters/hunters/merc_* chain is added. References from companion DATA/TS
PAKs are resolved against exact virtual paths and recorded in a manifest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil

from evolve_pak_workspace import normalized_name
from multi_pak_assets import _resolve

MAX_CHARACTER_FILES = 16000
MAX_CHARACTER_BYTES = 12 * 1024 * 1024 * 1024


def character_asset_entries(workspace, character_folder, *, index=None):
    """Return verified (source file, original virtual name) for one character."""
    workspace = Path(workspace).resolve()
    files_root = (workspace / 'files').resolve()
    virtual = normalized_name(str(character_folder).replace('\\', '/'))
    prefix = virtual.casefold() + '/'
    source_folder = files_root.joinpath(*virtual.split('/'))
    if (not source_folder.is_dir() or source_folder.is_symlink() or
            not source_folder.resolve().is_relative_to(files_root)):
        raise ValueError('Select a real character folder in this PAK.')
    records = {}
    # Always include the selected archive's own files, even if the shared
    # index is stale. Never crawl outside the selected virtual subtree.
    for path in source_folder.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        if not path.resolve().is_relative_to(files_root):
            continue
        rel = path.relative_to(files_root).as_posix()
        records[rel.casefold()] = (path, rel, 'selected PAK')
        if len(records) > MAX_CHARACTER_FILES:
            raise ValueError('Selected character folder exceeds the file limit.')
    missing = []
    if index:
        # Include exactly matching character virtual paths from imported
        # sibling TS/DATA PAKs (never other characters with similar basenames).
        for key in sorted(index):
            if not key.casefold().startswith(prefix) or key.casefold() in records:
                continue
            hits, status = _resolve(index, key, preferred_workspace=workspace)
            if status != 'found':
                missing.append((key, 'ambiguous or missing companion asset'))
                continue
            record = hits[0]
            source = Path(record['file'])
            if not source.is_file() or source.is_symlink():
                missing.append((key, 'unreadable imported companion asset'))
                continue
            origin_root = (Path(record.get('workspace')) / 'files').resolve() if record.get('workspace') else None
            if origin_root and not source.resolve().is_relative_to(origin_root):
                missing.append((key, 'unsafe imported companion path'))
                continue
            virtual_name = normalized_name(record.get('relative') or key)
            if not virtual_name.casefold().startswith(prefix):
                continue
            records[key.casefold()] = (
                source, virtual_name, str(record.get('archive') or 'companion PAK'))
            if len(records) > MAX_CHARACTER_FILES:
                raise ValueError('Character archive contains too many files.')
    return virtual, tuple(records.values()), tuple(missing)


def assembled_character_glb(character, workspace, relative, index, *,
                             texture_folder=None, projects_root=None,
                             stage_root=None):
    """Export one assembled mesh; fall back to explicitly unrigged GLB.

    The fallback is still a full COMPOSITE mesh, not a single body skin. It
    must never pretend that native weights or skeletons were reconstructed.
    """
    if character is None:
        raise ValueError('No CDF-based character assembly was verified.')
    try:
        from native_character_glb import create_character_glb
        payload, info = create_character_glb(
            workspace, relative, index, character=character,
            texture_folder=texture_folder,
            projects_root=projects_root, stage_root=stage_root)
        info = dict(info)
        info['parts'] = tuple(character.included)
        return payload, info
    except (ValueError, OSError) as error:
        from model_blender_export import glb_bytes
        from model_textured_export import embed_diffuse_textures
        payload = glb_bytes(character.mesh, name=Path(character.cdf).stem + '_Full')
        count = 0
        if character.materials is not None:
            payload, count = embed_diffuse_textures(payload, character.materials)
        return payload, {
            'texture_count': count, 'bone_count': 0,
            'part_count': len(character.included),
            'parts': tuple(character.included),
            'rig_status': 'Unrigged assembled mesh: ' + str(error)[:180],
        }


def build_cdf_folder_glbs(workspace, character_folder, index, *,
                          texture_folder=None, projects_root=None,
                          stage_root=None, selected_cdf=None,
                          progress=None, cancelled=None, max_assemblies=48):
    """Prepare every independently verified CDF as a Blender GLB.

    The chosen folder, not similar character names elsewhere in the library,
    constrains eligible CDFs. Failed or ambiguous definitions are reported,
    never silently replaced with a different character's body.
    """
    from character_assembly import assemble_character
    virtual = normalized_name(str(character_folder).replace('\\', '/'))
    prefix = virtual.casefold() + '/'
    candidates = sorted({
        name.casefold(): name for name in index
        if name.casefold().startswith(prefix) and name.casefold().endswith('.cdf')
    }.values(), key=str.casefold)
    if len(candidates) > max_assemblies:
        raise ValueError('Too many character definitions in folder; choose a narrower folder.')
    results, skipped = {}, []
    max_total = 512 * 1024 * 1024
    total = 0
    for number, name in enumerate(candidates, 1):
        if cancelled and cancelled():
            raise InterruptedError('CDF assembly export cancelled.')
        if progress:
            progress('Assembling CDF ' + PurePosixPath(name).name, number - 1, len(candidates))
        # Keep the hierarchy under Assemblies/ when definitions live in a
        # subfolder so same-named CDFs cannot overwrite each other.
        relative = PurePosixPath(name[len(virtual) + 1:]).with_suffix('.glb')
        output = PurePosixPath('Assemblies') / relative
        if any(part in ('', '.', '..') for part in output.parts):
            raise ValueError('Invalid CDF output path.')
        try:
            from multi_pak_assets import _resolve
            hits, state = _resolve(index, name, preferred_workspace=workspace)
            if state != 'found':
                raise ValueError('CDF is missing or ambiguous: ' + state)
            character = assemble_character(
                workspace, name, index, texture_folder=texture_folder,
                projects_root=projects_root, stage_root=stage_root)
            if character is None or character.cdf.casefold() != name.casefold():
                raise ValueError('No verified assembly belonging to this CDF')
            blob, info = assembled_character_glb(
                character, workspace, name, index,
                texture_folder=texture_folder, projects_root=projects_root,
                stage_root=stage_root)
            if not blob.startswith(b'glTF'):
                raise ValueError('CDF assembly did not produce Blender-compatible GLB.')
            if total + len(blob) > max_total:
                raise ValueError('Assembled GLB output exceeds 512 MiB safety budget.')
            total += len(blob)
            results[output.as_posix()] = (blob, dict(info, cdf=name))
        except (ValueError, OSError) as exc:
            skipped.append((name, str(exc)[:220]))
        if progress:
            progress('Prepared CDF assemblies', number, len(candidates))
    return results, tuple(skipped)


def export_character_asset_folder(workspace, character_folder, destination,
                                  *, index=None, full_model=None,
                                  assemblies=None, assembly_errors=(),
                                  progress=None, cancelled=None):
    """Export exact model-folder contents with no redundant virtual ancestors.

    A new <destination>/<character-folder> directory is created atomically.
    Never overwrite an existing export, mutate source game assets, or silently
    resolve virtual-path conflicts to arbitrarily chosen texture bytes.

    full_model: optional (GLB bytes, report) for Full_Character.glb.
    assemblies: mapping of Assemblies/<cdf relative>.glb to (bytes, report).
    Original CDFs and all raw assets stay alongside the Blender GLBs.
    """
    home = Path(workspace).resolve()
    destination = Path(destination).resolve()
    if not destination.is_dir():
        raise FileNotFoundError('Choose an existing export destination.')
    source_root = (home / 'files').resolve()
    if destination.is_relative_to(source_root) or destination.is_relative_to(home):
        raise ValueError('Export destination must be outside extracted assets.')
    virtual, records, conflicts = character_asset_entries(
        home, character_folder, index=index)
    if not records:
        raise ValueError('No character assets found to export.')
    short_name = PurePosixPath(virtual).name
    target = destination / short_name
    if target.exists():
        raise FileExistsError('Export folder already exists: ' + str(target))
    staging = destination / ('.' + short_name + '.exporting')
    if staging.exists():
        raise FileExistsError('An unfinished character export already exists.')
    limit = sum(int(source.stat().st_size) for source, _, _ in records)
    assemblies = dict(assemblies or {})
    if len(assemblies) > 48:
        raise ValueError('Too many CDF assembly exports.')
    if full_model is not None:
        limit += len(full_model[0])
    for output, pair in assemblies.items():
        location = PurePosixPath(output)
        if (location.parts[0] != 'Assemblies' or location.suffix.casefold() != '.glb'
                or any(part in ('', '.', '..') for part in location.parts)
                or not isinstance(pair[0], bytes) or not pair[0].startswith(b'glTF')):
            raise ValueError('Invalid CDF assembly output.')
        limit += len(pair[0])
    if limit > MAX_CHARACTER_BYTES:
        raise ValueError('Character assets exceed the 12 GiB export limit.')
    staging.mkdir()
    manifest_rows = []
    try:
        prefix = virtual.casefold() + '/'
        count = len(records) + int(full_model is not None) + len(assemblies)
        for i, (source, game_path, archive) in enumerate(records, 1):
            if cancelled and cancelled():
                raise InterruptedError('Character export cancelled.')
            if not game_path.casefold().startswith(prefix):
                raise ValueError('Companion asset lies outside character virtual path.')
            local = game_path[len(virtual) + 1:]
            local_parts = PurePosixPath(local).parts
            if not local_parts or any(p in ('', '.', '..') for p in local_parts):
                raise ValueError('Unsafe character asset filename.')
            output = staging.joinpath(*local_parts)
            if not output.resolve().is_relative_to(staging.resolve()):
                raise ValueError('Unsafe output asset path.')
            output.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as inp, output.open('xb') as dst:
                shutil.copyfileobj(inp, dst, 1024 * 1024)
            manifest_rows.append({'file': local, 'virtual': game_path, 'source': archive})
            if progress:
                progress('Copying character assets', i, count)
        info = None
        if full_model is not None:
            if cancelled and cancelled():
                raise InterruptedError('Character export cancelled.')
            payload, info = full_model
            (staging / 'Full_Character.glb').write_bytes(payload)
            from blender_texture_folder import write_texture_folder
            write_texture_folder(payload, staging / 'Full_Character.glb')
            if progress:
                progress('Writing full character model', count, count)
        assembly_rows = []
        for position, (location, pair) in enumerate(sorted(assemblies.items()), 1):
            if cancelled and cancelled():
                raise InterruptedError('Character export cancelled.')
            payload, report = pair
            out = staging.joinpath(*PurePosixPath(location).parts)
            if not out.resolve().is_relative_to(staging.resolve()):
                raise ValueError('CDF assembly output escapes destination.')
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open('xb') as target_file:
                target_file.write(payload)
            from blender_texture_folder import write_texture_folder
            write_texture_folder(payload, out)
            assembly_rows.append({'glb': location, **report})
            if progress:
                progress('Writing CDF Blender assemblies',
                         len(records) + int(full_model is not None) + position, count)
        # Provenance preserves full virtual game paths without adding redundant
        # directories to the user-visible output.
        (staging / 'asset_sources.json').write_text(
            json.dumps({'format': 'evolve-character-asset-export-v1',
                        'character_folder': virtual, 'files': manifest_rows,
                        'unresolved_companions': list(conflicts),
                        'full_model': info,
                        'cdf_assemblies': assembly_rows,
                        'cdf_assembly_errors': list(assembly_errors)}, indent=2, default=str),
            encoding='utf-8')
        os.replace(staging, target)
        return {
            'folder': str(target), 'files': len(manifest_rows),
            'unresolved': tuple(conflicts), 'full_model': info,
            'cdf_assemblies': tuple(assembly_rows),
            'cdf_assembly_errors': tuple(assembly_errors),
        }
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
