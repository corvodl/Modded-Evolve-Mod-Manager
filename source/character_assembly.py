"""Assemble a whole character preview from its CryEngine .cdf skin bindings.

Render-only, never changes the source archive.  Body + one selected equipment
skin is intentional: drawing every CDF equipment attachment at the same time
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
        if row.get('Type', '').casefold() != 'ca_skin' or not row.get('Binding'):
            continue
        try:
            binding = normalized_name(row.get('Binding'))
        except ValueError:
            continue
        if binding.casefold().endswith('.skin'):
            refs.append((row.get('AName', '') or Path(binding).stem, binding))
    return skeleton, tuple(refs)


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
            skeleton, skins = _parse_cdf(Path(file).read_bytes())
        except (ValueError, OSError, ET.ParseError):
            continue
        if (candidate == expected or selected_skin == skeleton.casefold()
                or selected_skin in (skin.casefold() for _, skin in skins)
                or selected_skin == skeleton.casefold() + 'm'):
            return candidate, skeleton, skins
    return None


def combine_meshes(items):
    """Combine meshes and material-slot tables without losing per-part textures."""
    if not items:
        raise ValueError('No compatible character parts to combine.')
    vertices, triangles, uvs, subsets = [], [], [], []
    textures, names, issues = [], [], []
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
        remap = {}
        for slot in slots:
            remap[slot] = len(textures)
            tex = (materials.textures[slot] if materials and
                   slot < len(materials.textures) else None)
            label = (materials.names[slot] if materials and
                     slot < len(materials.names) else 'unresolved slot ' + str(slot))
            textures.append(tex)
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
                                'CDF character assembly', tuple(issues))
    return mesh, material


def assemble_character(workspace, selected, index, *, texture_folder=None,
                       projects_root=None, stage_root=None):
    """Return None when no owning CDF exists or fewer than two skins resolve.

    Only select the character body and explicitly selected weapon/skin. Extra
    equipment can be added through a future attachment choice UI.
    """
    definition = _character_definition(selected, index, workspace)
    if definition is None:
        return None
    cdf, skeleton, skins = definition
    selected_skin = normalized_name(selected).casefold()
    if selected_skin.endswith('.skinm'):
        selected_skin = selected_skin[:-1]
    primary = [(label, rel) for label, rel in skins
               if label.casefold() in ('body', 'hands', 'character', 'base', 'main')]
    if not primary and skins:
        primary = [skins[0]]
    selected_equipment = [(label, rel) for label, rel in skins
                          if rel.casefold() == selected_skin and (label, rel) not in primary]
    requested = primary + selected_equipment
    if not requested:
        return None
    included, omitted, parts = [], [], []
    for label, skin in requested[:8]:
        render = skin + 'm'
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
                stage_root=stage_root, index=index)
        except (ValueError, OSError) as exc:
            materials = None
            omitted.append(label + ' textures: ' + str(exc))
        parts.append((label, mesh, materials))
        included.append(label)
    if not parts:
        return None
    mesh, materials = combine_meshes(parts)
    return CharacterPreview(mesh, materials, cdf, skeleton,
                            tuple(included), tuple(omitted))
