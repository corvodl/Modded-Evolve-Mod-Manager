"""Read-only native CAF -> rigged textured character GLB bridge.

The full CDF model and native controller are decoded on demand; the manager
never bundles game animation assets or rewrites a user game file.
"""
from __future__ import annotations

from pathlib import Path
from model_preview import find_preview_mesh
from model_blender_export import glb_bytes
from model_textured_export import (
    embed_diffuse_textures, textured_model_from_workspace)
from model_rigging import skin_glb_composite
from native_caf import append_caf_animations
from multi_pak_assets import _resolve


def create_character_glb(workspace, relative, index, *, character=None,
                         texture_folder=None, projects_root=None, stage_root=None,
                         clip=None, clips=None):
    """Return (GLB bytes, details), optionally containing an original CAF clip.

    The preview and rigged export use the same character assembly selection.
    For a non-CDF mesh, use its existing verified single-skin exporter.
    """
    if character is None:
        source = (Path(workspace)/'files'/relative).resolve()
        mesh = find_preview_mesh(workspace,relative,source.read_bytes(),
            projects_root=projects_root, stage_root=stage_root)
        blob, details = textured_model_from_workspace(
            mesh, workspace, mesh.source_name or relative,
            texture_folder=texture_folder, projects_root=projects_root,
            stage_root=stage_root)
        if 'Rigged:' not in details.get('rig_status',''):
            raise ValueError(details.get('rig_status','No compatible native skeleton found'))
    else:
        candidates, status = _resolve(index, character.skeleton,
                                      preferred_workspace=workspace)
        if status != 'found':
            raise ValueError('Missing or ambiguous CDF skeleton: '+character.skeleton)
        skeleton = candidates[0]['file'].read_bytes()
        parts = []
        for virtual, render, skin, count in character.rig_parts:
            if skin is None:
                raise ValueError('Missing native skin companion for '+virtual)
            parts.append((render.read_bytes(), skin.read_bytes(), count))
        blob = glb_bytes(character.mesh, name=Path(character.cdf).stem)
        blob, textures = embed_diffuse_textures(blob,character.materials)
        blob, rig = skin_glb_composite(blob,parts,skeleton)
        details = {'texture_count':textures,'rig_status':'Rigged CDF assembly',
                   'bone_count':rig['bone_count'],'part_count':rig['part_count']}
    selected_clips = (tuple(clips) if clips is not None
                      else (clip,) if clip is not None else ())
    if selected_clips:
        if len(selected_clips) > 256:
            raise ValueError('Export at most 256 animation clips per model')
        for entry in selected_clips:
            if not entry.available or entry.file is None or entry.kind != 'caf':
                raise ValueError('Native animation missing or incompatible: ' + entry.name)
        blob, anim_report = append_caf_animations(
            blob, ((entry.name,entry.file.read_bytes()) for entry in selected_clips))
        details['animations'] = anim_report['clips']
        details['bone_channels'] = anim_report['bone_channels']
    return blob, details
