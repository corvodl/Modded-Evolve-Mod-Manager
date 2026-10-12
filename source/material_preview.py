"""Read-only approximate CryEngine UV/diffuse material preview.

File resolution is restricted to verified extracted PAK workspaces, or a user-
selected loose texture folder. No game files are written; ambiguous assets and
unsupported DDS layouts fail visibly instead of being guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from PIL import Image

from dds_streaming import inspect_stream, inspect_whole_part0
from dds_texture import parse_dds
from multi_pak_assets import related_asset_index, _resolve, load_workspace, normalized_name

MAX_MATERIAL_BYTES = 5 * 1024 * 1024
MAX_TEXTURE_BYTES = 64 * 1024 * 1024
MAX_PREVIEW_TEX_DIM = 768


@dataclass(frozen=True)
class PreviewMaterials:
    textures: tuple  # one optional PIL RGB image per CryEngine submaterial
    names: tuple
    source: str
    issues: tuple = ()
    normal_maps: tuple = ()
    specular_maps: tuple = ()
    opacity: tuple = ()
    alpha_modes: tuple = ()  # True only where material explicitly uses texture alpha

    @property
    def count(self):
        return sum(tex is not None for tex in self.textures)

    @cached_property
    def rgb_arrays(self):
        """Decode Pillow textures to NumPy just once per material selection."""
        import numpy as np
        return tuple(None if im is None else np.asarray(im.convert('RGB'), dtype=np.uint8)
                     for im in self.textures)


class RenderCancelled(Exception):
    """Normal cancellation of an obsolete background preview render."""


def _workspace_index(workspace, projects_root=None, stage_root=None):
    workspace = Path(workspace).resolve()
    # Imported PAK folders contain asset-library.json, not a project
    # workspace manifest. Their game-relative paths must still resolve
    # against every imported shared/data/texture archive.
    if (workspace / 'asset-library.json').is_file():
        from asset_library_preview import archive_folder, library_model_index
        if archive_folder(workspace.name, root=workspace.parent) == workspace:
            return library_model_index(workspace.name, root=workspace.parent)
    linked = related_asset_index(workspace, projects_root, stage_root)
    if linked is not None:
        # Persistent imports remain visible even when a multi-PAK project
        # or stage workspace supplied the initial lookup index.
        from asset_library import extracted_index
        merged = {key: list(hits) for key, hits in linked.items()}
        for name, records in extracted_index().items():
            known = {str(hit.get('file')) for hit in merged.get(name, ())}
            merged.setdefault(name, []).extend(
                hit for hit in records if str(hit.get('file')) not in known)
        return merged
    # Build an index from the workspace manifest; avoid scanning arbitrary files.
    index = {}
    root = (workspace / 'files').resolve()
    for entry in load_workspace(workspace)['entries']:
        rel = normalized_name(entry['path'])
        path = (root / rel).resolve()
        if path.is_relative_to(root) and path.is_file() and not path.is_symlink():
            index.setdefault(rel.casefold(), []).append(
                {'file': path, 'workspace': workspace, 'relative': rel})
    # If a manually imported/extracted character isn't associated with this
    # signing stage, its own files still have first priority. The permanent
    # extracted Asset Library must *also* be visible, otherwise all nine PAKs
    # can be present and Kala still reports missing shared DDS files.
    from asset_library import extracted_index
    for name, records in extracted_index().items():
        index.setdefault(name, []).extend(records)
    return index


def _find_unique(index, virtual, *, preferred_workspace=None):
    hits, status = _resolve(index, virtual, preferred_workspace=preferred_workspace)
    if status == 'ambiguous':
        raise ValueError('Different versions of this asset exist in multiple unpacked PAKs: ' + virtual + '. Select its owning PAK or remove outdated duplicate projects.')
    return hits[0] if status == 'found' else None


def _external_lookup(folder, ref):
    """Locate one DDS within an explicitly selected extracted texture tree.

    Exact virtual paths win. A basename-only match is safe only when unique.
    No writes or symlink traversal; bounded to avoid freezing the editor.
    """
    if not folder:
        return None
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError('Selected texture folder does not exist.')
    from multi_pak_assets import _candidates
    choices = [c.casefold() for c in _candidates(ref) if c.casefold().endswith(('.dds', '.dds.0'))]
    if not choices:
        return None
    base_names = {Path(c).name for c in choices}
    exact, by_name = [], []
    visited = 0
    for item in folder.rglob('*'):
        visited += 1
        if visited > 15000:
            raise ValueError('Selected texture folder contains too many paths. '
                             'Select a narrower extracted texture folder.')
        if not item.is_file() or item.is_symlink():
            continue
        relative = item.relative_to(folder).as_posix().casefold()
        if relative in choices:
            exact.append(item)
        elif item.name.casefold() in base_names:
            by_name.append(item)
    hits = exact if exact else by_name
    if len(hits) > 1:
        raise ValueError('Multiple DDS matches for ' + ref +
                         ' in the selected texture folder. Choose a narrower folder.')
    if hits:
        return {'file': hits[0], 'workspace': None, 'relative': hits[0].name}
    return None


def _decode_texture(item, *, index=None):
    path = item['file']
    if path.stat().st_size > MAX_TEXTURE_BYTES:
        raise ValueError('Texture fragment exceeds the 64 MiB preview limit.')
    relative = item['relative']
    if relative.casefold().endswith('.dds.0'):
        if item['workspace'] is not None:
            try:
                raw = inspect_whole_part0(item['workspace'], relative)
            except ValueError:
                raw = inspect_stream(item['workspace'], relative).merged
        else:
            # Loose external folder may be flat; its .dds.N siblings must be present.
            # Assemble loose streaming fragments in memory; no symlinks or writes.
            from dds_streaming import _mip_sizes
            base = path.name[:-2]
            parts = {}
            for child in path.parent.iterdir():
                match = re.fullmatch(re.escape(base) + r'\.(\d+)', child.name, re.IGNORECASE)
                if match and child.is_file() and not child.is_symlink():
                    parts[int(match.group(1))] = child
            # The Asset Library stores each signed PAK under its own import
            # root. A streamed DDS can have its .0 in a character PAK and
            # high-resolution .1/.2 parts in a different textures PAK.
            # Resolve exact VIRTUAL paths across all indexed PAKs, never
            # guess by basename or concatenate conflicting payloads.
            if index:
                stem = relative[:-2]
                for number in range(1, 64):
                    virtual = stem + '.' + str(number)
                    hit = _find_unique(index, virtual)
                    if hit is None:
                        if number not in parts:
                            break
                        continue
                    candidate = Path(hit['file'])
                    if not candidate.is_symlink() and candidate.is_file():
                        parts[number] = candidate
            if 0 not in parts:
                raise ValueError('Missing DDS .0 header.')
            first = path.read_bytes()
            info = parse_dds(first)
            if info.mipmaps == 1 and len(parts) == 1:
                raw = first
            else:
                if len(parts) < 2 or set(parts) != set(range(len(parts))):
                    raise ValueError('Texture folder is missing DDS streaming parts for ' +
                                     relative + ' across imported PAKs; found fragments ' +
                                     ', '.join(str(i) for i in sorted(parts)))
                sizes = _mip_sizes(info, first)
                n_tail = [n for n in range(1, info.mipmaps) if sum(sizes[-n:]) == len(first)-info.data_offset]
                if len(n_tail) != 1 or len(parts) != info.mipmaps-n_tail[0]+1:
                    raise ValueError('Unsupported or incomplete DDS mip stream.')
                pieces = []
                for i in range(1, len(parts)):
                    piece = parts[i].read_bytes()
                    if len(piece) != sizes[-n_tail[0]-i]:
                        raise ValueError('DDS fragment has unexpected size.')
                    pieces.append(piece)
                raw = first[:info.data_offset] + b''.join(reversed(pieces)) + first[info.data_offset:]
    else:
        raw = path.read_bytes()
    info = parse_dds(raw)
    if len(raw) > MAX_TEXTURE_BYTES or info.width * info.height > 16_777_216:
        raise ValueError('DDS texture exceeds preview size limits.')
    with Image.open(BytesIO(raw)) as im:
        if im.format != 'DDS':
            raise ValueError('Unsupported texture encoding for model preview.')
        im.load()
        tex = im.convert('RGBA') if 'A' in im.getbands() else im.convert('RGB')
    tex.thumbnail((MAX_PREVIEW_TEX_DIM, MAX_PREVIEW_TEX_DIM * 2), Image.Resampling.LANCZOS)
    return tex


def load_preview_materials(workspace, model_relative, texture_folder=None, *, projects_root=None, stage_root=None, index=None):
    if index is None:
        index = _workspace_index(workspace, projects_root, stage_root)
    from model_material_resolve import find_model_material
    material, reason, attempted = find_model_material(
        index, model_relative, preferred_workspace=workspace)
    if material is None:
        raise ValueError('No verified model .mtl association for ' +
                         model_relative + '; searched: ' +
                         ', '.join(attempted[:6]) +
                         '. Check CDF material overrides or embedded material references.')
    raw = material['file'].read_bytes()
    if len(raw) > MAX_MATERIAL_BYTES or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Material XML is too large or contains disallowed entities.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError('Cannot parse model material XML.') from error
    # Nested multi-materials occur in character equipment. Flatten leaves
    # in source order so submesh IDs and texture slots remain aligned.
    def leaves(item):
        children = item.findall('./SubMaterials/Material')
        if not children:
            yield item
        else:
            for child in children:
                yield from leaves(child)

    subs = list(leaves(root))
    # Broken virtual prefixes occur in older character materials. Resolve only
    # when exactly one verified matching DDS basename exists across all
    # extracted PAKs. Different files with the same name are never guessed.
    def recover_unique_dds(ref):
        from multi_pak_assets import _candidates
        basenames = {Path(item).name.casefold() for item in _candidates(ref)
                     if item.casefold().endswith(('.dds','.dds.0'))}
        hits = {}
        for path, records in index.items():
            if Path(path).name.casefold() not in basenames:
                continue
            for record in records:
                # A signed shared archive is indexed lazily, and is not a
                # loose path until a unique basename was verified.
                if '_shared_archive' in record:
                    from shared_pak_cache import materialize_shared_record
                    record = materialize_shared_record(record)
                hits[str(Path(record['file']).resolve())] = record
                if len(hits) > 32:
                    return None
        if not hits:
            return None
        # A shared TS PAK, custom signed stage and user's imported game PAK
        # may all contain the SAME DDS at different physical locations.
        # Deduplicate by bytes, not absolute filenames. Never choose between
        # different DDS payloads based on an ambiguous basename.
        if len(hits) > 1:
            from multi_pak_assets import _content_fingerprint
            try:
                if len({_content_fingerprint(hit['file']) for hit in hits.values()}) != 1:
                    return None
            except OSError:
                return None
        return next(iter(hits.values()))

    def find_texture(ref):
        """Try exact game path, then material-relative refs, without guessing."""
        hit = _find_unique(index, ref, preferred_workspace=material['workspace'])
        if hit is not None:
            return hit
        # Material texture names are often relative to the CHARACTER,
        # not the .mtl directory. E.g. teleporter.mtl may say
        # 'teleporter_diff.tif' while the TS archive owns
        # characters/monsters/teleporter/textures/teleporter_diff.dds.0.
        # Resolve only exact virtual paths rooted in the material's own
        # character hierarchy; never guess a texture from a different actor.
        normalized_ref = str(ref).strip().replace('\\', '/')
        if (normalized_ref and not normalized_ref.startswith('/')
                and ':' not in normalized_ref and
                all(part not in ('.', '..', '') for part in normalized_ref.split('/'))):
            ancestors = Path(material['relative']).parent.parts
            # Limit fallback resolution to this model's character directory.
            # Other archives retain their existing explicit-path lookup.
            minimum = (3 if len(ancestors) >= 3
                       and ancestors[0].casefold() == 'characters'
                       and ancestors[1].casefold() in ('hunters', 'monsters', 'creatures')
                       else 1)
            # Fully qualified virtual paths were already checked above.
            rooted = normalized_ref.casefold().startswith(
                ('characters/', 'objects/', 'materials/', 'levels/'))
            if not rooted:
                for depth in range(len(ancestors), minimum - 1, -1):
                    parent = '/'.join(ancestors[:depth])
                    candidates = [parent + '/' + normalized_ref]
                    if '/' not in normalized_ref:
                        candidates.append(parent + '/textures/' + normalized_ref)
                    for candidate in candidates:
                        hit = _find_unique(
                            index, candidate,
                            preferred_workspace=material['workspace'])
                        if hit is not None:
                            return hit
        hit = _external_lookup(texture_folder, ref)
        return hit if hit is not None else recover_unique_dds(ref)

    def optional_map(item, names):
        ref = next((child.get('File') or child.get('file')
                    for child in item.findall('./Textures/Texture')
                    if (child.get('Map') or child.get('map') or '').casefold() in names),None)
        if not ref:
            return None
        hit = find_texture(ref)
        if hit is None:
            return None
        return _decode_texture(hit, index=index)

    result=[]; names=[]; issues=[]; normals=[]; speculars=[]; opacities=[]; alpha_modes=[]
    for item in subs:
        name = item.get('Name') or 'unnamed'
        names.append(name)
        try:
            opacity = max(0.,min(1.,float(item.get('Opacity','1'))))
        except ValueError:
            opacity = 1.
        opacities.append(opacity)
        shader = item.get('Shader', '').casefold()
        # CryEngine diffuse DDS alpha is frequently a mask or auxiliary data,
        # not an alpha-blend instruction. Do NOT hide opaque armor/body meshes.
        try:
            alpha_test = float(item.get('AlphaTest', '0')) > 0
        except ValueError:
            alpha_test = False
        alpha_modes.append(alpha_test or opacity < 0.999 or
                           any(word in shader for word in ('glass', 'transparent', 'translucent', 'decal')))
        for target,keys in ((normals,('bumpmap','normal','normalmap')),
                            (speculars,('specular','specularcolor','gloss'))):
            try:
                target.append(optional_map(item,keys))
            except (OSError,ValueError) as error:
                target.append(None)
                issues.append(name+': optional material map not loaded: '+str(error))
        diffuse = next((child.get('File') or child.get('file') for child in item.findall('./Textures/Texture')
                        if (child.get('Map') or child.get('map') or '').casefold() == 'diffuse'), None)
        if not diffuse or item.get('Shader','').casefold()=='nodraw':
            result.append(None)
            continue
        try:
            found = find_texture(diffuse)
            if found is None:
                issues.append(name + ': DDS missing for ' + diffuse)
                result.append(None)
                continue
            result.append(_decode_texture(found, index=index))
        except (ValueError, OSError) as error:
            issues.append(name + ': ' + str(error))
            result.append(None)
    if not any(x is not None for x in result):
        detail = issues[0] if issues else 'Material defines no supported diffuse textures.'
        raise ValueError('No diffuse DDS textures found or decoded: ' + detail +
                         ' (see Textures > Diagnose Texture Links).')
    return PreviewMaterials(tuple(result),tuple(names),material['relative'],
                            tuple(issues),tuple(normals),tuple(speculars),tuple(opacities),tuple(alpha_modes))


def render_textured_mesh(mesh, materials, width=720, height=480, yaw=-.5, elevation=.24, zoom=1., *, pan=(0.,0.), cancel=None):
    """Depth-tested diffuse/UV CPU rasterizer. This is NOT CryEngine shader parity."""
    import math
    import numpy as np
    if cancel is not None and cancel():
        raise RenderCancelled('No longer needed')
    width=max(128,min(int(width),1400));height=max(128,min(int(height),1100))
    zoom=max(.2,min(float(zoom),6.0))
    if not mesh.uvs or len(mesh.uvs)!=len(mesh.vertices):
        raise ValueError('Mesh has no supported UV coordinates.')
    if not materials or materials.count==0:
        raise ValueError('No resolved diffuse material textures.')
    verts=np.asarray(mesh.vertices,dtype=np.float32)
    uv=np.asarray(mesh.uvs,dtype=np.float32)
    tris=np.asarray(mesh.triangles,dtype=np.int32)
    if len(tris) > 500_000:
        raise ValueError('Too many triangles for a CPU material preview.')
    center=np.asarray(mesh.center,dtype=np.float32)
    offset=verts-center
    ca,sa=math.cos(yaw),math.sin(yaw)
    ce,se=math.cos(elevation),math.sin(elevation)
    scale=min(width,height)*.42 / mesh.radius * zoom
    sx=width*.5 + float(pan[0]) + (offset[:,0]*ca-offset[:,1]*sa)*scale
    sy=height*.5 + float(pan[1]) + (-offset[:,2]*ce+(offset[:,0]*sa+offset[:,1]*ca)*se)*scale
    dz=(offset[:,0]*sa+offset[:,1]*ca)*ce+offset[:,2]*se
    screen=np.column_stack((sx,sy))
    buffer=np.full((height,width,3),(32,33,42),dtype=np.uint8)
    zbuf=np.full((height,width),-np.inf,dtype=np.float32)
    textures=materials.rgb_arrays
    default_material=next((i for i,tex in enumerate(textures) if tex is not None),0)
    mapping=np.full(len(tris),default_material,dtype=np.int16)
    for first,count,material_index in mesh.subsets:
        if 0 <= material_index < len(textures):
            mapping[first//3:(first+count)//3]=material_index
    # Painter-like simple Lambert factor, with real depth testing and UV sampling.
    light=np.asarray((.4,-.5,.77),dtype=np.float32)
    light/=np.linalg.norm(light)
    for index,face in enumerate(tris):
        if cancel is not None and index % 32 == 0 and cancel():
            raise RenderCancelled('No longer needed')
        texno=int(mapping[index]);tex=textures[texno] if texno<len(textures) else None
        if tex is None:
            continue
        p=screen[face]
        x0=max(0,int(math.floor(float(np.min(p[:,0])))))
        y0=max(0,int(math.floor(float(np.min(p[:,1])))))
        x1=min(width,int(math.ceil(float(np.max(p[:,0]))))+1)
        y1=min(height,int(math.ceil(float(np.max(p[:,1]))))+1)
        if x1<=x0 or y1<=y0:continue
        # Very large clipped triangles are uncommon on this model; cap defensive cost.
        if (x1-x0)*(y1-y0)>width*height*.70:
            continue
        (ax,ay),(bx,by),(cx,cy)=p
        denom=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy)
        if abs(float(denom))<.25:continue
        gridy,gridx=np.mgrid[y0:y1,x0:x1]
        gridx=gridx+.5;gridy=gridy+.5
        a=((by-cy)*(gridx-cx)+(cx-bx)*(gridy-cy))/denom
        b=((cy-ay)*(gridx-cx)+(ax-cx)*(gridy-cy))/denom
        c=1.-a-b
        inside=(a>=-0.001)&(b>=-0.001)&(c>=-0.001)
        if not np.any(inside):continue
        zs=a*dz[face[0]]+b*dz[face[1]]+c*dz[face[2]]
        depth=zbuf[y0:y1,x0:x1]
        inside &= zs > depth
        if not np.any(inside):continue
        localuv=uv[face]
        u=a*localuv[0,0]+b*localuv[1,0]+c*localuv[2,0]
        v=a*localuv[0,1]+b*localuv[1,1]+c*localuv[2,1]
        # Wrap UVs; DDS image data uses top-left row origin.
        tx=np.mod((u*(tex.shape[1]-1)).astype(np.int64),tex.shape[1])
        ty=np.mod((v*(tex.shape[0]-1)).astype(np.int64),tex.shape[0])
        vertices=verts[face]
        normal=np.cross(vertices[1]-vertices[0],vertices[2]-vertices[0])
        norm=np.linalg.norm(normal)
        lighting=.50+.50*abs(float(np.dot(normal,light)/norm)) if norm>1e-12 else .65
        color=np.clip(tex[ty,tx].astype(np.float32)*lighting,0,255).astype(np.uint8)
        dest=buffer[y0:y1,x0:x1]
        dest[inside]=color[inside]
        depth[inside]=zs[inside]
    if cancel is not None and cancel():
        raise RenderCancelled('No longer needed')
    image=Image.fromarray(buffer,'RGB')
    from PIL import ImageDraw
    ImageDraw.Draw(image).text((12,height-26),f'Approximate diffuse UV preview | {mesh.description}',fill='#cad2df')
    return image
