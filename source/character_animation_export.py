"""Export verified character motion beside Blender GLBs, not empty placeholders.

A character's CDF/CHRParams is the only authority for CAF/DBA mappings.
Standalone CAFs with compatible controllers can become one rigged Blender GLB
containing named clips. Packed DBA motion is converted at most once per
export through the existing validated bridge. Raw CAF/DBA sources are
optional attachments, never mislabeled as playable GLB animations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

MAX_ANIMATION_REFERENCES = 512
MAX_NATIVE_CLIPS = 256
MAX_RAW_SOURCES = 32
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RAW_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ANIMATED_GLB_BYTES = 512 * 1024 * 1024


def _safe(value):
    return re.sub(r'[^a-zA-Z0-9_.-]+', '_', str(value)).strip('._')[:72] or 'Animation'


def _bounded_catalog_for_export(references, skeleton, cancelled=None):
    """Favor verified animation sources, never fail on a large .chrparams.

    The source can contain thousands of absent CAF declarations and metadata.
    Bound the work, not the original catalog; do not invent motion or mix rigs.
    """
    total = len(references)
    if total <= MAX_ANIMATION_REFERENCES:
        return references, 0
    from character_animation_catalog import (
        native_clip_playability, preferred_third_person_dba)
    preferred = preferred_third_person_dba(references, skeleton)
    selected_dba = (preferred.virtual_path.casefold()
                    if preferred is not None else None)
    ranked = []
    for order, ref in enumerate(references):
        if cancelled is not None and cancelled():
            raise InterruptedError('Character animation export cancelled.')
        status, _reason = native_clip_playability(ref)
        if (selected_dba and ref.kind == 'dba'
                and ref.virtual_path.casefold() == selected_dba):
            priority = 0
        elif ref.kind == 'caf' and status == 'candidate':
            priority = (1 if re.search(r'(^|[_-])idle($|[_-])',
                                       ref.name.casefold()) else 2)
        elif ref.kind == 'dba' and status == 'converter':
            priority = 3
        elif ref.kind == 'caf' and status in ('converter', 'extractable'):
            priority = 4
        elif ref.kind == 'caf' and status == 'layered':
            priority = 5
        else:
            priority = 6
        ranked.append((priority, order, ref))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    chosen = tuple(ref for _priority, _order, ref
                   in ranked[:MAX_ANIMATION_REFERENCES])
    return chosen, total - len(chosen)


def export_character_animation_folder(
        root, model_blob, relative, index, workspace, *,
        character=None, model_name=None, cancelled=None, progress=None,
        convert_database=True, references=None, copy_sources=True):
    """Create <root>/animations/{<rig>_animations.glb,sources/,manifest.json}.

    Model payload must already be a *verified* CDF Blender GLB; conversion
    reuses its bone weights and material textures rather than inventing a rig.
    No path is guessed from similarly named game characters. A manifest
    explicitly records clips that were not convertible.
    """
    from character_animation_catalog import (
        discover_animations, native_clip_playability, caf_header,
        preferred_third_person_dba)
    from model_textured_export import _unpack_glb
    from native_caf import append_caf_animations

    def checkpoint():
        if cancelled is not None and cancelled():
            raise InterruptedError('Character animation export cancelled.')

    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Animation export root must be a real directory.')
    folder = root / 'animations'
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise FileExistsError('Animation output conflicts with an existing file.')
    if (folder / 'animation_manifest.json').exists():
        raise FileExistsError('Animation manifest already exists.')
    checkpoint()
    if references is None:
        selected = character.skeleton if character is not None else relative
        references, _idle, note = discover_animations(
            selected, index, preferred_workspace=workspace, cancel=cancelled)
    else:
        references = tuple(references)
        note = 'Supplied verified character animation catalog'
    catalog_total = len(references)
    references, catalog_omitted = _bounded_catalog_for_export(
        references, character.skeleton if character is not None else relative,
        cancelled=cancelled)
    if catalog_omitted:
        note += ('; selected %d export-relevant references from %d declarations '
                 '(%d omitted under bounded animation export budget)'
                 % (len(references), catalog_total, catalog_omitted))
    doc, _ = _unpack_glb(model_blob)
    rigged = len(doc.get('skins', ())) == 1
    prefix = _safe(model_name or Path(relative).stem)
    preferred_database = preferred_third_person_dba(
        references, character.skeleton if character is not None else relative)
    statuses = []
    accepted = []
    sources = []
    total_bytes = 0
    folder.mkdir(exist_ok=True)
    raw_root = folder / 'sources'
    if raw_root.is_symlink():
        raise ValueError('Refusing symlinked animation sources directory.')
    raw_root.mkdir(exist_ok=True)
    source_names = set()
    chosen_dba = None
    for ref in references:
        checkpoint()
        status, explanation = native_clip_playability(ref)
        row = {'name': ref.name, 'virtual': ref.virtual_path,
               'kind': ref.kind, 'availability': status}
        path = Path(ref.file) if ref.available and ref.file is not None else None
        if copy_sources and path is not None and ref.kind in ('caf', 'dba'):
            try:
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Source not a regular file')
                size = path.stat().st_size
                if size < 16 or size > MAX_SOURCE_BYTES:
                    raise ValueError('Source exceeds per-asset safety limit')
                if (len(sources) >= MAX_RAW_SOURCES or
                        total_bytes + size > MAX_RAW_TOTAL_BYTES):
                    raise ValueError('Raw source export budget reached')
                unique = hashlib.sha256(ref.virtual_path.casefold().encode('utf-8')).hexdigest()[:10]
                label = _safe(Path(ref.virtual_path).stem) + '_' + unique + path.suffix.casefold()
                if label in source_names:
                    row['source_file'] = 'sources/' + label
                else:
                    destination = raw_root / label
                    with path.open('rb') as inp, destination.open('xb') as out:
                        shutil.copyfileobj(inp, out, 1024 * 1024)
                    row['source_file'] = 'sources/' + label
                    sources.append(row['source_file'])
                    source_names.add(label)
                    total_bytes += size
            except (ValueError, OSError) as exc:
                row['source_note'] = str(exc)[:150]
        # Only validated standalone CAFs are suitable for automatic clip
        # export. DBA references are not standalone tracks or inferred CAFs.
        if status == 'candidate' and ref.kind == 'caf' and path is not None:
            if not rigged:
                row['conversion'] = 'Skipped: exported model has no single verified rig'
            elif len(accepted) >= MAX_NATIVE_CLIPS:
                row['conversion'] = 'Skipped: 256 verified native CAF clip safety limit'
            else:
                try:
                    if not caf_header(path):
                        raise ValueError('CAF header could not be verified')
                    if progress:
                        progress('Checking CAF: ' + ref.name, len(accepted),
                                 MAX_NATIVE_CLIPS)
                    blob, report = append_caf_animations(
                        model_blob, [(ref.name, path.read_bytes())])
                    if report.get('clips') != 1:
                        raise ValueError('No valid animation timeline')
                    if len(blob) > MAX_ANIMATED_GLB_BYTES:
                        raise ValueError('Animated GLB safety budget exceeded')
                    accepted.append((ref, row))
                    row['conversion'] = 'Validated'
                except (OSError, ValueError) as exc:
                    row['conversion'] = 'Unsupported: ' + str(exc)[:160]
        elif ref.kind == 'dba' and status == 'converter':
            if chosen_dba is None or (
                    preferred_database is not None and
                    ref.virtual_path == preferred_database.virtual_path):
                chosen_dba = ref
            row['conversion'] = 'DBA conversion candidate (verified separately)'
        else:
            row['conversion'] = 'Not a standalone convertible clip: ' + explanation[:125]
        statuses.append(row)

    # A DBA database and loose CAF clips are independent valid motion sources.
    # Do not suppress the verified DBA simply because some loose CAFs converted:
    # that previously made 19 valid CAFs hide hundreds of database references.
    # Always attach both sets to this same checked rig, never retarget by name.
    animated = model_blob
    converted = []
    converted_keys = set()
    dba_failed = None
    if rigged and convert_database and chosen_dba is not None and character is not None:
        checkpoint()
        matching = next(
            (row for row in statuses if row['virtual'] == chosen_dba.virtual_path), None)
        try:
            from database_animation import (
                convert_database_motion, convert_historical_database_motion)
            historical = (bool(character.rig_parts) and
                          all(str(part[0]).casefold().endswith('.chr')
                              for part in character.rig_parts))
            if progress:
                progress('Converting verified DBA for Blender', 0, 1)
            if historical:
                proposed = convert_historical_database_motion(
                    model_blob, chosen_dba, index, character,
                    preferred_workspace=workspace, cancelled=cancelled)
            else:
                proposed = convert_database_motion(
                    model_blob, chosen_dba, index, character,
                    preferred_workspace=workspace)
            database_doc, _ = _unpack_glb(proposed)
            clips = database_doc.get('animations', ())
            if (len(database_doc.get('skins', ())) != 1 or not clips
                    or len(clips) > MAX_NATIVE_CLIPS
                    or len(proposed) > MAX_ANIMATED_GLB_BYTES):
                raise ValueError('DBA conversion produced no bounded rigged animation GLB')
            names = [clip.get('name','').strip() for clip in clips]
            if any(not name for name in names) or len(set(names)) != len(names):
                raise ValueError('DBA converter produced missing or duplicate clip names')
            animated = proposed
            converted.extend(names)
            converted_keys.update(name.casefold() for name in names)
            if matching is not None:
                matching['conversion'] = (
                    '%d verified DBA clips exported into animated GLB' % len(names))
        except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
            dba_failed = str(exc)[:175]
            if matching is not None:
                matching['conversion'] = 'DBA unavailable: ' + dba_failed

    # Only standalone rig-compatible CAFs are appended; all source material,
    # original skeleton mapping and already verified DBA tracks stay intact.
    for ref, row in accepted if rigged else ():
        checkpoint()
        key = ref.name.casefold()
        if key in converted_keys:
            row['conversion'] = 'Already provided by verified DBA (not duplicated)'
            continue
        if len(converted) >= MAX_NATIVE_CLIPS:
            row['conversion'] = 'Skipped: combined CAF/DBA limit of 256 verified clips'
            continue
        try:
            proposed, details = append_caf_animations(
                animated, [(ref.name, Path(ref.file).read_bytes())])
            if (details.get('clips') != 1 or
                    len(proposed) > MAX_ANIMATED_GLB_BYTES):
                raise ValueError('Combined animation GLB exceeds safety budget')
            updated, _ = _unpack_glb(proposed)
            if (len(updated.get('skins', ())) != 1 or
                    len(updated.get('animations', ())) != len(converted) + 1):
                raise ValueError('CAF append changed verified rig or animation count')
            animated = proposed
        except (OSError, ValueError) as exc:
            row['conversion'] = 'Failed assembling: ' + str(exc)[:160]
            continue
        row['conversion'] = 'Exported into animated GLB'
        converted.append(ref.name)
        converted_keys.add(key)

    # Write a single final GLB only when its complete track manifest is valid.
    # The saved manifest distinguishes actual Action clips from references.
    if converted:
        checkpoint()
        (folder / (prefix + '_animations.glb')).write_bytes(animated)
    for row in statuses:
        if (row['kind'] == 'dba'
                and row['conversion'] == 'DBA conversion candidate (verified separately)'):
            row['conversion'] = (
                'Not converted: preferred DBA was processed for this skeleton'
                if chosen_dba is not None else 'Not converted: no verified DBA selected')
    note_text = (
        'Animated GLB includes original rig, mesh and embedded textures. '
        'CAF/DBA source attachments are CryEngine data, not separate Blender animation files.'
        if converted else
        'No animation tracks were validated against this export rig. Raw CAF/DBA '
        'attachments are provided when available; original CryEngine databases '
        'need compatible converter output and skeleton binding.')
    report = {'format': 'evolve-character-animation-export-v1',
              'model': model_name or Path(relative).name, 'source': relative,
              'rigged_single_skin': rigged,
              'converted_clips': converted,
              'animated_glb': prefix + '_animations.glb' if converted else None,
              'raw_sources': sources,
              'references': statuses, 'catalog_note': note,
              'catalog_total': catalog_total,
              'catalog_processed': len(references),
              'catalog_omitted': catalog_omitted,
              'note': note_text}
    (folder / 'animation_manifest.json').write_text(
        json.dumps(report, indent=2), encoding='utf-8')
    return {'animations': len(converted), 'sources': len(sources),
            'manifest': str(folder / 'animation_manifest.json'),
            'animated_glb': report['animated_glb']}



def embed_verified_assembly_animations(model_blob, character, index, workspace, *,
                                       relative=None, cancelled=None, progress=None,
                                       references=None):
    """Return (animated GLB, report) for this exact assembled CDF skeleton.

    Reuse the *same* verified CAF/DBA pathway as the standalone animations/
    export. Each CDF is evaluated independently; never retarget tracks merely
    because two models have similar names. Temporary conversion files are
    deleted, and raw CAF/DBA sources are not copied into each assembly.
    An unrigged model is left unmodified with an explicit reason.
    """
    from model_textured_export import _unpack_glb

    if character is None or not getattr(character, 'cdf', None):
        raise ValueError('Assembly needs an exact verified CDF.')
    if cancelled is not None and cancelled():
        raise InterruptedError('Assembly animation export cancelled.')
    relative = relative or character.cdf
    if str(relative).casefold() != str(character.cdf).casefold():
        raise ValueError('Animation CDF and assembled model do not match.')
    document, _binary = _unpack_glb(model_blob)
    if len(document.get('skins', ())) != 1:
        return model_blob, {
            'animations': 0, 'converted_clips': [],
            'note': 'This assembly has no single verified armature; animations not embedded.'
        }
    # The standalone exporter already validates each CAF controller against
    # this precise GLB and uses original CDF/CHRParams for packed DBA (including
    # CryTek 2012/0x745 rigs). Use it, not a second independent decoder.
    with TemporaryDirectory(prefix='evolve-assembly-motion-') as tmp:
        temp_root = Path(tmp)
        status = export_character_animation_folder(
            temp_root, model_blob, relative, index, workspace,
            character=character, model_name=Path(relative).stem,
            cancelled=cancelled, progress=progress,
            references=references, copy_sources=False)
        manifest = temp_root / 'animations' / 'animation_manifest.json'
        report = json.loads(manifest.read_text(encoding='utf-8'))
        names = report.get('converted_clips', [])
        result = {
            'animations': status['animations'],
            'converted_clips': names,
            'note': report['note'],
            'catalog_note': report.get('catalog_note', '')
        }
        if not status['animations'] or not status['animated_glb']:
            return model_blob, result
        candidate = temp_root / 'animations' / status['animated_glb']
        if (not candidate.is_file() or candidate.is_symlink()
                or candidate.stat().st_size > MAX_ANIMATED_GLB_BYTES):
            raise ValueError('Validated animated CDF assembly output is missing or too large.')
        animated = candidate.read_bytes()
        animated_doc, _ = _unpack_glb(animated)
        if (len(animated_doc.get('skins', ())) != 1
                or len(animated_doc.get('animations', ())) != len(names)
                or not names):
            raise ValueError('Embedded GLB lost the exact rig or verified animation clips.')
        result['animated_glb_embedded'] = True
        return animated, result
