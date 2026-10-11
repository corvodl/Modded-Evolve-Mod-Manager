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
from native_caf import append_caf_animations, decode_caf
from model_textured_export import _unpack_glb
import zlib
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
        document, _ = _unpack_glb(blob)
        if len(document.get('skins', ())) != 1:
            raise ValueError('Animated export needs a single compatible skeleton')
        skin = document['skins'][0]
        compatible_bones = {
            zlib.crc32(document['nodes'][node]['name'].casefold().encode('utf-8'))
            for node in skin['joints'] if document['nodes'][node].get('name')
        }
        playable, skipped = [], []
        for entry in selected_clips:
            if not entry.available or entry.file is None or entry.kind != 'caf':
                skipped.append(entry.name + ': not extracted')
                continue
            from character_animation_catalog import native_clip_playability
            state, reason = native_clip_playability(entry)
            if state == 'layered':
                skipped.append(entry.name + ': requires CryEngine base-pose blending')
                continue
            try:
                data = entry.file.read_bytes()
                motion = decode_caf(data)
                matched = sum(t.controller_id in compatible_bones for t in motion.tracks)
                if motion.additive:
                    raise ValueError('CAF flagged additive; requires base-pose blending')
                if matched < min(3, max(1, len(motion.tracks)//4)):
                    raise ValueError('controller IDs do not match this character skeleton')
                if not any(t.rotation_times or t.position_times for t in motion.tracks):
                    raise ValueError('no keyframe tracks')
                playable.append((entry.name, data))
            except (ValueError, OSError) as exc:
                skipped.append(entry.name + ': ' + str(exc)[:110])
        if not playable:
            raise ValueError('No standalone native CAF clip could be played. '
                             'Layered aim poses require a base animation; '
                             'select a standalone idle/stand/run clip. Details: '
                             + '; '.join(skipped[:3]))
        try:
            blob, anim_report = append_caf_animations(blob, playable)
        except ValueError:
            # Rare files contain valid controllers but unsupported glTF
            # timelines. Retry independently so valid clips still export.
            assembled, channels, count = blob, 0, 0
            for name, data in playable:
                try:
                    assembled, report = append_caf_animations(
                        assembled, [(name, data)])
                    channels += report['bone_channels']
                    count += report['clips']
                except ValueError as exc:
                    skipped.append(name + ': ' + str(exc)[:110])
            if not count:
                raise ValueError('None of the native CAFs could be exported')
            blob, anim_report = assembled, {
                'clips': count, 'bone_channels': channels}
        details['animations'] = anim_report['clips']
        details['bone_channels'] = anim_report['bone_channels']
        details['skipped_animations'] = len(skipped)
        details['skipped_reasons'] = tuple(skipped[:12])
    return blob, details
