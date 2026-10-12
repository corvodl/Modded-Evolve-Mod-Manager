"""GLB diffuse texture embedding and honest skinning-readiness diagnostics.

Uses the existing verified material/DDS streaming decoder and extracted-PAK
project catalog. Unsupported or ambiguous textures remain unbound rather than
being guessed. A skeleton is never fabricated from skin indices.
"""
from __future__ import annotations
from io import BytesIO
import json
from pathlib import Path
import struct

from model_blender_export import glb_bytes
from model_asset import DATA_STREAM_CHUNK
from model_preview import read_preview_mesh

MAX_TEXTURES = 64
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _unpack_glb(blob):
    if len(blob) < 28 or struct.unpack_from('<4sII', blob) != (b'glTF', 2, len(blob)):
        raise ValueError('Invalid base GLB header.')
    json_size, kind = struct.unpack_from('<I4s', blob, 12)
    if kind != b'JSON' or json_size > len(blob) - 28:
        raise ValueError('Invalid GLB JSON chunk.')
    doc = json.loads(blob[20:20 + json_size])
    offset = 20 + json_size
    binary_size, kind = struct.unpack_from('<I4s', blob, offset)
    if kind != b'BIN\x00' or offset + 8 + binary_size != len(blob):
        raise ValueError('Invalid GLB binary chunk.')
    return doc, bytearray(blob[offset + 8:])


def _pack_glb(doc, binary):
    while len(binary) % 4:
        binary.append(0)
    doc['buffers'][0]['byteLength'] = len(binary)
    block = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    block += b' ' * (-len(block) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(block) + 8 + len(binary))
            + struct.pack('<I4s', len(block), b'JSON') + block
            + struct.pack('<I4s', len(binary), b'BIN\x00') + binary)


def embed_diffuse_textures(blob, materials):
    """Inject available diffuse maps by CryEngine subset/material-slot index.

    Returns (glb_bytes, embedded_count). Material slots lacking a matching
    diffuse texture retain the original untextured material definition.
    """
    if materials is None:
        return blob, 0
    doc, binary = _unpack_glb(blob)
    images = doc.setdefault('images', [])
    textures = doc.setdefault('textures', [])
    count = 0
    for material in doc.get('materials', []):
        name = material.get('name', '')
        prefix = 'CryEngine material slot '
        if not name.startswith(prefix):
            continue
        try:
            slot = int(name[len(prefix):].split()[0])
        except ValueError:
            continue
        if slot < 0 or slot >= len(materials.textures):
            continue
        image = materials.textures[slot]
        if image is None:
            continue
        if count >= MAX_TEXTURES:
            raise ValueError('Exceeded texture export safety limit.')
        alpha_mode = slot < len(materials.alpha_modes) and materials.alpha_modes[slot]
        cutoff = materials.alpha_cutoffs[slot] if slot < len(materials.alpha_cutoffs) else 0.
        opacity = materials.opacity[slot] if slot < len(materials.opacity) else 1.
        with BytesIO() as output:
            # Original DDS may use BCn compression; embed a portable PNG.
            image.convert('RGBA' if alpha_mode else 'RGB').save(output, format='PNG')
            payload = output.getvalue()
        if len(payload) > MAX_IMAGE_BYTES:
            raise ValueError('A material diffuse PNG is too large to embed.')
        binary.extend(b'\x00' * (-len(binary) % 4))
        view = {'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(payload)}
        binary.extend(payload)
        doc['bufferViews'].append(view)
        images.append({'bufferView': len(doc['bufferViews'])-1,
                       'mimeType': 'image/png',
                       'name': 'Diffuse slot %d' % slot})
        textures.append({'source': len(images)-1})
        material['pbrMetallicRoughness']['baseColorFactor'] = [1, 1, 1, opacity]
        material['alphaMode'] = 'MASK' if cutoff > 0 else ('BLEND' if alpha_mode or opacity < .999 else 'OPAQUE')
        if cutoff > 0:
            material['alphaCutoff'] = cutoff
        material['pbrMetallicRoughness']['baseColorTexture'] = {
            'index': len(textures)-1}
        from texture_transform import transform_for, IDENTITY
        transform = transform_for(materials, slot)
        if transform != IDENTITY:
            su, sv, ou, ov, angle = transform
            material['pbrMetallicRoughness']['baseColorTexture']['extensions'] = {
                'KHR_texture_transform': {'scale': [su, sv], 'offset': [ou, ov], 'rotation': angle}}
            extensions = doc.setdefault('extensionsUsed', [])
            if 'KHR_texture_transform' not in extensions:
                extensions.append('KHR_texture_transform')
        count += 1
    if not count:
        doc.pop('images', None)
        doc.pop('textures', None)
    return _pack_glb(doc, binary), count


def weight_stream_status(model_bytes):
    """Report whether a well-formed four-influence skin map exists.

    This does not decode skeleton transforms and is not a Blender armature.
    """
    from model_asset import inspect_model
    info = inspect_model(model_bytes)
    if info.format_name != 'CrChF':
        return {'status': 'unsupported format', 'vertices': 0}
    for i in range(info.chunks):
        typ, cid, size, offset = struct.unpack_from('<4I', info.chunk_table, i * 16)
        if typ != DATA_STREAM_CHUNK or size < 24:
            continue
        _, stream_type, n, stride, _, _ = struct.unpack_from('<6I', model_bytes, offset)
        if stream_type != 9:
            continue
        if stride != 8 or n > 250000:
            return {'status': 'unsupported bone mapping format', 'vertices': n}
        raw = memoryview(model_bytes)[offset+24:offset+size]
        if len(raw) != n*8:
            return {'status': 'truncated bone mapping', 'vertices': n}
        # Four 8-bit joint ids + four 8-bit weights. Reject malformed data.
        if any(sum(raw[i+4:i+8]) != 255 for i in range(0, len(raw), 8)):
            return {'status': 'unverified bone weights', 'vertices': n}
        return {'status': 'bone weights verified; skeleton decoder required',
                'vertices': n,
                'joints': 1 + max(raw[i+j] for i in range(0, len(raw), 8)
                                  for j in range(4) if raw[i+4+j])}
    return {'status': 'no bone mapping stream', 'vertices': 0}


def _workspace_for_file(source, projects_root):
    path = Path(source).resolve()
    projects = Path(projects_root).resolve() if projects_root else None
    for parent in path.parents:
        if (parent / '.evolve-pak-workspace.json').is_file() and (
                projects is None or parent.is_relative_to(projects)):
            files = (parent / 'files').resolve()
            if path.is_relative_to(files):
                return parent, path.relative_to(files).as_posix()
    return None, None


def textured_model_from_workspace(mesh, workspace, relative,
                                  *, texture_folder=None, projects_root=None,
                                  stage_root=None, index=None, materials=None):
    """Return GLB plus texturing/rig readiness information, no on-disk writes."""
    from material_preview import load_preview_materials, _workspace_index
    if index is None:
        # Use the same cross-PAK index for textures AND the native rig companion.
        # In particular, an Asset Library model's .skin may be in a different
        # imported game archive, and extracted folders need the shared TS PAK.
        try:
            index = _workspace_index(workspace, projects_root, stage_root)
        except (OSError, ValueError, KeyError):
            index = None
    payload = glb_bytes(mesh, name=Path(relative).stem)
    report = {'texture_count': 0, 'texture_status': 'No material resources found',
              'rig_status': 'Unrigged: no compatible skin source found',
              'bone_count': 0}
    try:
        mats = materials if materials is not None else load_preview_materials(
            workspace, relative, texture_folder,
            projects_root=projects_root, stage_root=stage_root, index=index)
    except (ValueError, OSError) as exc:
        report['texture_status'] = str(exc)
    else:
        payload, count = embed_diffuse_textures(payload, mats)
        warnings = tuple(mats.issues)
        report.update(texture_count=count,
                      texture_status='%d diffuse textures embedded from unpacked PAKs' % count,
                      texture_warnings=warnings)
    # Resolve the original .skin/.chr companion in a safe, read-only way.
    # CompiledBones stores inverse-bind and rest-pose matrices; each mesh
    # subset has its own local-to-global bone palette.
    from model_rigging import skin_glb, skin_companion_path
    from multi_pak_assets import locate_related_asset
    companion = skin_companion_path(relative)
    if companion is not None:
        files_root = (Path(workspace).resolve() / 'files').resolve()
        model_file = (files_root / relative).resolve()
        bone_file = (files_root / companion).resolve()
        if not bone_file.is_file() or not bone_file.is_relative_to(files_root):
            # The selected model may come from the permanent Asset Library,
            # with its matching .skin/.chr in another imported PAK. Honor the
            # same virtual-path catalog used to render its materials.
            if index is not None:
                from multi_pak_assets import _resolve
                hits, state = _resolve(index, companion, preferred_workspace=workspace)
                bone_file = Path(hits[0]['file']) if state == 'found' else None
                if state == 'ambiguous':
                    report['rig_status'] = 'Unrigged: multiple different skin companions'
            else:
                bone_file = locate_related_asset(
                    workspace, companion.as_posix(), projects_root, stage_root)
        if (model_file.is_file() and model_file.is_relative_to(files_root)
                and bone_file is not None and Path(bone_file).is_file()):
            try:
                payload, rig = skin_glb(
                    payload, model_file.read_bytes(), Path(bone_file).read_bytes())
            except (ValueError, OSError) as error:
                report['rig_status'] = 'Unrigged: ' + str(error)
            else:
                report['bone_count'] = rig['bone_count']
                report['rig_status'] = (
                    'Rigged: %d bones, %d active (original bind poses and weights)' %
                    (rig['bone_count'], rig['active_bones']))
    return payload, report


def export_extracted_model(source, destination, *, projects_root=None,
                           stage_root=None, texture_folder=None):
    source, destination = Path(source).resolve(), Path(destination)
    from model_blender_export import convert_model
    workspace, rel = _workspace_for_file(source, projects_root)
    if workspace is None:
        # A loose extracted folder may still contain a matching .skin/.chr.
        # Keep its unverified external texture resources separate; no guessing.
        from model_rigging import skin_glb, skin_companion_path
        mesh = read_preview_mesh(source.read_bytes(), source.name)
        blob = glb_bytes(mesh, name=source.stem)
        companion = skin_companion_path(source)
        report = {'texture_count': 0,
                  'texture_status': 'Texture lookup requires a compatible unpacked project.',
                  'rig_status': 'Unrigged: native skin companion not found', 'bone_count': 0}
        if companion is not None and companion.is_file() and not companion.is_symlink():
            try:
                blob, rig = skin_glb(
                    blob, source.read_bytes(), companion.read_bytes())
            except (ValueError, OSError) as error:
                report['rig_status'] = 'Unrigged: ' + str(error)
            else:
                report['bone_count'] = rig['bone_count']
                report['rig_status'] = 'Rigged: %d bones, %d active' % (
                    rig['bone_count'], rig['active_bones'])
        if destination.suffix.casefold() != '.glb' or not destination.parent.is_dir():
            raise ValueError('Choose an existing export folder and .glb filename.')
        destination.write_bytes(blob)
        return (len(mesh.vertices), len(mesh.triangles)), report
    mesh = read_preview_mesh(source.read_bytes(), source.name)
    glb, report = textured_model_from_workspace(
        mesh, workspace, rel, texture_folder=texture_folder,
        projects_root=projects_root, stage_root=stage_root)
    if destination.suffix.casefold() != '.glb' or not destination.parent.is_dir():
        raise ValueError('Export to an existing folder with a .glb filename.')
    if destination.resolve() == source:
        raise ValueError('Cannot overwrite the original CryEngine model.')
    destination.write_bytes(glb)
    report['weight_status'] = weight_stream_status(source.read_bytes())['status']
    return (len(mesh.vertices), len(mesh.triangles)), report


def export_extracted_folder(folder, destination, *, projects_root=None,
                            stage_root=None, texture_folder=None,
                            progress=None, cancelled=None):
    from model_blender_export import MAX_FOLDER_FILES
    folder, destination = Path(folder).resolve(), Path(destination).resolve()
    if not folder.is_dir() or not destination.is_dir():
        raise FileNotFoundError('Choose existing model and export directories.')
    if destination == folder or destination.is_relative_to(folder):
        raise ValueError('Choose a separate export folder outside model sources.')
    candidates = sorted((p for p in folder.rglob('*') if p.is_file() and
                         not p.is_symlink() and p.suffix.casefold() in
                         ('.skinm', '.chrm', '.cgf', '.cgfm', '.cga', '.cgam')))
    if len(candidates) > MAX_FOLDER_FILES:
        raise ValueError('Model count exceeds exporter limit.')
    done, errors = [], []
    if progress:
        progress('Exporting Blender models', 0, len(candidates))
    for i, p in enumerate(candidates, 1):
        if cancelled and cancelled():
            raise InterruptedError('Blender folder export cancelled.')
        rel = p.relative_to(folder)
        if p.suffix.casefold() == '.chrm' and p.with_suffix('.skinm').exists():
            if progress:
                progress('Exporting Blender models', i, len(candidates))
            continue
        target = destination / rel.with_suffix('.glb')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            counts, details = export_extracted_model(
                p, target, projects_root=projects_root, stage_root=stage_root,
                texture_folder=texture_folder)
            done.append((str(rel), str(target.relative_to(destination)), counts, details))
        except (ValueError, OSError) as exc:
            errors.append((str(rel), str(exc)))
        if progress:
            progress('Exporting Blender models', i, len(candidates))
    return done, errors
