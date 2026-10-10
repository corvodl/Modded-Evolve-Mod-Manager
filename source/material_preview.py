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
    linked = related_asset_index(workspace, projects_root, stage_root)
    if linked is not None:
        return linked
    # Build an index from the workspace manifest; avoid scanning arbitrary files.
    index = {}
    root = (workspace / 'files').resolve()
    for entry in load_workspace(workspace)['entries']:
        rel = normalized_name(entry['path'])
        path = (root / rel).resolve()
        if path.is_relative_to(root) and path.is_file() and not path.is_symlink():
            index.setdefault(rel.casefold(), []).append({'file': path, 'workspace': workspace, 'relative': rel})
    return index


def _find_unique(index, virtual, *, preferred_workspace=None):
    hits, status = _resolve(index, virtual, preferred_workspace=preferred_workspace)
    if status == 'ambiguous':
        raise ValueError('Different versions of this asset exist in multiple unpacked PAKs: ' + virtual + '. Select its owning PAK or remove outdated duplicate projects.')
    return hits[0] if status == 'found' else None


def _external_lookup(folder, ref):
    """Match a flat, explicitly selected directory, never recursively guess."""
    if not folder:
        return None
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError('Selected texture folder does not exist.')
    path = Path(ref.replace('\\', '/'))
    stem = path.name
    if stem.casefold().endswith('.tif'):
        choices = (stem[:-4] + '.dds.0', stem[:-4] + '.dds')
    else:
        choices = (stem + '.0', stem)
    hits = [item for item in folder.iterdir() if item.is_file() and not item.is_symlink()
            and item.name.casefold() in {n.casefold() for n in choices}]
    if len(hits) > 1:
        raise ValueError('Multiple candidates for ' + ref + ' in selected texture folder.')
    return {'file': hits[0], 'workspace': None, 'relative': hits[0].name} if hits else None


def _decode_texture(item):
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
            if 0 not in parts:
                raise ValueError('Missing DDS .0 header.')
            first = path.read_bytes()
            info = parse_dds(first)
            if info.mipmaps == 1 and len(parts) == 1:
                raw = first
            else:
                if len(parts) < 2 or set(parts) != set(range(len(parts))):
                    raise ValueError('Texture folder is missing DDS streaming parts.')
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
        tex = im.convert('RGB')
    tex.thumbnail((MAX_PREVIEW_TEX_DIM, MAX_PREVIEW_TEX_DIM * 2), Image.Resampling.LANCZOS)
    return tex


def load_preview_materials(workspace, model_relative, texture_folder=None, *, projects_root=None, stage_root=None):
    index = _workspace_index(workspace, projects_root, stage_root)
    stem = Path(model_relative).with_suffix('').as_posix()
    bases = [stem, re.sub(r'_lod\d+$', '', stem, flags=re.IGNORECASE)]
    material = next((item for b in bases if (item := _find_unique(index, b+'.mtl', preferred_workspace=workspace)) is not None), None)
    if material is None:
        raise ValueError('Model .mtl not found. Unpack the material PAK alongside this model.')
    raw = material['file'].read_bytes()
    if len(raw) > MAX_MATERIAL_BYTES or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Material XML is too large or contains disallowed entities.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError('Cannot parse model material XML.') from error
    subs = list(root.findall('./SubMaterials/Material'))
    if not subs:
        subs = [root]
    result=[]; names=[]
    for item in subs:
        name = item.get('Name') or 'unnamed'
        names.append(name)
        diffuse = next((child.get('File') or child.get('file') for child in item.findall('./Textures/Texture')
                        if (child.get('Map') or child.get('map') or '').casefold() == 'diffuse'), None)
        if not diffuse or item.get('Shader','').casefold()=='nodraw':
            result.append(None)
            continue
        found = _find_unique(index, diffuse, preferred_workspace=material['workspace'])
        if found is None:
            found = _external_lookup(texture_folder, diffuse)
        if found is None:
            result.append(None)
            continue
        result.append(_decode_texture(found))
    if not any(x is not None for x in result):
        raise ValueError('No diffuse DDS textures found. Unpack the texture PAKs, or choose a folder containing the extracted DDS fragments.')
    return PreviewMaterials(tuple(result),tuple(names),material['relative'])


def render_textured_mesh(mesh, materials, width=720, height=480, yaw=-.5, elevation=.24, zoom=1., *, cancel=None):
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
    sx=width*.5 + (offset[:,0]*ca-offset[:,1]*sa)*scale
    sy=height*.5 + (-offset[:,2]*ce+(offset[:,0]*sa+offset[:,1]*ca)*se)*scale
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
