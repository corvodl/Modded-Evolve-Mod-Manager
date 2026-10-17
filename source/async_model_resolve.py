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
from performance_trace import span, record



def refresh_character_material_index(index, relative, stage_root=None):
    """Supplement an editor's possibly stale index with current verified assets.

    The Qt explorer builds its preview index when the archive is opened; later
    Asset Library imports and asynchronously warmed character/shared PAK
    catalogs otherwise remain invisible to the final material decode even
    though the texture diagnostic can see them. Include only exact virtual
    paths from the selected actor, its category-shared assets and the game's
    explicit global textures/ tree; never guess by matching another actor's
    texture basename. The renderer and assembly share this same snapshot.
    """
    from asset_library import extracted_index
    from evolve_pak_workspace import normalized_name
    parts = normalized_name(relative).casefold().split('/')
    if len(parts) < 4 or parts[:2] not in (
            ['characters','hunters'], ['characters','monsters'],
            ['characters','creatures']):
        return index
    actor = '/'.join(parts[:3]) + '/'
    category_shared = '/'.join(parts[:2]) + '/shared/'
    from shared_pak_cache import shared_asset_index
    supplementary = (shared_asset_index(stage_root) if stage_root is not None
                     else extracted_index())
    if not supplementary:
        return index
    merged = {key:list(values) for key, values in index.items()}
    for key, records in supplementary.items():
        virtual = key.casefold()
        if not virtual.startswith((actor, category_shared, 'textures/')):
            continue
        existing = merged.setdefault(key, [])
        known = {str(item.get('file')) for item in existing}
        for item in records:
            filename = str(item.get('file'))
            if filename not in known:
                existing.append(item)
                known.add(filename)
    return merged

def resolve_model(workspace, relative, *, projects_root=None, stage_root=None,
                  texture_folder=None, animation_index=None, equipment=None,
                  index=None, require_assembly=False, cancel=None,
                  geometry_ready=None, stage_ready=None):
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
    record('model', 'reference_index_started')
    with span('model', 'reference_index'):
        if index is None:
            index = _workspace_index(workspace, projects_root, stage_root)
        # Refresh index in the background, not from a QWidget.
        index = refresh_character_material_index(index, relative, stage_root)
    checkpoint()
    downstream_cancel = {'cancel': cancel} if cancel is not None else {}
    character = None
    assembly_error = ''
    preliminary = {}
    def publish_geometry(mesh):
        checkpoint()
        record('model', 'geometry_prepare_started', vertices=len(mesh.vertices),
               triangles=len(mesh.triangles))
        with span('model', 'geometry_prepare'):
            if projects_root is not None:
                from asset_geometry_cache import cached_geometry
                geometry, cached = cached_geometry(
                    mesh, Path(projects_root)/'.asset-cache'/'gpu', shading='smooth')
            else:
                geometry, cached = prepare_geometry(mesh, shading='smooth'), False
        checkpoint()
        preliminary.update(mesh=mesh, geometry=geometry, cached=cached)
        if geometry_ready is not None:
            geometry_ready({'mesh': mesh, 'geometry': geometry,
                            'character': None, 'index': index, 'report': report})
    record('model', 'character_assembly_started')
    try:
        with span('model', 'character_assembly'):
            character = assemble_character(
                workspace, relative, index, texture_folder=texture_folder,
                projects_root=projects_root, stage_root=stage_root,
                equipment=equipment, pre_material_geometry=(
                    publish_geometry if geometry_ready is not None else None),
                stage_ready=stage_ready, **downstream_cancel)
    except (ValueError, OSError) as exc:
        assembly_error = str(exc)
    checkpoint()
    if require_assembly and character is None:
        # Emet's original remote-cannon 3P mesh already includes his arm;
        # the healing-totem and dropship CDFs are separate equipment models.
        # Use the exact .skinm/.skin pair, never invent a replacement CDF.
        from character_full_models import is_emet_primary_model
        from multi_pak_assets import _resolve
        native_skin_verified = False
        if is_emet_primary_model(relative):
            hits, state = _resolve(
                index, relative[:-1], preferred_workspace=workspace)
            native_skin_verified = state == 'found'
        if not native_skin_verified:
            raise ValueError(
                'Full Character needs a valid CDF with compatible body and '
                'equipment meshes. ' + (assembly_error or 'No CDF assembly found.'))
        report = 'Emet • Original remote-cannon 3P complete mesh (with arm)'

    record('model', 'mesh_decode_started')
    with span('model', 'mesh_decode'):
        mesh = (character.mesh if character else
                find_preview_mesh(workspace, relative, raw,
                                  projects_root=projects_root, stage_root=stage_root,
                                  index=index))
    checkpoint()
    if preliminary:
        # Reuse early geometry instead of rebuilding the same GPU buffers
        # after all CDF materials have finished decoding.
        geometry, cached = preliminary['geometry'], preliminary['cached']
    else:
        publish_geometry(mesh)
        geometry, cached = preliminary['geometry'], preliminary['cached']
    checkpoint()
    if stage_ready is not None:
        stage_ready('materials')
    materials = character.materials if character else None
    issue = ''
    if materials is None:
        try:
            source_material_model = (
                relative if str(mesh.source_name).startswith('Character assembly:')
                else (mesh.source_name or relative))
            with span('model', 'materials_resolve'):
                materials = load_preview_materials(
                    workspace, source_material_model, texture_folder,
                    projects_root=projects_root, stage_root=stage_root,
                    index=index, **downstream_cancel)
        except (ValueError, OSError) as exc:
            issue = str(exc)
    checkpoint()
    if stage_ready is not None:
        stage_ready('animations')
    skeleton = character.skeleton if character else relative
    with span('model', 'animation_discovery'):
        references, idle, config = discover_animations(
            skeleton, index, preferred_workspace=workspace,
            loose_index=animation_index, **downstream_cancel)
    checkpoint()
    checkpoint()
    return {
        'raw':raw, 'report':report, 'mesh':mesh, 'materials':materials,
        'character':character, 'index':index,'geometry':geometry,
        'animation':(references,idle,config),
        'materials_error':issue, 'assembly_error':assembly_error,
        'geometry_cache_hit':cached,
    }
