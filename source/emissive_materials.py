"""CryEngine 3 emissive/glow material parsing and read-only texture baking.

Legacy Illum/Cloth/HumanSkin glow uses GlowAmount and the *diffuse alpha*
as an emission mask; it is NOT an alpha-blend instruction. Illum can also
use a Decal texture for colored glow. Later materials use Emittance maps.
Only explicit material emission parameters/maps enable this path.
"""
from __future__ import annotations

import math
import re

from PIL import Image, ImageChops


def _parameter(material, *names):
    # Caller order is explicit priority. Attribute order and XML subtree
    # position must not override the declared CryEngine parameter precedence.
    nodes = [material, *(node for node in material.iter() if node is not material)]
    for name in names:
        wanted = name.casefold()
        for node in nodes:
            for key, value in node.attrib.items():
                if key.casefold() == wanted:
                    return value
            if (node.get('Name') or node.get('name') or '').casefold() == wanted:
                value = node.get('Value') or node.get('value')
                if value is not None:
                    return value
    return None


def _positive_float(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return 0.0
    return min(16.0, max(0.0, number)) if math.isfinite(number) else 0.0


def _color(value):
    if not value:
        return None
    try:
        parts = [float(p) for p in re.split(r'[,;\s]+', str(value).strip()) if p]
    except ValueError:
        return None
    if len(parts) != 3 or not all(math.isfinite(p) and p >= 0 for p in parts):
        return None
    # CryEngine serializes colors as 0..1, but some old tools use 0..255.
    if max(parts) > 1:
        parts = [p / 255.0 for p in parts]
    return tuple(min(1.0, p) for p in parts)


def _map_references(material):
    explicit = decal = None
    for node in material.findall('./Textures/Texture'):
        role = (node.get('Map') or node.get('map') or '').casefold()
        reference = node.get('File') or node.get('file')
        if not reference:
            continue
        if role in ('emittance', 'emissive', 'emission', 'glow') and explicit is None:
            explicit = reference
        if role == 'decal' and decal is None:
            decal = reference
    return explicit, decal


def shader_flags(material):
    """Parse exact CryEngine shader-generation flag tokens, never substrings."""
    return frozenset(part.strip().upper()
                     for part in (material.get('StringGenMask') or '').split('%')
                     if part.strip())


def emission_mask_source(material, mode=None):
    """Select the source of the emission mask, independently from opacity.

    Positive GlowAmount makes Illum/Cloth/HumanSkin emission available but
    does not imply that a Decal is masked by diffuse alpha. ALPHAGLOW opts
    into this extra behavior explicitly.
    """
    mode = emission_mode(material) if mode is None else mode
    if mode == 'legacy_alpha':
        return 'diffuse_alpha'
    if mode == 'legacy_decal' and 'ALPHAGLOW' in shader_flags(material):
        return 'diffuse_alpha'
    return 'none'


def emission_texture_node(material):
    """Actual emitting map node, for role-specific TexMod provenance."""
    explicit = decal = None
    for node in material.findall('./Textures/Texture'):
        role = (node.get('Map') or node.get('map') or '').casefold()
        if role in ('emittance', 'emissive', 'emission', 'glow') and explicit is None:
            explicit = node
        elif role == 'decal' and decal is None:
            decal = node
    if explicit is not None:
        return explicit
    return (decal if (material.get('Shader') or '').casefold() == 'illum'
            and _legacy_glow(material) else None)


def _legacy_glow(material):
    shader = (material.get('Shader') or '').casefold()
    flags = shader_flags(material)
    raw = _parameter(material, 'GlowAmount')
    return (_positive_float(raw) > 0.0
            and (shader in ('illum', 'cloth', 'humanskin')
                 or 'ALPHAGLOW' in flags))


def emission_mode(material):
    """One of none, legacy_alpha, legacy_decal, modern_map, modern_color.

    Decal is an Illum legacy *color source*, not a general-purpose emission
    declaration. Only legacy modes use diffuse alpha as an emission mask.
    """
    strength, _, ref = emission_settings(material)
    if strength <= 0.:
        return 'none'
    explicit, decal = _map_references(material)
    if explicit:
        return 'modern_map'
    if _legacy_glow(material):
        return ('legacy_decal' if decal and
                (material.get('Shader') or '').casefold() == 'illum'
                else 'legacy_alpha')
    return 'modern_map' if ref else 'modern_color'


def emission_settings(material):
    """Return (strength, optional color multiplier, verified MTL map reference).

    CryEngine 3 legacy GlowAmount/ALPHAGLOW normally takes its emission RGB
    from diffuse or Illum Decal, masked by diffuse alpha. The serialized
    Emissive="0,0,0" value on that legacy path is a default, not a black
    multiplier. Explicit colored emission overrides it and modern black
    emissive colors retain their black semantics.
    """
    shader = (material.get('Shader') or '').casefold()
    raw_glow = _parameter(material, 'GlowAmount')
    raw_modern = _parameter(material, 'EmissiveIntensity',
                            'EmittanceIntensity', 'EmissiveStrength',
                            'EmissionStrength')
    # Positive legacy GlowAmount has priority over auxiliary intensity
    # fields for known legacy shader modes; explicit zero disables it.
    intensity_raw = raw_glow if raw_glow is not None else raw_modern
    intensity = _positive_float(intensity_raw)
    modern_color = _color(_parameter(material, 'EmissiveColor',
                                     'EmissionColor', 'EmittanceColor'))
    serialized_color = _color(_parameter(material, 'Emissive'))
    legacy = _legacy_glow(material)
    if legacy:
        # A nonblack explicitly authored legacy Emissive is still meaningful,
        # but CryEngine's zero default must not erase positive alpha glow.
        color = (modern_color if modern_color is not None
                 else serialized_color if serialized_color and any(serialized_color)
                 else None)
    else:
        color = modern_color if modern_color is not None else serialized_color
    explicit, decal = _map_references(material)
    if legacy and decal and shader == 'illum' and not explicit and color is None:
        # CryEngine Illum Decal carries RGB emission but the owning legacy
        # material may tint it through its declared Diffuse color. Without
        # this the bright Decal RGB alone can make colored tubes white.
        # Do not apply this to modern Emittance or alpha-only Body glow.
        authored_tint = _color(_parameter(material, 'Diffuse'))
        if authored_tint is not None and any(authored_tint):
            color = authored_tint
    if explicit and intensity_raw is None:
        intensity = 1.0
    if intensity_raw is None and color is not None and any(color):
        intensity = 1.0
    if intensity <= 0.0:
        return 0.0, None, None
    return intensity, color, (explicit or (decal if legacy and shader == 'illum'
                                           else None))


def bake_emission_map(diffuse, *, color=None, emission=None,
                      diffuse_alpha_mask=True):
    """Bake a glTF/GPU-ready RGB emission map at source resolution.

    The explicit emission/decal map supplies RGB, or the diffuse RGB does.
    Diffuse alpha masks glow ONLY on a verified emissive material and when
    the caller has ruled out using that alpha for opacity.
    """
    if diffuse is None and emission is None:
        return None
    source = emission if emission is not None else diffuse
    result = source.convert('RGB')
    if color is not None:
        channels = result.split()
        result = Image.merge('RGB', tuple(
            channel.point(lambda level, factor=factor: int(round(level * factor)))
            for channel, factor in zip(channels, color)))
    if diffuse_alpha_mask and diffuse is not None and 'A' in diffuse.getbands():
        mask = diffuse.getchannel('A')
        if mask.size != result.size:
            mask = mask.resize(result.size, Image.Resampling.BILINEAR)
        # This is an emission mask, not transparency; it must not modify
        # the surface diffuse image or material alphaMode.
        result = Image.merge('RGB', tuple(
            ImageChops.multiply(channel, mask) for channel in result.split()))
    return result


def subset_emission_coverage(mesh, materials, slot, max_samples=4096):
    """Approximate emissive vertex-UV coverage for a material subset.

    Returns (nonzero_count, sampled_count) or None for absent UV/geometry.
    Checks the *coordinates actually used* by the mesh, not whether some
    unrelated region of a texture atlas has bright pixels. This is a
    vertex-sampling diagnostic, not a rasterization/in-game shader oracle.
    """
    from texture_transform import transform_for, transform_uv
    texture_maps = getattr(materials, 'emissive_maps', ())
    if not mesh or slot < 0 or slot >= len(texture_maps):
        return None
    img = texture_maps[slot]
    if (img is None or not hasattr(mesh,'triangles') or
            not hasattr(mesh,'vertices') or not hasattr(mesh,'uvs') or
            len(mesh.uvs) != len(mesh.vertices)):
        # The viewer may display partial/loading meshes and synthetic
        # diagnostics before their triangle streams have arrived.
        return None
    uv_ids = set()
    for first, count, material_id in (getattr(mesh, 'subsets', ()) or
                                      ((0, len(mesh.triangles) * 3, 0),)):
        if material_id != slot:
            continue
        for triangle in mesh.triangles[first // 3:(first + count) // 3]:
            uv_ids.update(int(v) for v in triangle)
            if len(uv_ids) > max_samples * 8:
                break
    if not uv_ids:
        return None
    ids = sorted(uv_ids)
    if len(ids) > max_samples:
        stride = (len(ids) + max_samples - 1) // max_samples
        ids = ids[::stride]
    emission_transform = transform_for(materials, slot, 'emissive')
    diffuse_transform = transform_for(materials, slot)
    modes = getattr(materials, 'emissive_masks', ())
    shader_mask = slot < len(modes) and modes[slot] == 'diffuse_alpha'
    base = (getattr(materials, 'textures', ()) or ())
    diffuse = base[slot] if shader_mask and slot < len(base) else None
    if shader_mask and diffuse is None:
        return None
    w,h = img.size
    mw,mh = diffuse.size if diffuse is not None else (0,0)
    lit = 0
    for index in ids:
        if index < 0 or index >= len(mesh.uvs):
            continue
        u,v = mesh.uvs[index]
        x,y = transform_uv(float(u),float(v),emission_transform)
        try:
            rgb = img.getpixel((int((x % 1.) * w) % w, int((y % 1.) * h) % h))
            level = max(rgb[:3] if isinstance(rgb, tuple) else (rgb,))
            if diffuse is not None:
                du,dv = transform_uv(float(u),float(v),diffuse_transform)
                alpha = diffuse.getpixel((int((du%1.)*mw)%mw, int((dv%1.)*mh)%mh))
                alpha = alpha[3] if isinstance(alpha,tuple) and len(alpha)>=4 else 255
                level = level * alpha / 255.
            if level > 0:
                lit += 1
        except (ValueError, OverflowError, IndexError, TypeError):
            continue
    return lit, len(ids)
