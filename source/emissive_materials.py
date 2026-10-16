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
    wanted = {name.casefold() for name in names}
    nodes = [material]
    nodes.extend(node for node in material.iter()
                 if node is not material and node.tag.casefold() in
                 ('publicparams', 'shaderparams', 'materialparams'))
    for node in nodes:
        for key, value in node.attrib.items():
            if key.casefold() in wanted:
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


def emission_settings(material):
    """Return (emission strength, color multiplier or None, map reference).

    A positive GlowAmount enables diffuse-alpha glow. No positive emission
    settings and no explicit Emittance map means an ordinary material.
    A Decal slot is glow ONLY with an Illum shader and positive GlowAmount.
    """
    shader = (material.get('Shader') or '').casefold()
    value = _parameter(material, 'GlowAmount', 'EmissiveIntensity',
                       'EmittanceIntensity', 'EmissiveStrength',
                       'EmissionStrength')
    intensity = _positive_float(value)
    color = _color(_parameter(material, 'EmissiveColor', 'EmissionColor',
                              'EmittanceColor', 'Emissive'))
    emissive_ref = None
    decal_ref = None
    for node in material.findall('./Textures/Texture'):
        role = (node.get('Map') or node.get('map') or '').casefold()
        ref = node.get('File') or node.get('file')
        if not ref:
            continue
        if role in ('emittance', 'emissive', 'emission', 'glow'):
            emissive_ref = ref
        if role == 'decal':
            decal_ref = ref
    if emissive_ref and value is None:
        intensity = 1.0
    if intensity == 0 and value is None and color and any(color):
        intensity = 1.0
    if intensity <= 0:
        return 0.0, None, None
    return intensity, color, (emissive_ref or
                              (decal_ref if shader == 'illum' else None))


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
