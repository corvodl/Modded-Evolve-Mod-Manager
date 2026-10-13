"""Attach normal textures to glTF 2.0 material dictionaries.

Callers must append a PNG image and texture to their glTF document first.
This helper does not guess material bindings or mutate original game assets.
"""
from __future__ import annotations


def attach_normal_texture(gltf: dict, material_index: int, texture_index: int,
                          *, scale: float = 1.0, texcoord: int = 0) -> None:
    """Wire an existing texture to a material's tangent-space normal slot.

    glTF normal maps are linear, not sRGB. Exporters should encode source
    CryEngine normals in glTF's +Y convention before calling this function.
    """
    import math

    materials = gltf.get("materials", [])
    textures = gltf.get("textures", [])
    if not isinstance(material_index, int) or not 0 <= material_index < len(materials):
        raise ValueError("Invalid material index")
    if not isinstance(texture_index, int) or not 0 <= texture_index < len(textures):
        raise ValueError("Invalid texture index")
    if not isinstance(texcoord, int) or texcoord < 0:
        raise ValueError("Invalid UV set")
    if not math.isfinite(scale) or scale < 0:
        raise ValueError("Invalid normal strength")
    material = materials[material_index]
    if not isinstance(material, dict):
        raise ValueError("Invalid material")
    material["normalTexture"] = {"index": texture_index, "texCoord": texcoord,
                                 "scale": float(scale)}
