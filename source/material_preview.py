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
import math
from collections import OrderedDict
import hashlib
import re
import threading
from xml.etree import ElementTree as ET

from PIL import Image

from dds_streaming import inspect_stream, inspect_whole_part0
from dds_texture import parse_dds
from multi_pak_assets import related_asset_index, _resolve, load_workspace, normalized_name

MAX_MATERIAL_BYTES = 5 * 1024 * 1024
MAX_TEXTURE_BYTES = 64 * 1024 * 1024
MAX_PREVIEW_TEX_DIM = 16384  # no silent highest-mip downscale; memory limits remain explicit
MAX_MATERIAL_DDS_BYTES = 512 * 1024 * 1024
MAX_MATERIAL_PIXELS = 100_000_000
_texture_pixels = OrderedDict()
_texture_lock = threading.RLock()
MAX_CACHED_TEXTURE_BYTES = 128 * 1024 * 1024


def clear_texture_cache():
    with _texture_lock:
        _texture_pixels.clear()


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

    uv_transforms: tuple = ()
    diffuse_sources: tuple = ()  # exact resolved (virtual path, PAK), per material slot
    diffuse_references: tuple = ()  # original MTL File attr, even if missing
    uv_available: tuple = ()  # source render mesh has decoded UVs for this slot
    alpha_cutoffs: tuple = ()  # Explicit CryEngine AlphaTest thresholds

    hidden: tuple = ()  # Explicit NoDraw slots; never inferred from missing DDS.
    gloss_maps: tuple = ()  # Explicit Gloss/Glossiness/Smoothness slots only.
    detail_maps: tuple = ()  # Optional, separately mapped detail-color layer.
    detail_normal_maps: tuple = ()  # Optional, separately mapped detail normal.
    map_uv_transforms: tuple = ()  # (role, per-material UV-transform tuples)
    uv_motions: tuple = ()  # per-material supported constant (du,dv,drotate) motions
    map_uv_motions: tuple = ()  # (role, per-material motions)
    environment_maps: tuple = ()  # six-face PIL tuples, or None per material
    reflection_strengths: tuple = ()
    glass_materials: tuple = ()
    refraction_indices: tuple = ()  # explicit source IOR only
    ao_maps: tuple = ()  # explicit ambient-occlusion map, or verified packed channel
    ao_strengths: tuple = ()  # per-material AO strength in [0, 1]
    emissive_maps: tuple = ()
    emissive_strengths: tuple = ()
    emissive_sources: tuple = ()

    @property
    def count(self):
        return sum(tex is not None for tex in self.textures)

    @cached_property
    def alpha_arrays(self):
        import numpy as np
        return tuple(None if im is None else np.asarray(im.convert('RGBA').getchannel('A'), dtype=np.uint8)
                     for im in self.textures)

    @cached_property
    def rgb_arrays(self):
        """Decode Pillow textures to NumPy just once per material selection."""
        import numpy as np
        return tuple(None if im is None else np.asarray(im.convert('RGB'), dtype=np.uint8)
                     for im in self.textures)


def material_color(image, value):
    """Bake linear MTL diffuse tint into sRGB RGB, preserving texture alpha."""
    import numpy as np
    try:
        tint = np.asarray([float(v) for v in value.split(',')], dtype=np.float64)
        if tint.shape != (3,) or not np.isfinite(tint).all():
            return image
        tint = np.clip(tint, 0., 1.)
    except (ValueError, TypeError):
        return image
    if np.all(tint == 1):
        return image
    rgba = np.asarray(image.convert('RGBA')).copy()
    rgb = rgba[:, :, :3] / 255.
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    linear *= tint
    rgb = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1/2.4) - .055)
    rgba[:, :, :3] = np.rint(np.clip(rgb, 0, 1) * 255).astype('uint8')
    return Image.fromarray(rgba)


class RenderCancelled(Exception):
    """Normal cancellation of an obsolete background preview render."""


def _workspace_index(workspace, projects_root=None, stage_root=None):
    workspace = Path(workspace).resolve()
    # Imported PAK folders contain asset-library.json, not a project
    # workspace manifest. Their game-relative paths must still resolve
    # against every imported shared/data/texture archive.
    if (workspace / 'asset-library.json').is_file():
        from asset_library import _archive_identifier
        from asset_library_preview import archive_folder, library_model_index
        identifier = _archive_identifier(workspace)
        if (identifier is not None and
                archive_folder(identifier, root=workspace.parent) == workspace):
            return library_model_index(identifier, root=workspace.parent)
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
    # An older extracted project's manifest may contain an empty-name,
    # zero-byte archive placeholder. Never use it as an asset path. Keep
    # load_workspace strict for rebuilding PAKs; sanitize this READ-ONLY
    # texture/model lookup only, rejecting every other unsafe entry.
    try:
        entries = load_workspace(workspace)['entries']
    except ValueError as exc:
        if "Unsafe archive entry name: ''" not in str(exc):
            raise
        from evolve_pak_workspace import safe_index_entries
        entries = safe_index_entries(workspace)
    for entry in entries:
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


def _historical_2012_character(index, workspace, relative):
    """Gate Xenos transforms on an actual CryTek 0x745 model in this build."""
    import struct
    from pathlib import PurePosixPath
    name = str(relative).replace('\\', '/')
    if name.casefold().endswith('.cdf'):
        name = str(PurePosixPath(name).with_suffix('.chr'))
    if not name.casefold().endswith('.chr'):
        return False
    hits, status = _resolve(index, name, preferred_workspace=workspace)
    if status != 'found':
        return False
    candidate = Path(hits[0]['file'])
    if not candidate.is_file() or candidate.is_symlink():
        return False
    with candidate.open('rb') as handle:
        header = handle.read(16)
    return (len(header) == 16 and header[:8] == b'CryTek\0\0'
            and struct.unpack_from('<I', header, 12)[0] == 0x745)


def _decode_texture(item, *, index=None, max_dimension=MAX_PREVIEW_TEX_DIM,
                    normal_map=False, historical_2012=False):
    path = item['file']
    if path.stat().st_size > MAX_MATERIAL_DDS_BYTES:
        raise ValueError('DDS fragment exceeds the 512 MiB safety limit.')
    relative = item['relative']
    if relative.casefold().endswith('.dds.0'):
        # Imported source PAKs commonly hold only .0 while higher-resolution
        # .1/.2 mip payloads reside in a separate signed textures PAK.
        # Resolve exact virtual stream names across the complete index before
        # falling back to a self-contained workspace stream.
        indexed_siblings = bool(index and any(
            relative[:-2].casefold()+'.'+str(n) in index
            for n in range(1,64)))
        if item['workspace'] is not None and not indexed_siblings:
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
            parts[0] = path
            if 0 not in parts:
                raise ValueError('Missing DDS .0 header.')
            first = path.read_bytes()
            info = parse_dds(first)
            if len(parts) == 1:
                # Validate complete mip chains, including .0 files with >1 mip.
                from dds_streaming import _mip_sizes
                if info.mipmaps > 1:
                    expected = info.data_offset + sum(_mip_sizes(info, first))
                    if len(first) != expected:
                        raise ValueError('Incomplete standalone DDS mip chain.')
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
    return _decode_texture_pixels(raw, relative, max_dimension=max_dimension,
                                  normal_map=normal_map, historical_2012=historical_2012)


def _decode_texture_pixels(raw, relative, *, max_dimension=MAX_PREVIEW_TEX_DIM,
                           normal_map=False, historical_2012=False):
    # Key the assembled bytes, not a filename: edited files, stream fragments,
    # builds and preview/export quality must never reuse stale pixels.
    key = (hashlib.sha256(raw).digest(), relative if normal_map else None,
           max_dimension, normal_map, historical_2012)
    with _texture_lock:
        cached = _texture_pixels.get(key)
        if cached is not None:
            _texture_pixels.move_to_end(key)
            return cached.copy()
    info = parse_dds(raw)
    if len(raw) > MAX_MATERIAL_DDS_BYTES or info.width * info.height > MAX_MATERIAL_PIXELS:
        raise ValueError('DDS exceeds the 512 MiB / 100 megapixel decoding safety budget.')
    if max_dimension >= MAX_PREVIEW_TEX_DIM and max(info.width, info.height) > MAX_PREVIEW_TEX_DIM:
        raise ValueError('DDS top mip exceeds the supported full-resolution dimension; refusing silent downscale.')
    # CryEngine normal maps can be BC5 XY or DXT5nm alpha/green, not
    # directly usable tangent-space RGB. The same normalized pixels feed the
    # Qt shader and GLB exporter; never rescale a lower mip as a substitute.
    from xbox360_dds import is_2012_console_dds, decode_2012_console_dds
    if historical_2012 and is_2012_console_dds(raw):
        tex = decode_2012_console_dds(raw, normal_map=normal_map)
    elif normal_map:
        from normal_map_utils import decode_normal_dds_image
        tex = decode_normal_dds_image(raw, source_name=relative)
    else:
        with Image.open(BytesIO(raw)) as im:
            if im.format != 'DDS':
                raise ValueError('Unsupported texture encoding for model preview.')
            im.load()
            tex = im.convert('RGBA') if 'A' in im.getbands() else im.convert('RGB')
    # DDS alpha may be auxiliary material data. Pillow RGBA resampling
    # premultiplies it into RGB, destroying opaque colors wherever alpha is 0.
    # Preserve independent channels; the material decides how alpha is used.
    alpha = tex.getchannel('A') if tex.mode == 'RGBA' else None
    tex = tex.convert('RGB')
    tex.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    if alpha is not None:
        tex.putalpha(alpha.resize(tex.size, Image.Resampling.LANCZOS))
    with _texture_lock:
        _texture_pixels[key] = tex.copy()
        _texture_pixels.move_to_end(key)
        while (len(_texture_pixels) > 128 or
               sum(im.width * im.height * 4
                   for im in _texture_pixels.values()) > MAX_CACHED_TEXTURE_BYTES):
            _texture_pixels.popitem(last=False)
    return tex


def load_preview_materials(workspace, model_relative, texture_folder=None, *, projects_root=None, stage_root=None, index=None, cdf=None, material_override=None, texture_quality="highest", cancel=None):
    def checkpoint():
        if cancel is not None and cancel():
            raise InterruptedError('Texture/material decoding cancelled')
    checkpoint()
    if texture_quality not in ("highest","balanced"):
        raise ValueError("Unsupported texture quality setting.")
    decode_max = MAX_PREVIEW_TEX_DIM if texture_quality == "highest" else 1024
    if index is None:
        index = _workspace_index(workspace, projects_root, stage_root)
    checkpoint()
    historical_2012 = _historical_2012_character(
        index, workspace, model_relative)
    from model_material_resolve import find_model_material
    if material_override:
        from multi_pak_assets import _resolve
        from model_material_resolve import _material_path
        exact = _material_path(material_override, '')
        if not exact:
            raise ValueError('Invalid explicit level material override.')
        hits, state = _resolve(index, exact, preferred_workspace=workspace)
        if state != 'found':
            raise ValueError('Explicit level material could not be uniquely resolved: '
                             + exact + ' (' + state + ')')
        material, reason, attempted = hits[0], 'level instance Material override', (exact,)
    else:
        material, reason, attempted = find_model_material(
            index, model_relative, preferred_workspace=workspace, cdf=cdf)
    if material is None:
        raise ValueError('No verified model .mtl association for ' +
                         model_relative + '; searched: ' +
                         ', '.join(attempted[:6]) +
                         '. Check CDF material overrides or embedded material references.')
    checkpoint()
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
        """Recover a cooked texture only from the material's own character.

        Historical Evolve materials often start with Objects/characters/.
        Never search globally for a same-named DDS from a different actor.
        """
        from multi_pak_assets import _candidates, _content_fingerprint
        cooked = [p.casefold() for p in _candidates(ref)
                  if p.casefold().endswith(('.dds', '.dds.0'))]
        parts = str(material['relative']).replace('\\', '/').casefold().split('/')
        if len(parts) >= 4 and parts[:2] == ['objects', 'characters']:
            # objects/characters/monsters/parasitehost/*.mtl and comparable.
            scope = '/'.join(parts[:4]) + '/' if parts[2] in (
                'monsters', 'hunters', 'creatures') and len(parts) >= 5 \
                else '/'.join(parts[:3]) + '/'
        elif len(parts) >= 4 and parts[0] == 'characters' and parts[1] in (
                'monsters', 'hunters', 'creatures'):
            scope = '/'.join(parts[:3]) + '/'
        else:
            # Never borrow a global basename when ownership is unknown.
            return None
        for wanted in sorted({Path(x).name for x in cooked},
                             key=lambda x: (not x.endswith('.dds.0'), x)):
            hits = {}
            for virtual, records in index.items():
                if (not virtual.casefold().startswith(scope)
                        or Path(virtual).name.casefold() != wanted):
                    continue
                for record in records:
                    if '_shared_archive' in record:
                        from shared_pak_cache import materialize_shared_record
                        record = materialize_shared_record(record)
                    hits[str(Path(record['file']).resolve())] = record
                    if len(hits) > 32:
                        return None
            if not hits:
                continue
            if len(hits) > 1:
                try:
                    if len({_content_fingerprint(hit['file'])
                            for hit in hits.values()}) != 1:
                        return None
                except OSError:
                    return None
            return next(iter(hits.values()))
        return None

    def find_texture(ref):
        """Resolve cooked DDS, prioritizing the complete streaming source.

        A material's .tif is a *source reference*, not a DDS image. An
        extracted TIFF entry cannot supersede a verified cooked .dds/.dds.0.
        """
        from multi_pak_assets import resolve_material_texture, _candidates
        cooked = [name for name in _candidates(ref)
                  if name.casefold().endswith(('.dds', '.dds.0'))]
        cooked.sort(key=lambda name: not name.casefold().endswith('.dds.0'))
        for virtual in dict.fromkeys(cooked):
            candidates, state = resolve_material_texture(
                index, virtual, material['relative'],
                preferred_workspace=material['workspace'])
            if state == 'ambiguous':
                raise ValueError(
                    'Different versions of this material texture exist: ' + str(virtual))
            if state == 'unsafe-path':
                raise ValueError('Unsafe material texture path: ' + str(virtual))
            if state == 'found':
                return candidates[0]
        hit = _external_lookup(texture_folder, ref)
        return hit if hit is not None else recover_unique_dds(ref)

    def optional_map(item, names):
        node = next((child for child in item.findall('./Textures/Texture')
                     if (child.get('Map') or child.get('map') or '').casefold() in names), None)
        channel = node.get('Channel', '') if node is not None else ''
        # The gloss channel of a *named* specular texture may be packed.
        # Only an explicit GlossChannel declaration authorizes extracting it.
        if node is None and 'gloss' in names:
            node = next((child for child in item.findall('./Textures/Texture')
                         if (child.get('Map') or child.get('map') or '').casefold()
                         in ('specular','specularcolor') and
                         child.get('GlossChannel')), None)
            channel = node.get('GlossChannel', '') if node is not None else ''
        if node is None and 'ao' in names:
            # An explicitly declared AOChannel on any bound texture is
            # evidence of packed occlusion; alpha elsewhere is NOT assumed
            # to be occlusion.
            node = next((child for child in item.findall('./Textures/Texture')
                         if child.get('AOChannel') or child.get('OcclusionChannel')), None)
            if node is not None:
                channel = node.get('AOChannel') or node.get('OcclusionChannel')
        ref = (node.get('File') or node.get('file')) if node is not None else None
        normal = 'normal' in names or 'detailnormal' in names
        if not ref and normal:
            # Some extracted materials omit the normal slot but ship a
            # conventional _ddn source in the same character/Asset Library.
            # Resolve only via the verified material-relative index; never
            # select an arbitrary matching basename from another actor.
            diffuse_ref = next((child.get('File') or child.get('file')
                for child in item.findall('./Textures/Texture')
                if (child.get('Map') or child.get('map') or '').casefold() == 'diffuse'), None)
            if diffuse_ref:
                candidate = re.sub(r'(?i)(?:_diffuse|_diff|_dif|_d)(?=\.(?:dds|tif|tiff|png)(?:\.\d+)?$)',
                                   '_ddn', diffuse_ref)
                if candidate == diffuse_ref:
                    candidate = re.sub(r'(?i)(?=\.(?:dds|tif|tiff|png)(?:\.\d+)?$)',
                                       '_ddn', diffuse_ref)
                if candidate != diffuse_ref:
                    ref = candidate
        if not ref:
            return None
        hit = find_texture(ref)
        if hit is None:
            if 'ao' in names:
                raise ValueError('Explicit AO texture was not resolved: ' + str(ref))
            return None
        decoded = _decode_texture(hit, index=index, max_dimension=decode_max,
                                  normal_map=normal, historical_2012=historical_2012)
        if channel:
            component = channel.upper()
            if component not in ('R', 'G', 'B', 'A'):
                raise ValueError('Unsupported explicit ' +
                                 ('AO ' if 'ao' in names else '') +
                                 'packed-channel selector: ' + channel)
            if component not in decoded.getbands():
                raise ValueError('Declared packed channel is absent from the source DDS: ' + channel)
            decoded = Image.merge('RGB', (decoded.getchannel(component),) * 3)
        return decoded

    result=[]; names=[]; issues=[]; normals=[]; speculars=[]; glosses=[]; details=[]; detail_normals=[]; ao_maps=[]; ao_strengths=[]; environments=[]; reflections=[]; glasses=[]; iors=[]; opacities=[]; alpha_modes=[]; cutoffs=[]
    transforms = []; motions = []; sources = []; references = []; hidden = []
    role_transforms = {role: [] for role in ('normal','specular','gloss','detail','detail_normal','ao')}
    role_motions = {role: [] for role in role_transforms}
    for item in subs:
        checkpoint()
        name = item.get('Name') or 'unnamed'
        names.append(name)
        from texture_transform import parse_transform, parse_motion, IDENTITY
        diffuse_node = next((t for t in item.findall('./Textures/Texture')
                             if t.get('Map', '').casefold() == 'diffuse'), None)
        try:
            transform, dynamic = parse_transform(diffuse_node)
            if dynamic:
                issues.append(name + ': animated texture modifiers shown at their static transform')
        except ValueError as error:
            transform = IDENTITY
            issues.append(name + ': ' + str(error))
        transforms.append(transform)
        try:
            motions.append(parse_motion(diffuse_node))
        except ValueError as error:
            motions.append((0.,0.,0.))
            issues.append(name + ': ' + str(error))
        role_keys = {
            'normal': ('bumpmap','normal','normalmap'),
            'specular': ('specular','specularcolor'),
            'gloss': ('gloss','glossiness','smoothness'),
            'detail': ('detail','detaildiffuse','detailmap'),
            'detail_normal': ('detailnormal','detailnormalmap','detailbumpmap'),
            'ao': ('ao','occlusion','occlusionmap','ambientocclusion','ambientocclusionmap'),
        }
        for role, keys in role_keys.items():
            texture_node = next((n for n in item.findall('./Textures/Texture')
                                if (n.get('Map') or n.get('map') or '').casefold() in keys), None)
            if role == 'ao' and texture_node is None:
                # A packed AO channel inherits its actual source texture's
                # TexMod rather than erroneously sampling diffuse UVs.
                texture_node = next((n for n in item.findall('./Textures/Texture')
                                     if n.get('AOChannel') or n.get('OcclusionChannel')), None)
            try:
                slot_transform, animated = parse_transform(texture_node)
                if animated:
                    issues.append(name + ': animated ' + role + ' texture modifiers require verification')
            except ValueError as error:
                slot_transform = IDENTITY
                issues.append(name + ': ' + str(error))
            role_transforms[role].append(slot_transform)
            try:
                role_motions[role].append(parse_motion(texture_node))
            except ValueError as error:
                role_motions[role].append((0.,0.,0.))
                issues.append(name + ': ' + str(error))
        try:
            opacity = max(0.,min(1.,float(item.get('Opacity','1'))))
        except ValueError:
            opacity = 1.
        opacities.append(opacity)
        raw_ao_strength = (item.get('AOStrength') or item.get('OcclusionStrength')
                           or item.get('AmbientOcclusionStrength') or '1')
        try:
            ao_strength = float(raw_ao_strength)
            if not math.isfinite(ao_strength) or not 0. <= ao_strength <= 1.:
                raise ValueError('AO strength out of [0, 1] range')
        except ValueError:
            issues.append(name + ': invalid AO strength, using 1.0')
            ao_strength = 1.
        ao_strengths.append(ao_strength)
        shader = item.get('Shader', '').casefold()
        hidden.append(shader == 'nodraw')
        glasses.append('glass' in shader or 'refract' in shader)
        ior = item.get('IOR') or item.get('RefractionIndex') or ''
        try:
            ior = float(ior) if ior else None
            if ior is not None and not 1.0 <= ior <= 3.0:
                raise ValueError('out-of-range IOR')
        except ValueError:
            issues.append(name + ': invalid explicit glass refraction index')
            ior = None
        iors.append(ior)
        reflection_node = next((tex for tex in item.findall('./Textures/Texture')
                                if (tex.get('Map') or tex.get('map') or '').casefold()
                                in ('environment','cubemap','reflection')), None)
        environment = None
        if reflection_node is not None:
            env_ref = reflection_node.get('File') or reflection_node.get('file')
            if env_ref:
                try:
                    from cubemap_material import decode_cubemap_dds, assemble_cubemap_stream
                    found = find_texture(env_ref)
                    if found is not None:
                        source = Path(found['file'])
                        if source.is_symlink() or source.stat().st_size > MAX_MATERIAL_DDS_BYTES:
                            raise ValueError('Unsafe or oversized cubemap source')
                        raw_cube = source.read_bytes()
                        if str(found['relative']).casefold().endswith('.dds.0'):
                            stem = str(found['relative'])[:-2]
                            companions = {}
                            for number in range(1,32):
                                match = _find_unique(index, stem+'.'+str(number))
                                if match is None:
                                    break
                                child = Path(match['file'])
                                if child.is_symlink() or child.stat().st_size > MAX_MATERIAL_DDS_BYTES:
                                    raise ValueError('Unsafe cubemap stream part')
                                companions[number] = child.read_bytes()
                            if companions:
                                raw_cube = assemble_cubemap_stream(raw_cube, companions)
                        environment = decode_cubemap_dds(raw_cube)
                except (ValueError,OSError) as error:
                    issues.append(name + ': cubemap unavailable: ' + str(error))
        environments.append(environment)
        try:
            intensity = float(item.get('ReflectionStrength','1'))
            if not math.isfinite(intensity):
                raise ValueError('non-finite intensity')
            intensity = max(0.,min(1.,intensity))
        except ValueError:
            issues.append(name + ': invalid reflection strength')
            intensity = 1.
        reflections.append(intensity if environment else 0.)
        # CryEngine diffuse DDS alpha is frequently a mask or auxiliary data,
        # not an alpha-blend instruction. Do NOT hide opaque armor/body meshes.
        try:
            alpha_test = max(0., min(1., float(item.get('AlphaTest', '0'))))
        except ValueError:
            alpha_test = False
        cutoffs.append(float(alpha_test))
        explicitly_packed_opacity = bool(diffuse_node is not None and diffuse_node.get('OpacityChannel'))
        alpha_modes.append(bool(alpha_test) or opacity < 0.999 or explicitly_packed_opacity or
                           any(word in shader for word in ('glass', 'transparent', 'translucent', 'decal')))
        for target,keys in ((normals,('bumpmap','normal','normalmap')),
                            (speculars,('specular','specularcolor')),
                            (glosses,('gloss','glossiness','smoothness')),
                            (details,('detail','detaildiffuse','detailmap')),
                            (detail_normals,('detailnormal','detailnormalmap','detailbumpmap')),
                            (ao_maps,('ao','occlusion','occlusionmap','ambientocclusion','ambientocclusionmap'))):
            try:
                target.append(optional_map(item,keys))
            except (OSError,ValueError) as error:
                target.append(None)
                issues.append(name+': optional material map not loaded: '+str(error))
        diffuse = next((child.get('File') or child.get('file') for child in item.findall('./Textures/Texture')
                        if (child.get('Map') or child.get('map') or '').casefold() == 'diffuse'), None)
        references.append(diffuse or '')
        if not diffuse or item.get('Shader','').casefold()=='nodraw':
            if shader == 'glass' and not diffuse:
                result.append(material_color(Image.new('RGBA', (1, 1), 'white'), item.get('Diffuse', '1,1,1')))
                sources.append(None)
                continue
            if not diffuse and item.get('Shader','').casefold() != 'nodraw':
                issues.append(name + ': material has no diffuse map; '
                              'rendering this slot without a texture')
            result.append(None)
            sources.append(None)
            continue
        try:
            found = find_texture(diffuse)
            if found is None:
                from multi_pak_assets import _candidates
                cooked = tuple(p for p in _candidates(diffuse)
                               if p.casefold().endswith(('.dds', '.dds.0')))
                issues.append(
                    name + ': DDS missing for ' + diffuse
                    + ('; expected cooked ' + ', '.join(cooked[:2]) if cooked else ''))
                result.append(None)
                sources.append(None)
                continue
            checkpoint()
            image = _decode_texture(
                found, index=index, max_dimension=decode_max,
                historical_2012=historical_2012)
            checkpoint()
            if explicitly_packed_opacity:
                channel = diffuse_node.get('OpacityChannel').upper()
                if channel not in ('R','G','B','A') or channel not in image.getbands():
                    raise ValueError('Explicit diffuse OpacityChannel is absent or unsupported: ' + channel)
                image = image.convert('RGBA')
                image.putalpha(image.getchannel(channel))
            result.append(material_color(image, item.get('Diffuse', '1,1,1')))
            sources.append((
                str(found.get('relative') or diffuse),
                str(found.get('archive') or 'extracted workspace')))
        except (ValueError, OSError) as error:
            issues.append(name + ': ' + str(error))
            result.append(None)
            sources.append(None)
    visible_diffuse = tuple(ref for ref, is_hidden in zip(references, hidden)
                            if ref and not is_hidden)
    if visible_diffuse and not any(tex is not None for tex, is_hidden in
                                   zip(result, hidden) if not is_hidden):
        # Old CryTek MTLs often have several NoDraw skeleton slots preceding
        # the real body material. They previously suppressed this error and
        # silently produced a black/untextured GPU preview.
        detail = '; '.join(issues[:3]) if issues else ', '.join(visible_diffuse[:3])
        raise ValueError('No diffuse DDS textures found or decoded for visible material slots: ' + detail +
                         '. Import the owning Legacy texture PAK for this build, '
                         'then use Textures > Diagnose Texture Links.')
    if not any(x is not None for x in result) and not any(hidden):
        detail = issues[0] if issues else 'Material defines no supported diffuse textures.'
        raise ValueError('No diffuse DDS textures found or decoded: ' + detail +
                         ' (see Textures > Diagnose Texture Links).')
    from emissive_materials import emission_settings, bake_emission_map
    emissive_maps, emissive_strengths, emissive_sources = [], [], []
    for slot, (item, diffuse_img, name) in enumerate(zip(subs, result, names)):
        amount, color, reference = emission_settings(item)
        glow, provenance = None, None
        if amount > 0 and not hidden[slot]:
            emission_img = None
            if reference:
                try:
                    found = find_texture(reference)
                    if found is None:
                        raise ValueError('Emissive DDS missing: ' + reference)
                    emission_img = _decode_texture(found, index=index,
                        max_dimension=decode_max, historical_2012=historical_2012)
                    provenance = (str(found.get('relative') or reference),
                                  str(found.get('archive') or 'extracted workspace'))
                except (ValueError, OSError) as error:
                    issues.append(name + ': ' + str(error))
                    amount = 0.
            if amount > 0:
                glow = bake_emission_map(diffuse_img, color=color,
                    emission=emission_img,
                    diffuse_alpha_mask=(emission_img is None and not alpha_modes[slot]))
                if glow is None:
                    issues.append(name + ': emissive texture missing')
                    amount = 0.
        emissive_maps.append(glow)
        emissive_strengths.append(amount)
        emissive_sources.append(provenance)
    return PreviewMaterials(tuple(result),tuple(names),material['relative'],
                            tuple(issues),tuple(normals),tuple(speculars),tuple(opacities),tuple(alpha_modes),
                            tuple(transforms),tuple(sources),tuple(references),alpha_cutoffs=tuple(cutoffs), hidden=tuple(hidden), gloss_maps=tuple(glosses),
                            detail_maps=tuple(details), detail_normal_maps=tuple(detail_normals),
                            map_uv_transforms=tuple((role,tuple(matrices)) for role,matrices in role_transforms.items()),
                            uv_motions=tuple(motions),
                            map_uv_motions=tuple((role,tuple(mats)) for role,mats in role_motions.items()),
                            environment_maps=tuple(environments),
                            reflection_strengths=tuple(reflections),
                            glass_materials=tuple(glasses),
                            refraction_indices=tuple(iors),
                            ao_maps=tuple(ao_maps), ao_strengths=tuple(ao_strengths),
                             emissive_maps=tuple(emissive_maps),
                             emissive_strengths=tuple(emissive_strengths),
                             emissive_sources=tuple(emissive_sources))



def diffuse_source_stats(materials, slot):
    """Character texture source brightness, independent of any 3D lighting.

    Decoded DDS success does not establish correct UV mapping or that a
    submaterial is visually bright. Sample a bounded thumbnail, not the
    potentially enormous original. Returns None if this slot has no DDS.
    """
    if materials is None or not (0 <= slot < len(materials.textures)):
        return None
    image = materials.textures[slot]
    if image is None:
        return None
    from PIL import Image
    sample = image.convert('RGB')
    sample.thumbnail((32, 32), Image.Resampling.BILINEAR)
    pixels = list(sample.getdata())
    if not pixels:
        return None
    average = sum((.2126*r+.7152*g+.0722*b) for r,g,b in pixels)/len(pixels)
    near_black = sum(max(r,g,b) < 32 for r,g,b in pixels) / len(pixels)
    return round(average, 1), round(near_black*100)



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
        texno=int(mapping[index])
        if texno < len(materials.hidden) and materials.hidden[texno]:
            continue
        tex=textures[texno] if texno<len(textures) else None
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
        from texture_transform import transform_for, transform_uv
        u, v = transform_uv(u, v, transform_for(materials, texno))
        # Wrap UVs; DDS image data uses top-left row origin.
        tx=np.mod((u*(tex.shape[1]-1)).astype(np.int64),tex.shape[1])
        ty=np.mod((v*(tex.shape[0]-1)).astype(np.int64),tex.shape[0])
        vertices=verts[face]
        normal=np.cross(vertices[1]-vertices[0],vertices[2]-vertices[0])
        norm=np.linalg.norm(normal)
        lighting=.50+.50*abs(float(np.dot(normal,light)/norm)) if norm>1e-12 else .65
        color=np.clip(tex[ty,tx].astype(np.float32)*lighting,0,255).astype(np.uint8)
        dest=buffer[y0:y1,x0:x1]
        opacity = materials.opacity[texno] if texno < len(materials.opacity) else 1.
        cutoff = materials.alpha_cutoffs[texno] if texno < len(materials.alpha_cutoffs) else 0.
        uses_alpha = texno < len(materials.alpha_modes) and materials.alpha_modes[texno]
        alpha = materials.alpha_arrays[texno][ty,tx] / 255. * opacity if uses_alpha else np.full(tx.shape, opacity)
        inside &= alpha >= (cutoff if cutoff > 0 else .02)
        if cutoff <= 0 and (uses_alpha or opacity < .999):
            color = (color * alpha[...,None] + dest * (1-alpha[...,None])).astype(np.uint8)
        dest[inside]=color[inside]
        depth[inside]=zs[inside]
    if cancel is not None and cancel():
        raise RenderCancelled('No longer needed')
    image=Image.fromarray(buffer,'RGB')
    from PIL import ImageDraw
    ImageDraw.Draw(image).text((12,height-26),f'Approximate diffuse UV preview | {mesh.description}',fill='#cad2df')
    return image
