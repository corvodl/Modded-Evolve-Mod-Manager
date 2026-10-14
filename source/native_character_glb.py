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
from model_rigging import skin_glb_composite, skin_glb_composite_separate
from native_caf import append_caf_animations, decode_caf
from model_textured_export import _unpack_glb
import zlib
from multi_pak_assets import _resolve


def create_character_glb(workspace, relative, index, *, character=None,
                         texture_folder=None, projects_root=None, stage_root=None,
                         clip=None, clips=None, allow_layered=False, materials=None):
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
            stage_root=stage_root, index=index, materials=materials)
        if 'Rigged:' not in details.get('rig_status',''):
            raise ValueError(details.get('rig_status','No compatible native skeleton found'))
    else:
        candidates, status = _resolve(index, character.skeleton,
                                      preferred_workspace=workspace)
        if status != 'found':
            raise ValueError('Missing or ambiguous CDF skeleton: '+character.skeleton)
        skeleton = candidates[0]['file'].read_bytes()
        parts = []
        historical = False
        for virtual, render, skin, count in character.rig_parts:
            if skin is None:
                if virtual.casefold().endswith('.chr'):
                    from model_asset import inspect_model
                    info = inspect_model(render.read_bytes())
                    if info.format_name == 'CryTek':
                        historical = True
                        parts.append((render.read_bytes(), None, count))
                        continue
                raise ValueError('Missing native skin companion for ' + virtual)
            parts.append((render.read_bytes(), skin.read_bytes(), count))
        if historical:
            from legacy_crytek_rig import read_legacy_rig
            try:
                if len(parts) != 1 or parts[0][1] is not None:
                    raise ValueError('Single body CDF binding required.')
                read_legacy_rig(parts[0][0], skeleton, parts[0][2])
            except ValueError as exc:
                raise ValueError('Legacy bone/weight decoding validation failed: ' + str(exc)) from exc
        blob = glb_bytes(character.mesh, name=Path(character.cdf).stem)
        blob, textures = embed_diffuse_textures(blob, character.materials)
        if historical:
            if len(parts) != 1 or parts[0][1] is not None:
                raise ValueError(
                    'Mixed/multipart 2012 CDF rigs are not validated yet; '
                    'select the single matching .chr body.')
            from legacy_crytek_rig import skin_glb_legacy
            blob, rig = skin_glb_legacy(blob, parts[0][0], skeleton)
            layout = 'historical-validated'
        else:
            try:
                blob, rig = skin_glb_composite(blob, parts, skeleton)
                layout = 'shared'
            except ValueError as exc:
                if not any(hint in str(exc).casefold() for hint in (
                        'conflicting bind poses', 'hierarchy differs',
                        'bind/rest pose')):
                    raise
                # Multiple distinct native rest poses require distinct armatures.
                blob, rig = skin_glb_composite_separate(
                    blob, parts, part_names=character.included)
                layout = 'independent'
        details = {'texture_count': textures,
                   'rig_status': ('Rigged historical CDF (rest pose validated)'
                                  if historical else
                                  'Rigged CDF assembly' if layout == 'shared'
                                  else 'Rigged CDF assembly: independent Blender armatures'),
                   'rig_layout': layout,
                   'bone_count': rig['bone_count'], 'part_count': len(parts)}
        if layout == 'independent' and (clip is not None or clips):
            raise ValueError('This CDF has multiple distinct native bind poses; '
                             'its Blender armatures are poseable independently '
                             'but shared CAF/DBA playback has not been verified.')
    if character is not None and 'historical' in locals() and historical and (clip is not None or clips):
        selected = clip if clip is not None else tuple(clips)[0]
        if selected.kind == 'dba' or (not selected.available and selected.dba_file is not None):
            from database_animation import convert_historical_database_motion
            blob = convert_historical_database_motion(blob, selected, index, character, preferred_workspace=workspace)
            motion_doc, _ = _unpack_glb(blob)
            details['animation_source'] = 'Pinned converter output verified against historical CDF rig'
            details['bone_channels'] = sum(len(a['channels']) for a in motion_doc['animations'])
            details['clips'] = len(motion_doc['animations'])
            return blob, details
        raise ValueError('Standalone CryTek 0x745 CAF controller decoding is not implemented; only verified converter-backed DBA clips can play.')
    if clip is not None and (clip.kind == 'dba' or
                             (not clip.available and clip.dba_file is not None)):
        from database_animation import convert_database_motion
        blob = convert_database_motion(blob, clip, index, character,
                                       preferred_workspace=workspace)
        details['animation_source'] = 'Verified DBA converted with selected character rig'
        motion_doc, _ = _unpack_glb(blob)
        details['bone_channels'] = sum(len(a['channels']) for a in motion_doc['animations'])
        details['clips'] = len(motion_doc['animations'])
        return blob, details
    selected_clips = (tuple(clips) if clips is not None
                      else (clip,) if clip is not None else ())
    if selected_clips:
        if allow_layered and (len(selected_clips) != 2 or
                              selected_clips[0].kind != 'caf' or
                              selected_clips[1].kind != 'caf'):
            raise ValueError('Layered animation needs one verified base CAF and one overlay CAF.')
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
            if state == 'layered' and not allow_layered:
                skipped.append(entry.name + ': needs a compatible base animation')
                continue
            if allow_layered and entry is selected_clips[0] and state == 'layered':
                raise ValueError('Base animation must be a standalone non-additive CAF.')
            try:
                data = entry.file.read_bytes()
                motion = decode_caf(data)
                matched = sum(t.controller_id in compatible_bones for t in motion.tracks)
                if motion.additive and not allow_layered:
                    raise ValueError('CAF flagged additive; requires base-pose blending')
                if motion.additive and entry is selected_clips[0]:
                    raise ValueError('Base animation cannot be an additive controller.')
                from native_caf import minimum_matching_controllers
                required = minimum_matching_controllers(len(motion.tracks))
                if matched < required:
                    raise ValueError(
                        'only %d/%d CAF controllers match this CDF rig (%d needed)' %
                        (matched, len(motion.tracks), required))
                if not any(t.rotation_times or t.position_times for t in motion.tracks):
                    raise ValueError('no keyframe tracks')
                playable.append((entry.name, data))
            except (ValueError, OSError) as exc:
                skipped.append(entry.name + ': ' + str(exc)[:110])
        if allow_layered and len(playable) != 2:
            raise ValueError('Both base and layered controllers must be decoded for blending: '
                             + '; '.join(skipped[:3]))
        if not playable:
            raise ValueError('No standalone native CAF clip could be played. '
                             'Layered aim poses require a base animation; '
                             'select a standalone idle/stand/run clip. Details: '
                             + '; '.join(skipped[:3]))
        try:
            blob, anim_report = append_caf_animations(
                blob, playable, allow_layered=allow_layered)
        except ValueError:
            # Rare files contain valid controllers but unsupported glTF
            # timelines. Retry independently so valid clips still export.
            if allow_layered:
                # A layered pair is atomic: exporting one without its base
                # would result in an invalid/misleading preview.
                raise
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
        if allow_layered and anim_report['clips'] != 2:
            raise ValueError('Layered clip export must contain both compatible motions.')
        details['animations'] = anim_report['clips']
        details['bone_channels'] = anim_report['bone_channels']
        details['skipped_animations'] = len(skipped)
        details['skipped_reasons'] = tuple(skipped[:12])
    return blob, details
