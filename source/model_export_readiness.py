"""Validate final glTF material/UV coverage and native LOD0 provenance.

Rigged character geometry must retain original render-vertex IDs until skin
weights are assigned. This final-GLB audit instead checks every textured
primitive's complete UV stream without rewriting geometry or source assets.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re

from model_textured_export import _unpack_glb

_LOWER_LOD = re.compile(r"_lod[1-9][0-9]*$", re.IGNORECASE)


def ensure_lod0_parts(character):
    """Do not substitute lower-detail source meshes into full CDF exports."""
    lower = []
    for render, *_rest in getattr(character, 'rig_parts', ()) or ():
        virtual = str(render).replace('\\', '/')
        if _LOWER_LOD.search(PurePosixPath(virtual).stem):
            lower.append(virtual)
    if lower:
        raise ValueError('LOD0 character export requires original highest-detail '
                         'mesh parts, not lower LODs: ' + ', '.join(lower[:5]))
    return {'lod': 0, 'render_parts_checked': len(
        getattr(character, 'rig_parts', ()) or ())}


def audit_material_uvs(blob):
    """Require each final textured material primitive to have complete UV0.

    glTF allows one complete vertex UV0 stream shared by multiple material
    primitives. Each material still has its own indexed triangles and PNG
    assignment; duplicating UV data for every material is unnecessary.
    """
    doc, _binary = _unpack_glb(blob)
    accessors = doc.get('accessors') or []
    materials = doc.get('materials') or []
    counts = {}
    primitives = 0
    textured = 0
    for mesh in doc.get('meshes') or ():
        for primitive in mesh.get('primitives') or ():
            if primitive.get('mode', 4) != 4:
                continue
            primitives += 1
            attributes = primitive.get('attributes') or {}
            position_id = attributes.get('POSITION')
            if (not isinstance(position_id, int)
                    or position_id < 0 or position_id >= len(accessors)):
                raise ValueError('Exported triangle material lacks valid positions.')
            vertex_count = int(accessors[position_id].get('count', 0))
            if vertex_count < 3:
                raise ValueError('Exported material has fewer than three vertices.')
            slot = primitive.get('material')
            if slot is None:
                continue
            if not isinstance(slot, int) or not 0 <= slot < len(materials):
                raise ValueError('Exported material index is invalid.')
            material = materials[slot]
            name = str(material.get('name', 'Material ' + str(slot)))
            counts[name] = counts.get(name, 0) + 1
            # A texture-ready material group needs a *complete* UV0 stream
            # even before artists assign replacement textures.
            uv0 = attributes.get('TEXCOORD_0')
            if (not isinstance(uv0, int)
                    or not 0 <= uv0 < len(accessors)
                    or accessors[uv0].get('type') != 'VEC2'
                    or int(accessors[uv0].get('count', 0)) != vertex_count):
                raise ValueError('Material %r has no complete UV0 set.' % name)
            infos = [material.get('normalTexture'), material.get('occlusionTexture'),
                     material.get('emissiveTexture'),
                     (material.get('pbrMetallicRoughness') or {}).get('baseColorTexture'),
                     (material.get('pbrMetallicRoughness') or {}).get('metallicRoughnessTexture')]
            bound = [info for info in infos if isinstance(info, dict)
                     and 'index' in info]
            if bound:
                textured += 1
                if 'TEXCOORD_0' not in attributes:
                    raise ValueError('Textured material %r lacks UV0 coordinates.' % name)
            for role in bound:
                set_index = int(role.get('texCoord', 0))
                attribute = attributes.get('TEXCOORD_%d' % set_index)
                if (not isinstance(attribute, int)
                        or not 0 <= attribute < len(accessors)
                        or accessors[attribute].get('type') != 'VEC2'
                        or int(accessors[attribute].get('count', 0)) != vertex_count):
                    raise ValueError('Material %r has an incomplete UV set %d.'
                                     % (name, set_index))
            uv = attributes.get('TEXCOORD_0')
            if uv is not None and (
                    not isinstance(uv, int)
                    or not 0 <= uv < len(accessors)
                    or int(accessors[uv].get('count', 0)) != vertex_count):
                raise ValueError('Material %r has a partial UV0 stream.' % name)
    if not primitives:
        raise ValueError('No drawable triangle material groups in the GLB.')
    return {'material_groups': counts, 'triangle_primitives': primitives,
            'textured_primitives_verified': textured, 'uv_policy': 'complete per material'}


def embedded_action_names(blob):
    """Return verified embedded animation names, or empty if absent/invalid."""
    doc, _ = _unpack_glb(blob)
    tracks = doc.get('animations') or ()
    if not tracks or len(doc.get('skins') or ()) != 1:
        return ()
    names = tuple(str(clip.get('name') or '') for clip in tracks)
    return names if all(names) and len(set(names)) == len(names) else ()
