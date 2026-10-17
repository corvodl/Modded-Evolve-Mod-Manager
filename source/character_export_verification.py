"""Source-grounded checks for canonical Emet CDF character GLBs.

Avoid promoting a skinm arm or an empty body accessor to a full character.
Do not invent bones or material bindings; report actual GLB drawable geometry.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import struct

from model_textured_export import _unpack_glb

BODY = 'emet_3p.skinm'
CANNON = 'emetremotecannon_3p.skinm'
NORMAL = 'emet_3p_face_normal.skinm'


def _stem(virtual):
    return PurePosixPath(str(virtual).replace('\\', '/')).name.casefold()


def is_emet_assembly(character):
    cdf = str(getattr(character, 'cdf', '')).replace('\\', '/').casefold()
    parts = PurePosixPath(cdf).parts
    return (len(parts) >= 4 and parts[:2] == ('characters', 'hunters')
            and parts[2] in ('emet', 'merc_emet', 'medic_emet')
            and parts[-1] == 'emet_3p.cdf')


def prepare_emet_full_character(workspace, cdf, index, *, character=None,
                                texture_folder=None, projects_root=None,
                                stage_root=None, faceplate=None,
                                cancelled=None):
    """Resolve a complete Emet body + cannon, keeping the selected faceplate.

    Rebuild using the verified CDF instead of making a body/cannon file-name
    merge. Equipment is changed only for the canonical full-character export.
    """
    from character_assembly import assemble_character
    kwargs = dict(texture_folder=texture_folder, projects_root=projects_root,
                  stage_root=stage_root)
    if cancelled is not None:
        kwargs['cancel'] = cancelled
    if character is None:
        character = assemble_character(workspace, cdf, index,
                                       faceplate=faceplate, **kwargs)
    if character is None or not is_emet_assembly(character):
        raise ValueError('Emet complete export requires the verified emet_3p.cdf body assembly.')
    names = {_stem(row[0]) for row in getattr(character, 'rig_parts', ())}
    if BODY not in names:
        raise ValueError('Emet CDF body emet_3p.skinm is missing or could not be decoded.')
    if CANNON not in names:
        choices = [virtual for _label, virtual in
                   getattr(character, 'equipment_options', ())
                   if _stem(virtual) == 'emetremotecannon_3p.skin']
        if len(choices) != 1:
            raise ValueError('Emet CDF does not verify one remote cannon attachment.')
        selection = (faceplate if faceplate is not None else
                     getattr(character, 'selected_faceplate', None)) or None
        character = assemble_character(
            workspace, cdf, index, equipment=choices[0],
            faceplate=selection, **kwargs)
        if character is None or not is_emet_assembly(character):
            raise ValueError('Unable to assemble Emet body and cannon from the same CDF.')
    names = {_stem(row[0]) for row in getattr(character, 'rig_parts', ())}
    if not {BODY, CANNON} <= names:
        raise ValueError('Emet CDF did not load both body and remote cannon geometry.')
    return character


def verify_emet_glb(blob, character, *, require_default_faceplate=False):
    """Verify drawable triangles from both source parts survived GLB export.

    glb_bytes uses contiguous source vertex ranges. Check the actual index
    payload, not just a CDF name or reported part count. The merged GLB uses
    one POSITION accessor for all parts. Independent native bind poses can
    emit separate meshes, where each drawable mesh counts independently.
    """
    if not is_emet_assembly(character):
        raise ValueError('The selected model is not Emet’s canonical body CDF.')
    rig_parts = tuple(getattr(character, 'rig_parts', ()))
    labels = tuple(_stem(part[0]) for part in rig_parts)
    if labels.count(BODY) != 1 or labels.count(CANNON) != 1:
        raise ValueError('Emet export must include exactly one body and one cannon.')
    if require_default_faceplate and NORMAL not in labels:
        raise ValueError('Emet default export must include the Normal faceplate.')
    sizes = [int(part[3]) for part in rig_parts]
    if any(size < 3 for size in sizes):
        raise ValueError('Emet body or attachment has insufficient decoded vertices.')
    doc, raw = _unpack_glb(blob)
    meshes = tuple(doc.get('meshes') or ())
    accessors = doc.get('accessors') or ()
    views = doc.get('bufferViews') or ()
    if not meshes:
        raise ValueError('Emet GLB has no render meshes.')
    if not any(doc.get('nodes') or ()):
        raise ValueError('Emet GLB has no scene nodes.')
    counts = [0] * len(rig_parts)
    ends = []
    total = 0
    for size in sizes:
        total += size
        ends.append(total)
    import bisect
    for mesh_idx, mesh in enumerate(meshes):
        for primitive in mesh.get('primitives') or ():
            if primitive.get('mode', 4) != 4:
                continue
            attr = primitive.get('attributes') or {}
            posid = attr.get('POSITION')
            idxid = primitive.get('indices')
            if not isinstance(posid, int) or not isinstance(idxid, int):
                continue
            position = accessors[posid]
            accessor = accessors[idxid]
            n = int(accessor.get('count', 0))
            kind = {5121: ('B', 1), 5123: ('H', 2), 5125: ('I', 4)}.get(
                accessor.get('componentType'))
            if not kind or n < 3 or n % 3:
                continue
            view = views[accessor['bufferView']]
            offset = int(view.get('byteOffset', 0)) + int(accessor.get('byteOffset', 0))
            fmt, width = kind
            if offset < 0 or offset + n * width > len(raw):
                raise ValueError('Emet GLB index accessor is outside its buffer.')
            indices = struct.unpack_from('<%d%s' % (n, fmt), raw, offset)
            # Shared CDF armature: one merged vertex stream with source
            # positions in original rig_parts order.
            if len(meshes) == 1 and int(position.get('count', 0)) == total:
                for i in range(0, n, 3):
                    owners = tuple(bisect.bisect_right(ends, idx) for idx in indices[i:i+3])
                    if owners[0] == owners[1] == owners[2] and owners[0] < len(counts):
                        counts[owners[0]] += 1
            # Multi-armature GLB: the converter emits one drawable mesh per
            # source part, with its own local vertex/index space.
            elif len(meshes) == len(rig_parts) and mesh_idx < len(counts):
                if int(position.get('count', 0)) < 3 or max(indices) >= position['count']:
                    raise ValueError('Emet separate-armature mesh indices are invalid.')
                counts[mesh_idx] += n // 3
            else:
                raise ValueError('Emet exported vertex layout no longer matches loaded CDF parts.')
    for label in (BODY, CANNON):
        if counts[labels.index(label)] == 0:
            raise ValueError('Emet GLB contains no drawable triangles for ' + label + '.')
    return {
        'status': 'verified_body_and_remote_cannon',
        'cdf': character.cdf,
        'source_parts': [str(part[0]) for part in rig_parts],
        'part_vertex_counts': sizes,
        'part_drawn_triangles': counts,
        'total_drawn_triangles': sum(counts),
        'selected_faceplate': str(getattr(character, 'selected_faceplate', '') or ''),
        'default_faceplate_verified': NORMAL in labels,
        'mesh_count': len(meshes),
        'rig_count': len(doc.get('skins', ())),
        'texture_count': len(doc.get('textures', ())),
        'materials_count': len(doc.get('materials', ())),
        'animation_count': len(doc.get('animations', ())),
        'unresolved_parts': list(getattr(character, 'omitted', ())),
    }
