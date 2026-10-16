"""One character export workflow shared by the main window and File Editor.

Never confuse individual-mesh conversion with a complete character export.
The caller selects a character within an extracted, verified PAK workspace.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

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


def _preferred_full_assembly(assemblies, selected_cdf=None):
    """Never sacrifice verified skinning to choose an unrigged equipment mesh."""
    entries = list(assemblies.values())
    if not entries:
        return None
    selected = str(selected_cdf or '').casefold()

    def score(pair):
        _, info = pair
        bones = int(info.get('bone_count') or 0)
        return (
            bones > 0,
            bool(info.get('preferred_full_character')),
            bool(selected and str(info.get('cdf', '')).casefold() == selected),
            int(info.get('embedded_animations') or 0),
            int(info.get('part_count') or 0),
        )

    return max(entries, key=score)


def export_character_directory(source_folder, destination, *, projects_root=None,
                               stage_root=None, texture_folder=None, index=None,
                               selected_cdf=None, progress=None, cancelled=None):
    """Export <Character>/<Character>.glb + animations/, textures/, models/.

    The top-level GLB is the best VERIFIED assembly. The exporter does not
    invent a rig or retarget incompatible animations; failures are reported.
    """
    workspace, virtual = character_export_source(source_folder)
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
    chosen = _preferred_full_assembly(assemblies, selected_cdf)
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
        output_name=_character_name(virtual),
        progress=progress, cancelled=cancelled)
