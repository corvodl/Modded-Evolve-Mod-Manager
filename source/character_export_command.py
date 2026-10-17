"""One character export workflow shared by the main window and File Editor.

Never confuse individual-mesh conversion with a complete character export.
The caller selects a character within an extracted, verified PAK workspace.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from character_aliases import character_name_for_internal
from character_asset_export import (
    build_cdf_folder_glbs, export_character_asset_folder,
)


def character_export_source(folder):
    """Resolve the selected directory to its extracted workspace and character."""
    original = Path(folder).expanduser()
    if original.is_symlink() or not original.is_dir():
        raise ValueError('Select a real extracted character folder, not a link.')
    source = original.resolve()
    for parent in (source, *source.parents):
        if parent.name.casefold() != 'files':
            continue
        relative = source.relative_to(parent)
        parts = relative.parts
        if not parts:
            break
        for i in range(len(parts) - 2):
            if (parts[i].casefold() == 'characters'
                    and parts[i + 1].casefold() in ('hunters', 'monsters')):
                parts = parts[:i + 3]
                break
        virtual = PurePosixPath(*parts).as_posix()
        from evolve_pak_workspace import normalized_name
        virtual = normalized_name(virtual)
        child = parent
        for component in virtual.split('/'):
            child = child / component
            if child.is_symlink():
                raise ValueError('Linked character source folders are not supported.')
        if not child.is_dir() or not child.resolve().is_relative_to(parent.resolve()):
            raise ValueError('Selected character folder is unavailable.')
        workspace = parent.parent
        if not ((workspace / '.evolve-pak-workspace.json').is_file()
                or (workspace / 'asset-library.json').is_file()):
            raise ValueError('Choose a character within an extracted PAK workspace or Asset Library.')
        return workspace, virtual
    raise ValueError(
        'Choose the character folder under an extracted PAK files/ directory. '
        'Use Export Individual Models for unrelated loose model folders.')


def _character_name(virtual):
    internal = PurePosixPath(virtual).name
    alias = character_name_for_internal(internal)
    if alias:
        return alias
    if internal.casefold().startswith(('merc_', 'mon_')):
        internal = internal.split('_', 1)[1]
    return internal.replace('_', ' ').title()


def validate_character_export_destination(source_folder, destination):
    """Prevent accidentally nesting exports in legacy flat model folders.

    Keep all old GLBs untouched; ask for their parent so a fresh named
    character directory can be created as a sibling.
    """
    _workspace, virtual = character_export_source(source_folder)
    output = Path(destination).expanduser()
    if not output.is_dir() or output.is_symlink():
        raise ValueError('Choose an existing non-linked parent export directory.')
    internal = PurePosixPath(virtual).name
    friendly = _character_name(virtual)
    if output.name.casefold() == friendly.casefold():
        raise ValueError(
            'You selected an existing ' + friendly + ' folder. Select its '
            'PARENT instead: ' + str(output.parent) + '. The manager creates '
            + friendly + '/' + friendly + '.glb automatically; it will not '
            'create a nested ' + friendly + '/' + friendly + ' folder.')
    if output.name.casefold() == internal.casefold() and (
            (output / 'Assemblies').exists()
            or (output / 'CDF_assembly_report.json').exists()
            or any(output.glob('*.glb'))):
        raise ValueError(
            'This is the old flat export folder. Select its PARENT instead: '
            + str(output.parent) + '. The new export will be '
            + str(output.parent / friendly) + ' with ' + friendly
            + '.glb, animations/, textures/, and models/. Existing files are preserved.')
    if (output / friendly).exists():
        raise FileExistsError(
            'Character export already exists at ' + str(output / friendly)
            + '. Choose a new empty parent folder; existing exports are never overwritten.')
    return friendly


_FIRST_PERSON = re.compile(
    r'(?<![a-z0-9])(?:fp|1p|first[_-]?person|1st[_-]?person|view[_-]?model)(?![a-z0-9])',
    re.IGNORECASE)
_THIRD_PERSON = re.compile(
    r'(?<![a-z0-9])(?:3p|third[_-]?person)(?![a-z0-9])',
    re.IGNORECASE)


def _assembly_perspective(info):
    """Identify view-only CDFs from their own paths and verified skeletons.

    Inspect only the asset's relative path within the character folder, not
    the character name. If CDF/skeleton tags conflict, do not promote it as
    the full 3P body. Unmarked CDFs remain eligible as neutral candidates.
    """
    if info.get('original_3p_model'):
        return '3p'
    kinds = set()
    for source in (info.get('cdf'), info.get('skeleton')):
        if not source:
            continue
        parts = PurePosixPath(str(source).replace('\\', '/')).parts
        if (len(parts) >= 4 and parts[0].casefold() == 'characters'
                and parts[1].casefold() in ('hunters', 'monsters', 'creatures')):
            parts = parts[3:]
        for segment in parts:
            stem = PurePosixPath(segment).stem
            if _FIRST_PERSON.search(stem):
                kinds.add('fp')
            elif _THIRD_PERSON.search(stem):
                kinds.add('3p')
    if 'fp' in kinds:
        return 'fp'
    return '3p' if '3p' in kinds else ''


def _preferred_full_assembly(assemblies, selected_cdf=None):
    """Prefer a complete verified body; never promote a rigged accessory as main."""
    # The primary root GLB represents the complete player-visible body.
    # FP/view-model CDFs remain available under models/ as variants but must
    # never win the root export just because they have more parts or bones.
    entries = [pair for pair in assemblies.values()
               if _assembly_perspective(pair[1]) != 'fp']
    if not entries:
        return None
    selected = str(selected_cdf or '').casefold()

    def score(pair):
        _, info = pair
        bones = int(info.get('bone_count') or 0)
        # A designated canonical complete body (Emet's original 3P arm)
        # must not be replaced with a rigged but incomplete equipment CDF.
        # When no canonical source exists, favor verified multi-part bodies
        # and rigging over stand-alone accessory meshes.
        parts = int(info.get('part_count') or 0)
        return (
            bool(info.get('preferred_full_character')),
            parts > 1,
            _assembly_perspective(info) == '3p',
            bones > 0,
            parts,
            bool(selected and str(info.get('cdf', '')).casefold() == selected),
            int(info.get('embedded_animations') or 0),
        )

    return max(entries, key=score)


def export_character_directory(source_folder, destination, *, projects_root=None,
                               stage_root=None, texture_folder=None, index=None,
                               selected_cdf=None, progress=None, cancelled=None):
    """Export three root monster Stage GLBs, or one hunter/wildlife root GLB.

    Monsters require three distinct verified CDFs; each root GLB retains only
    animations validated against that exact stage rig. Other classes retain
    the original single-primary model layout.
    """
    workspace, virtual = character_export_source(source_folder)
    display_name = validate_character_export_destination(source_folder, destination)
    if index is None:
        from material_preview import _workspace_index
        index = _workspace_index(workspace, projects_root, stage_root)
    if cancelled and cancelled():
        raise InterruptedError('Character export cancelled.')
    assemblies, errors = build_cdf_folder_glbs(
        workspace, virtual, index,
        texture_folder=texture_folder, projects_root=projects_root,
        stage_root=stage_root, selected_cdf=selected_cdf,
        progress=progress, cancelled=cancelled)
    from monster_stage_export import is_monster_character, select_monster_stage_models
    if is_monster_character(virtual):
        # Unlike hunters and wildlife, monsters have three independent
        # evolution rigs. Each assembly has already had its exact CDF's
        # playable CAF/DBA clips embedded by build_cdf_folder_glbs.
        try:
            stages, selected_paths = select_monster_stage_models(assemblies, virtual)
        except ValueError as exc:
            # Report failures from the real per-CDF assembly builder. Missing
            # "verified stages" does not mean missing .cdf files: an indexed
            # CDF may be present but its .chr, .skin or shared assets may fail
            # validation. Never tell users to import PAKs without evidence.
            if 'missing stage(s)' not in str(exc) or not errors:
                raise
            details = '; '.join(
                '%s: %s' % (PurePosixPath(str(name)).name, str(reason)[:160])
                for name, reason in errors[:3])
            raise ValueError(
                str(exc) + ' Unverified CDF details: ' + details) from exc
        other_assemblies = {
            name: pair for name, pair in assemblies.items()
            if name not in selected_paths}
        return export_character_asset_folder(
            workspace, virtual, destination, index=index,
            stage_models=stages, full_model=None, assemblies=other_assemblies,
            assembly_errors=errors, output_name=display_name,
            progress=progress, cancelled=cancelled)
    chosen = _preferred_full_assembly(assemblies, selected_cdf)
    if chosen is None and assemblies:
        raise ValueError(
            'No verified third-person character body was found. '
            'Only first-person/view-model assemblies were available; '
            'check the character 3P CDF and its skeleton dependencies.')
    if chosen is None:
        summary = '; '.join(str(reason) for _, reason in errors[:3])
        raise ValueError(
            'No verified character assembly could be exported. '
            + (summary or 'Check the character CDF and its shared PAK dependencies.'))
    # Do not emit the primary GLB again under models/. That directory is
    # reserved for other CDF variants, attachments and native source models.
    other_assemblies = {name: pair for name, pair in assemblies.items()
                        if pair is not chosen}
    return export_character_asset_folder(
        workspace, virtual, destination, index=index,
        full_model=chosen, assemblies=other_assemblies, assembly_errors=errors,
        output_name=display_name,
        progress=progress, cancelled=cancelled)
