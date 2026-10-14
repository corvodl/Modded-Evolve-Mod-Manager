"""Convert packed DBA motion with a real skin, then retain the viewer's mesh/materials."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET
import numpy as np
from multi_pak_assets import normalized_name
from model_textured_export import _unpack_glb, _pack_glb


def attach_converted_motion(blob, converted, requested=None):
    """Copy tracks only when joint names, parents, and rest transforms agree."""
    from model_animation import _local_matrix, AnimatedGLB
    from converted_motion import clip_key
    doc, binary = _unpack_glb(blob)
    other, payload = _unpack_glb(converted)
    if not other.get('animations') or not other.get('skins') or not other.get('meshes'):
        raise ValueError('Converter produced no rigged animation; empty output is not playable.')
    if len(doc.get('skins', [])) != 1 or len(other['skins']) != 1:
        raise ValueError('DBA transfer requires one verified skeleton per model.')
    def bones(d):
        result = {}
        for i in d['skins'][0]['joints']:
            name = d['nodes'][i].get('name', '').casefold()
            if not name or name in result:
                raise ValueError('Missing or duplicate converted bone name.')
            result[name] = i
        return result
    target, source = bones(doc), bones(other)
    def parents(d):
        return {c: i for i, n in enumerate(d['nodes']) for c in n.get('children', [])}
    tp, sp = parents(doc), parents(other)
    remap = {}
    for name, si in source.items():
        if name not in target:
            continue
        ti = target[name]
        def parent_name(d, table, i, joints):
            parent = table.get(i)
            return d['nodes'][parent].get('name', '').casefold() if parent in joints else None
        if parent_name(doc, tp, ti, target.values()) != parent_name(other, sp, si, source.values()):
            raise ValueError('Converted bone hierarchy differs: ' + name)
        if not np.allclose(_local_matrix(doc['nodes'][ti]), _local_matrix(other['nodes'][si]), atol=.002, rtol=.002):
            raise ValueError('Converted bone rest transform differs: ' + name)
        remap[si] = ti
    animations = other['animations']
    if requested:
        goal = clip_key(requested)
        exact = [a for a in animations if clip_key(a.get('name', '')) == goal]
        animations = exact or [a for a in animations if clip_key(a.get('name', '')).rsplit('/', 1)[-1] == goal.rsplit('/', 1)[-1]]
        if len(animations) != 1:
            raise ValueError('Requested DBA motion has %d exact source matches.' % len(animations))
    if not animations or len(animations) > 512:
        raise ValueError('No supported bounded animation set in converter output.')
    binary.extend(b'\0' * (-len(binary) % 4))
    byte_base, view_base, accessor_base = len(binary), len(doc['bufferViews']), len(doc['accessors'])
    binary.extend(payload)
    for view in other.get('bufferViews', []):
        item = deepcopy(view)
        if item.get('buffer', 0) != 0:
            raise ValueError('External converter buffers are unsupported.')
        item['byteOffset'] = item.get('byteOffset', 0) + byte_base
        doc['bufferViews'].append(item)
    for acc in other.get('accessors', []):
        item = deepcopy(acc)
        if 'sparse' in item or 'bufferView' not in item:
            raise ValueError('Unsupported converted animation accessor.')
        item['bufferView'] += view_base
        doc['accessors'].append(item)
    doc['animations'] = []
    for animation in animations:
        item = deepcopy(animation)
        if not item.get('channels'):
            raise ValueError('Converted clip has no animation channels.')
        for channel in item['channels']:
            si = channel['target']['node']
            if si not in remap or channel['target']['path'] not in ('rotation', 'translation', 'scale'):
                raise ValueError('Converted animation does not match this character skeleton.')
            ti = remap[si]
            if 'matrix' in other['nodes'][si]:
                raise ValueError('Converter returned an animated matrix-only bone.')
            channel['target']['node'] = ti
            # AnimatedGLB requires TRS nodes. Copy only the verified equivalent rest transform.
            node = doc['nodes'][ti]
            node.pop('matrix', None)
            for key, default in (('translation', [0,0,0]), ('rotation', [0,0,0,1]), ('scale', [1,1,1])):
                node[key] = deepcopy(other['nodes'][si].get(key, default))
        for sampler in item['samplers']:
            sampler['input'] += accessor_base
            sampler['output'] += accessor_base
        doc['animations'].append(item)
    result = _pack_glb(doc, binary)
    AnimatedGLB(result)  # Verify bounds, controllers and playable timelines before success.
    return result


def convert_database_motion(blob, reference, index, character, *, preferred_workspace=None,
                            executable=None, cache_root=None):
    from animation_converter_bridge import bundled_converter, validate_conversion
    from character_animation_catalog import selected_character_params
    if character is None or not character.rig_parts:
        raise ValueError('Open Full Character to convert DBA motion with its verified skeleton.')
    database = reference.file if reference.kind == 'dba' else reference.dba_file
    virtual_db = reference.virtual_path if reference.kind == 'dba' else reference.dba_virtual_path
    if database is None or not virtual_db:
        raise ValueError('No verified database for this motion.')
    _, params = selected_character_params(character.skeleton, index, preferred_workspace)
    if params is None:
        raise ValueError('Character skeleton has no verified animation configuration.')
    # Use an actual selected skin with compiled bones, never the database alone.
    render_virtual, render_file, skin_file, _ = character.rig_parts[0]
    if skin_file is None:
        raise ValueError('Selected character skin is unavailable.')
    with TemporaryDirectory(prefix='evolve-dba-', dir=cache_root) as temporary:
        root = Path(temporary)/'files';out = Path(temporary)/'output'
        root.mkdir();out.mkdir()
        def copy(source, virtual):
            relative = normalized_name(virtual)
            dest = root / relative
            if not dest.resolve().is_relative_to(root.resolve()):
                raise ValueError('Unsafe converter asset path.')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            return dest
        skin_virtual = render_virtual[:-1]
        skin = copy(skin_file, skin_virtual)
        copy(render_file, render_virtual)
        copy(database, virtual_db)
        # Minimal configuration selects only this verified database; no game XML is changed.
        xml = ET.Element('Params');listing = ET.SubElement(xml, 'AnimationList')
        ET.SubElement(listing, 'Animation', name='$TracksDatabase', path=normalized_name(virtual_db))
        skin.with_suffix('.chrparams').write_bytes(ET.tostring(xml))
        # Material data is optional for conversion; the original GLB's selections are retained.
        program, args = validate_conversion(executable or bundled_converter(), skin, root, out)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.run([program, *args], cwd=out, capture_output=True,
                                 timeout=180, creationflags=flags)
        if process.returncode:
            raise ValueError('DBA converter failed: ' + process.stderr.decode('utf8', 'replace')[-800:])
        files = list(out.rglob('*.glb'))
        if len(files) != 1 or files[0].stat().st_size > 192*1024*1024:
            raise ValueError('Converter did not produce one bounded rigged model.')
        return attach_converted_motion(blob, files[0].read_bytes(),
            reference.virtual_path if reference.kind == 'caf' else None)



def convert_historical_database_motion(blob, reference, index, character, *,
                                       preferred_workspace=None, executable=None,
                                       cache_root=None, cancelled=None):
    """Convert real CryTek 0x745 DBA motion against the exact CDF master rig.

    Work entirely in a throwaway copy. Never replace the skeleton's original
    .chrparams with a generated stub: that would lose the clip aliases and
    sometimes cause the converter to bind a different skeleton. Converted
    animations remain unavailable unless their names, controller hierarchy,
    rest transforms and keyframes validate against our already-rigged GLB.
    """
    from animation_converter_bridge import bundled_converter, validate_conversion
    from character_animation_catalog import selected_character_params
    from converted_motion import clip_key
    from legacy_crytek_rig import probe_legacy_dba
    from multi_pak_assets import _resolve
    import time

    if (character is None or len(character.rig_parts) != 1 or
            character.rig_parts[0][2] is not None):
        raise ValueError('2012 DBA conversion requires one validated .chr CDF body.')
    body_virtual, body_path, _, _ = character.rig_parts[0]
    if not body_virtual.casefold().endswith('.chr'):
        raise ValueError('Historical CDF body must be the original .chr asset.')

    virtual_db = (reference.virtual_path if reference.kind == 'dba'
                  else reference.dba_virtual_path)
    database = reference.file if reference.kind == 'dba' else reference.dba_file
    if database is None or not virtual_db:
        raise ValueError('No verified historical DBA for this selection.')
    raw_db = Path(database).read_bytes()
    probe_legacy_dba(raw_db)  # exact controller 0x905 container validation

    skeleton, status = _resolve(index, character.skeleton,
                                preferred_workspace=preferred_workspace)
    if status != 'found':
        raise ValueError('Historical master skeleton is missing or ambiguous.')
    config_name, config_path = selected_character_params(
        character.skeleton, index, preferred_workspace)
    if config_path is None:
        raise ValueError('Original master-skeleton .chrparams is unavailable.')
    config_data = Path(config_path).read_bytes()
    if len(config_data) > 2 * 1024 * 1024 or b'<!ENTITY' in config_data.upper() or b'<!DOCTYPE' in config_data.upper():
        raise ValueError('Unsafe or oversized historical .chrparams.')

    # Require an explicit declaration in the same skeleton configuration.
    try:
        params = ET.fromstring(config_data)
        entries = params.findall('./AnimationList/Animation')
        declared = [
            normalized_name((row.get('path') or '').replace('\\', '/'))
            for row in entries if (row.get('name') or '').casefold() == '$tracksdatabase'
        ]
    except (ValueError, ET.ParseError) as exc:
        raise ValueError('Invalid original historical .chrparams.') from exc
    if virtual_db.casefold() not in {name.casefold() for name in declared}:
        raise ValueError('The selected DBA is not declared by this exact skeleton.')
    if cancelled and cancelled():
        raise ValueError('Historical DBA conversion cancelled.')

    with TemporaryDirectory(prefix='evolve-2012-dba-', dir=cache_root) as temporary:
        home = Path(temporary)
        root, out = home / 'files', home / 'output'
        root.mkdir()
        out.mkdir()
        def stage(source, virtual):
            name = normalized_name(virtual)
            dest = root.joinpath(*name.split('/'))
            if dest.is_symlink() or not dest.resolve().is_relative_to(root.resolve()):
                raise ValueError('Converter input path escapes the isolated directory.')
            dest.parent.mkdir(parents=True, exist_ok=True)
            with Path(source).open('rb') as inp, dest.open('xb') as dst:
                shutil.copyfileobj(inp, dst, 1024 * 1024)
            return dest

        stage(body_path, body_virtual)
        master = stage(skeleton[0]['file'], character.skeleton)
        stage(config_path, config_name)
        stage(database, virtual_db)
        cdf, state = _resolve(index, character.cdf,
                              preferred_workspace=preferred_workspace)
        if state != 'found':
            raise ValueError('Selected historical CDF is missing or ambiguous.')
        stage(cdf[0]['file'], character.cdf)
        # Convert the master-skeleton .chr, not the 77-bone body. Its
        # original CHRPARAMS provides the 89-bone animation binding aliases.
        program, args = validate_conversion(
            executable or bundled_converter(), master, root, out)
        args.append('-notex')  # our existing GLB keeps its material choices
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if cancelled and cancelled():
            raise ValueError('Historical DBA conversion cancelled.')
        proc = subprocess.Popen([program, *args], cwd=out,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=flags)
        deadline = time.monotonic() + 180
        try:
            while True:
                if cancelled and cancelled():
                    raise ValueError('Historical DBA conversion cancelled.')
                if time.monotonic() > deadline:
                    raise ValueError('Historical DBA converter exceeded 180 seconds.')
                try:
                    _, stderr = proc.communicate(timeout=.4)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
            raise
        if proc.returncode:
            raise ValueError('Historical 0x905 DBA converter rejected this build: '
                             + stderr.decode('utf-8', 'replace')[-600:])

        outputs = sorted(path for path in out.rglob('*.glb')
                         if path.is_file() and not path.is_symlink()
                         and path.resolve().is_relative_to(out.resolve()))
        if not outputs or len(outputs) > 128 or any(
                p.stat().st_size > 192 * 1024 * 1024 for p in outputs):
            raise ValueError('Converter produced no bounded GLB motion output.')

        requested = (reference.virtual_path if reference.kind == 'caf' else None)
        if len(outputs) > 1:
            if requested is None:
                raise ValueError('DBA produced multiple clips; choose a named '
                                 'standalone animation (idle, walk, etc.) first.')
            # Inspect actual animation names, never trust output filenames.
            matches = []
            wanted = clip_key(requested)
            for path in outputs:
                candidate, _ = _unpack_glb(path.read_bytes())
                names = [clip_key(a.get('name', '')) for a in
                         candidate.get('animations', ())]
                if any(name == wanted or
                       name.rsplit('/', 1)[-1] == wanted.rsplit('/', 1)[-1]
                       for name in names):
                    matches.append(path)
            if len(matches) != 1:
                raise ValueError('Requested motion is not uniquely present in '
                                 'the converter output (%d matches).' % len(matches))
            outputs = matches
        return attach_converted_motion(blob, outputs[0].read_bytes(), requested)
