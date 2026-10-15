"""Read-only model preparation suitable for a Qt background worker.

No QWidget, QOpenGLContext or other GUI state is accessed here. Results carry
geometry, materials, and native animation references to the GUI thread.
"""
from __future__ import annotations

from pathlib import Path

from model_asset import inspect_model
from model_preview import find_preview_mesh
from material_preview import _workspace_index, load_preview_materials
from character_assembly import assemble_character
from character_animation_catalog import discover_animations
from gpu_model_preview import prepare_geometry
from workspace_file_actions import checked_path


def resolve_model(workspace, relative, *, projects_root=None, stage_root=None,
                  texture_folder=None, animation_index=None, equipment=None,
                  index=None, require_assembly=False, cancel=None):
    def checkpoint():
        if cancel is not None and cancel():
            raise InterruptedError('Obsolete model preparation cancelled')

    checkpoint()
    path = checked_path(workspace, relative)
    raw = path.read_bytes()
    checkpoint()
    if str(relative).casefold().endswith('.cdf'):
        report = 'Character Definition (CDF) • referenced model assembly'
    else:
        try:
            report = inspect_model(raw).description
        except ValueError as exc:
            report = str(exc)
    if index is None:
        index = _workspace_index(workspace, projects_root, stage_root)
    checkpoint()
    character = None
    assembly_error = ''
    try:
        character = assemble_character(
            workspace, relative, index, texture_folder=texture_folder,
            projects_root=projects_root, stage_root=stage_root,
            equipment=equipment)
    except (ValueError, OSError) as exc:
        assembly_error = str(exc)
    checkpoint()
    if require_assembly and character is None:
        raise ValueError(
            'Full Character needs a valid CDF with compatible body and '
            'equipment meshes. ' + (assembly_error or 'No CDF assembly found.'))
    mesh = (character.mesh if character else
            find_preview_mesh(workspace, relative, raw,
                              projects_root=projects_root, stage_root=stage_root,
                              index=index))
    materials = character.materials if character else None
    issue = ''
    if materials is None:
        try:
            source_material_model = (
                relative if str(mesh.source_name).startswith('Character assembly:')
                else (mesh.source_name or relative))
            materials = load_preview_materials(
                workspace, source_material_model, texture_folder,
                projects_root=projects_root, stage_root=stage_root,index=index)
        except (ValueError, OSError) as exc:
            issue = str(exc)
    checkpoint()
    skeleton = character.skeleton if character else relative
    references, idle, config = discover_animations(
        skeleton, index, preferred_workspace=workspace,
        loose_index=animation_index)
    checkpoint()
    # Derived geometry caches are optional. A broken/unwritable cache must
    # never prevent the game model from opening.
    if projects_root is not None:
        from asset_geometry_cache import cached_geometry
        geometry, cached = cached_geometry(
            mesh,Path(projects_root)/'.asset-cache'/'gpu',shading='faceted')
    else:
        geometry = prepare_geometry(mesh, shading='faceted')
        cached = False
    checkpoint()
    return {
        'raw':raw, 'report':report, 'mesh':mesh, 'materials':materials,
        'character':character, 'index':index,'geometry':geometry,
        'animation':(references,idle,config),
        'materials_error':issue, 'assembly_error':assembly_error,
        'geometry_cache_hit':cached,
    }
