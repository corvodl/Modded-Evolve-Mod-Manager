"""Assemble a whole character preview from its CryEngine .cdf skin bindings.

Render-only, never changes the source archive. Monster/creature skin parts and
hunter body accessories stay assembled, with one selected equipment skin.
Drawing every equipment attachment at the same time
would superimpose mutually exclusive weapons.  Bone-attached rigid geometry
requires its socket transforms and is not guessed by this path.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from model_preview import PreviewMesh, read_preview_mesh, MAX_VERTICES, MAX_TRIANGLES
from material_preview import PreviewMaterials, load_preview_materials
from multi_pak_assets import normalized_name, _resolve


@dataclass(frozen=True)
class CharacterPreview:
    mesh: PreviewMesh
    materials: PreviewMaterials | None
    cdf: str
    skeleton: str
    included: tuple
    omitted: tuple
    equipment_options: tuple = ()
    selected_equipment: str = ''
    rig_parts: tuple = ()


def _parse_cdf(raw):
    if len(raw) > 2 * 1024 * 1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Character definition is too large or contains forbidden XML.')
    root = ET.fromstring(raw)
    model = root.find('./Model')
    if model is None or not model.get('File'):
        raise ValueError('CDF has no skeleton model.')
    skeleton = normalized_name(model.get('File'))
    refs = []
    for row in root.findall('./AttachmentList/Attachment'):
        if row.get('Type', '').casefold() not in ('ca_skin', 'ca_skin_unique') or not row.get('Binding'):
            continue
        try:
            binding = normalized_name(row.get('Binding'))
        except ValueError:
            continue
        # Some retail Legacy CDFs use a CA_SKIN binding to a renderable
        # .chr rather than to .skin. Do not manufacture a .skinm name.
        if binding.casefold().endswith(('.skin', '.chr')):
            refs.append((row.get('AName', '') or Path(binding).stem, binding))
    sets = {}
    for group in root.findall('./SkinSets/SkinSet'):
        key = re.sub(r'[^a-z0-9]', '', (group.get('name') or '').casefold())
        if key:
            sets[key] = frozenset(
                re.sub(r'[^a-z0-9]', '', (row.get('name') or '').casefold())
                for row in group.findall('./skin'))
    return skeleton, tuple(refs), sets


def _resolve_file(index, virtual, workspace):
    hits, status = _resolve(index, virtual, preferred_workspace=workspace)
    return hits[0]['file'] if status == 'found' else None


def _character_definition(selected, index, workspace):
    virtual = normalized_name(selected)
    parent = Path(virtual).parent
    stem = Path(virtual).stem
    root_name = re.sub(r'_skeleton$', '', re.sub(r'_lod\d+$', '', stem, flags=re.I), flags=re.I)
    expected = (parent / (root_name + '.cdf')).as_posix().casefold()
    # A skeleton or main body skin can resolve directly. Equipment skins need
    # their owner's CDF; consider only same-directory definitions.
    candidates = [expected] + sorted(k for k in index if
        k.startswith(parent.as_posix().casefold() + '/') and
        k.endswith('.cdf') and k != expected)
    selected_skin = virtual.casefold()
    if selected_skin.endswith('.skinm'):
        selected_skin = selected_skin[:-1]
    for candidate in candidates[:32]:
        file = _resolve_file(index, candidate, workspace)
        if file is None:
            continue
        try:
            skeleton, skins, skin_sets = _parse_cdf(Path(file).read_bytes())
        except (ValueError, OSError, ET.ParseError):
            continue
        if (candidate == expected or selected_skin == skeleton.casefold()
                or selected_skin in (skin.casefold() for _, skin in skins)
                or selected_skin == skeleton.casefold() + 'm'):
            return candidate, skeleton, skins, skin_sets
    return None


def combine_meshes(items):
    """Combine meshes and material-slot tables without losing per-part textures."""
    if not items:
        raise ValueError('No compatible character parts to combine.')
    vertices, triangles, uvs, subsets = [], [], [], []
    textures, names, issues = [], [], []
    normals, speculars, glosses, opacities, alpha_modes = [], [], [], [], []
    transforms, sources, references, uv_available = [], [], [], []
    cutoffs = []; hidden = []
    # All verified emissive per-submaterial settings must survive assembly
    # remapping; otherwise Stage GLBs silently lose the glowing Kala/Goliath
    # material slots even though the standalone texture loader decoded them.
    emissive_maps, emissive_strengths, emissive_sources, emissive_black, emissive_masks = [], [], [], [], []
    source_slot_ids, source_mtls, emission_refs, emission_statuses = [], [], [], []
    uv_roles = ('normal','specular','gloss','detail','detail_normal','ao','emissive')
    role_transforms = {role: [] for role in uv_roles}
    role_motions = {role: [] for role in uv_roles}
    included = []
    for name, mesh, materials in items:
        if len(vertices) + len(mesh.vertices) > MAX_VERTICES or len(triangles) + len(mesh.triangles) > MAX_TRIANGLES:
            raise ValueError('Combined character exceeds preview vertex/triangle limits.')
        base_vertex = len(vertices)
        base_index = len(triangles) * 3
        source_subsets = mesh.subsets or ((0, len(mesh.triangles) * 3, 0),)
        # Source skin subset IDs can include the sentinel 255 for missing
        # material assignment; remap ONLY referenced slots to dense composite IDs.
        slots = sorted({slot for _, _, slot in source_subsets})
        if len(textures) + len(slots) > 256:
            raise ValueError('Combined character exceeds 256 GPU material slots.')
        has_uv = len(mesh.uvs) == len(mesh.vertices)
        if not has_uv:
            issues.append(
                name + ': render mesh has no decoded UV stream; source textures '
                'cannot be mapped, using untextured shading for this part')
        remap = {}
        for slot in slots:
            remap[slot] = len(textures)
            # Source subset IDs are the authority. Never use the composite
            # position to index the original MTL table (e.g. 11 -> 7 for Kala).
            source_slot_ids.append(
                materials.original_slot_ids[slot]
                if materials and slot < len(materials.original_slot_ids)
                else slot)
            source_mtls.append(
                materials.original_materials[slot]
                if materials and slot < len(materials.original_materials)
                else materials.source if materials else '')
            emission_refs.append(
                materials.emissive_references[slot]
                if materials and slot < len(materials.emissive_references) else '')
            hidden.append(bool(materials and slot < len(materials.hidden) and materials.hidden[slot]))
            tex = (materials.textures[slot] if has_uv and materials and
                   slot < len(materials.textures) else None)
            uv_available.append(has_uv)
            label = (materials.names[slot] if materials and
                     slot < len(materials.names) else 'unresolved slot ' + str(slot))
            textures.append(tex)
            source = (materials.diffuse_sources[slot] if materials and
                      slot < len(materials.diffuse_sources) else None)
            reference = (materials.diffuse_references[slot] if materials and
                         slot < len(materials.diffuse_references) else '')
            sources.append(source)
            references.append(reference)
            from texture_transform import transform_for
            transforms.append(transform_for(materials, slot))
            for role in uv_roles:
                role_transforms[role].append(transform_for(materials,slot,role))
                motions = next((values for name,values in
                    getattr(materials,'map_uv_motions',()) if name==role),())
                role_motions[role].append(motions[slot] if slot<len(motions) else (0.,0.,0.))
            # Preserve per-part material settings when combining CDF skins.
            # Dropping alpha/material mode made opaque character body geometry
            # appear transparent when its diffuse DDS carried auxiliary alpha.
            normals.append(materials.normal_maps[slot] if has_uv and materials and
                           slot < len(materials.normal_maps) else None)
            speculars.append(materials.specular_maps[slot] if has_uv and materials and
                             slot < len(materials.specular_maps) else None)
            # Split CryEngine DDNA alpha gloss belongs to this exact source
            # MTL leaf, not an arbitrary adjacent CDF composite slot.
            glosses.append(materials.gloss_maps[slot] if has_uv and materials and
                           slot < len(materials.gloss_maps) else None)
            opacities.append(materials.opacity[slot] if materials and
                             slot < len(materials.opacity) else 1.0)
            alpha_modes.append(materials.alpha_modes[slot] if has_uv and materials and
                               slot < len(materials.alpha_modes) else False)
            cutoffs.append(materials.alpha_cutoffs[slot] if materials and
                           slot < len(materials.alpha_cutoffs) else 0.)
            emissive_maps.append(materials.emissive_maps[slot] if has_uv and materials
                                 and slot < len(materials.emissive_maps) else None)
            emissive_strengths.append(materials.emissive_strengths[slot] if materials
                                      and slot < len(materials.emissive_strengths) else 0.)
            emissive_sources.append(materials.emissive_sources[slot] if materials
                                    and slot < len(materials.emissive_sources) else None)
            emissive_black.append(bool(materials.emissive_black[slot]) if materials
                                  and slot < len(getattr(materials, 'emissive_black', ()))
                                  else False)
            emissive_masks.append(materials.emissive_masks[slot] if materials
                                  and slot<len(getattr(materials,'emissive_masks',()))
                                  else 'none')
            emission_statuses.append(
                ('source mesh lacks UV coordinates; emission cannot be sampled'
                 if not has_uv else materials.emissive_status[slot])
                if materials and slot < len(materials.emissive_status)
                else 'no resolved source MTL material at slot %d' % slot)
            names.append(name + ': ' + label)
        vertices.extend(mesh.vertices)
        triangles.extend(tuple(v + base_vertex for v in tri) for tri in mesh.triangles)
        uvs.extend(mesh.uvs if len(mesh.uvs) == len(mesh.vertices)
                   else ((0., 0.),) * len(mesh.vertices))
        subsets.extend((base_index + first, count, remap[slot])
                       for first, count, slot in source_subsets)
        if materials:
            issues.extend(name + ': ' + issue for issue in materials.issues)
        included.append(name)
    xs, ys, zs = zip(*vertices)
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
              (min(zs) + max(zs)) / 2)
    radius = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)) / 2
    mesh = PreviewMesh(tuple(vertices), tuple(triangles), center, radius,
                       'Character assembly: ' + ', '.join(included),
                       tuple(uvs), tuple(subsets))
    material = PreviewMaterials(tuple(textures), tuple(names),
                                'CDF character assembly', tuple(issues),
                                tuple(normals), tuple(speculars),
                                tuple(opacities), tuple(alpha_modes), tuple(transforms),
                                tuple(sources), tuple(references), tuple(uv_available), tuple(cutoffs),
                                hidden=tuple(hidden), gloss_maps=tuple(glosses),
                                emissive_maps=tuple(emissive_maps),
                                emissive_strengths=tuple(emissive_strengths),
                                emissive_sources=tuple(emissive_sources),
                                emissive_black=tuple(emissive_black),
                                emissive_masks=tuple(emissive_masks),
                                original_slot_ids=tuple(source_slot_ids),
                                original_materials=tuple(source_mtls),
                                emissive_references=tuple(emission_refs),
                                emissive_status=tuple(emission_statuses),
                                map_uv_transforms=tuple((role,tuple(values))
                                    for role,values in role_transforms.items()),
                                map_uv_motions=tuple((role,tuple(values))
                                    for role,values in role_motions.items()))
    return mesh, material


MAX_CDF_RENDER_PARTS = 24


def _hunter_anatomical_skin(label, binding, cdf):
    """Only always include explicitly named body limbs, not gun attachments.

    E.g. an arm supplied as a separate CA_SKIN should not disappear when a
    weapon is selected. Names such as armgun, armory or weapon_armature are
    *not* treated as body parts. This reads the CDF; no mesh is invented.
    """
    limb_names = frozenset({
        'arm', 'leftarm', 'rightarm', 'larm', 'rarm',
        'upperarm', 'forearm', 'leftforearm', 'rightforearm',
        'hand', 'lefthand', 'righthand', 'lhand', 'rhand',
    })
    clean = lambda text: re.sub(r'[^a-z0-9]', '', text.casefold())
    name = clean(label)
    if name in limb_names:
        return True
    # Attachments may have a character name prefix, e.g. Emet_RightArm.
    own_tokens = {clean(p) for part in Path(cdf).parts[-2:]
                  for p in re.split(r'[^a-zA-Z0-9]+', part)
                  if p and len(p) >= 3}
    if any(name == prefix + limb for prefix in own_tokens for limb in limb_names):
        return True
    stem = Path(binding).stem.casefold()
    tokens = tuple(t for t in re.split(r'[^a-z0-9]+', stem) if t)
    return any(token in limb_names for token in tokens)


def assemble_character(workspace, selected, index, *, texture_folder=None,
                       projects_root=None, stage_root=None, equipment=None,
                       cancel=None):
    """Return None when no owning CDF or no renderable bound skin exists.

    Only select the character body and explicitly selected weapon/skin. Extra
    equipment can be added through a future attachment choice UI.
    """
    if cancel is not None and cancel():
        raise InterruptedError('Character assembly cancelled')
    definition = _character_definition(selected, index, workspace)
    if definition is None:
        return None
    cdf, skeleton, skins, skin_sets = definition
    selected_skin = normalized_name(selected).casefold()
    if selected_skin.endswith('.skinm'):
        selected_skin = selected_skin[:-1]
    # Cockpit is a permanent character mesh (Lennox's head/face/eyes),
    # not a mutually exclusive weapon. Its submaterial table is distinct
    # from the body and must be included even when a rifle is equipped.
    primary = [(label, rel) for label, rel in skins
               if label.casefold() in
               ('body', 'hands', 'character', 'base', 'main', 'cockpit')]
    if not primary and skins:
        # CDF labels are arbitrary (e.g. maggiebody). Prefer the owning CDF's
        # matching main skin before falling back to attachment order.
        main_skin = str(Path(cdf).with_suffix('.skin')).replace('\\', '/').casefold()
        primary = [(label, rel) for label, rel in skins if rel.casefold() == main_skin]
        if not primary:
            primary = [skins[0]]
    # Monster/creature attachments are body parts, not mutually exclusive guns.
    if normalized_name(selected).split('/')[1].casefold() in ('monsters', 'creatures'):
        primary = sorted(skins, key=lambda item: item[0].casefold() not in ('body', 'character', 'base', 'main'))
    else:
        # Hunter body accessories (mask, scarf, flashlight, etc.) stay visible
        # when a weapon changes; only separate equipment is selectable.
        body_stems = [Path(rel).stem.casefold() + '_' for _, rel in primary]
        primary.extend((label, rel) for label, rel in skins
                       if (label, rel) not in primary and
                       (any(Path(rel).stem.casefold().startswith(stem)
                            for stem in body_stems)
                        or _hunter_anatomical_skin(label, rel, cdf)))
    options = [(label, rel) for label, rel in skins if (label, rel) not in primary]
    if equipment == 'body':
        chosen = []
    elif equipment:
        chosen = [(label, rel) for label, rel in options if rel.casefold() == equipment.casefold()]
    else:
        selected_matches = [(label, rel) for label, rel in options
                            if rel.casefold() == selected_skin]
        # The first CDF skin after the body is the character's default
        # equipped appearance. Draw exactly ONE, never every overlapping gun.
        chosen = selected_matches or options[:1]
    requested = primary + chosen
    # Respect CDF SkinSets. Kala's Rift Device and its arm are a set,
    # while other weapon skins should be hidden.
    if chosen:
        clean = lambda name: re.sub(r'[^a-z0-9]', '', name.casefold())
        selected_name = clean(chosen[0][0])
        excluded = skin_sets.get('hiddenwhen' + selected_name + 'selected')
        if excluded is not None:
            requested.extend(
                (label, rel) for label, rel in options
                if (label, rel) not in chosen and clean(label) not in excluded)
    requested = list(dict.fromkeys(requested))
    if not requested:
        return None
    included, omitted, parts, rig_parts = [], [], [], []
    if len(requested) > MAX_CDF_RENDER_PARTS:
        omitted.extend(label + ': CDF part limit (' +
                       str(MAX_CDF_RENDER_PARTS) + ')' for label, _ in
                       requested[MAX_CDF_RENDER_PARTS:])
    for label, skin in requested[:MAX_CDF_RENDER_PARTS]:
        if cancel is not None and cancel():
            raise InterruptedError('Character assembly cancelled')
        render = skin if skin.casefold().endswith('.chr') else skin + 'm'
        path = _resolve_file(index, render, workspace)
        if path is None:
            omitted.append(label + ': missing ' + render)
            continue
        try:
            mesh = read_preview_mesh(Path(path).read_bytes(), render)
        except (ValueError, OSError) as exc:
            omitted.append(label + ': ' + str(exc))
            continue
        try:
            materials = load_preview_materials(
                workspace, render, texture_folder, projects_root=projects_root,
                stage_root=stage_root, index=index, cdf=cdf,
                **({'cancel': cancel} if cancel is not None else {}))
        except (ValueError, OSError) as exc:
            materials = None
            omitted.append(label + ' textures: ' + str(exc))
        parts.append((label, mesh, materials))
        skin_path = (_resolve_file(index, skin, workspace)
                     if skin.casefold().endswith('.skin') else None)
        # Legacy .chr body geometry must not be misrepresented as .skin
        # rigging; animated GLB export still requires separate validation.
        rig_parts.append((render, Path(path), Path(skin_path) if skin_path else None,
                          len(mesh.vertices)))
        included.append(label)
    if not parts:
        return None
    if cancel is not None and cancel():
        raise InterruptedError('Character assembly cancelled')
    mesh, materials = combine_meshes(parts)
    if omitted and materials is not None:
        from dataclasses import replace
        # Render failures must show which actual CDF part/MTL/texture failed,
        # even if the assembled mesh itself remains visible.
        materials = replace(
            materials, issues=tuple(materials.issues) + tuple(omitted))
    return CharacterPreview(mesh, materials, cdf, skeleton,
                            tuple(included), tuple(omitted),
                            tuple(options), chosen[0][1] if chosen else 'body', tuple(rig_parts))
