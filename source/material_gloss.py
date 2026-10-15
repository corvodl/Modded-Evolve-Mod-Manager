"""Decode *explicit* CryEngine gloss/smoothness into linear glTF roughness.

The specular (color) map is not equivalent to a gloss map. This helper is
called only for explicitly named Gloss, Glossiness or Smoothness MTL slots.
No game assets are modified.
"""
from __future__ import annotations

from PIL import Image, ImageOps


def gloss_to_metallic_roughness(gloss: Image.Image) -> Image.Image:
    """Encode metallic=0 in B and inverse gloss (roughness) in G.

    glTF metallicRoughnessTexture is always sampled in linear space, so
    export RGB pixel bytes without a gamma conversion. R is unused by PBR;
    a white R channel permits inspection without changing metallic/roughness.
    """
    if gloss is None or not hasattr(gloss, 'size'):
        raise ValueError("A decoded gloss texture is required.")
    width, height = gloss.size
    if not 0 < width <= 8192 or not 0 < height <= 8192:
        raise ValueError("Invalid gloss texture dimensions.")
    smoothness = gloss.convert("L")
    roughness = ImageOps.invert(smoothness)
    return Image.merge("RGB", (
        Image.new("L", gloss.size, 255),
        roughness,
        Image.new("L", gloss.size, 0),
    ))
