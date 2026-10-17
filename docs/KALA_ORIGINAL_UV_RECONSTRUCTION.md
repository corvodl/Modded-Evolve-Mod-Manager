# Kala Stage 2 original UV/topology analysis

Source: user-provided extracted Kala **LOD0** body (`tech_3p.skinm`) and matching `tech_3p.skin`, `tech_3p_skeleton.chr`, `tech_3p.cdf`, `tech_3p.mtl`, available in the user's private asset directory. **No proprietary asset bytes are committed here.**

## Observations from original CrChF v7 binary

| Native value | Observed |
| --- | ---: |
| position stream (type 0), half4 stride 8 | 34,422 vertices |
| UV stream (type 2), float2 stride 8 | **one stream, 34,422 UV pairs** |
| index stream (type 5), uint16 | 155,037 indices / 51,679 triangles |
| bone mapping (type 9), 8-byte entries | 34,422 vertex records |
| material subsets (SUBSETS chunk) | 13 |
| geometric vertex-position groups represented by multiple indices | 6,081 |
| extra vertices with exactly identical positions | 6,922 |
| geometric two-triangle edges split across render vertex indices | 6,481 |
| split edges whose endpoint UV values differ | 6,343 |
| split edges whose endpoint UV values match | 138 |
| position/UV/raw bone-map-identical redundant vertex records | 146 |
| UV range | U: 0.00052–1.9899, V: 0.00032–0.99772 |

The original body has **one** UV stream, not two. A user seeing many islands in Blender is not evidence that CryEngine authored a second map. UV discontinuities are mostly genuine seams; preserve per-corner coordinates. Some original U values exceed 1.0 (texture tiling), which must **not** be clamped to the 0–1 tile.

## Implemented reconstruction strategy

- Do not manufacture a second Smart-UV atlas or modify any source DDS mapping.
- Preserve the original UV0 and copy it to Blender face-corner UVs, with the name `UV0_Original`.
- Split independent materials into editable mesh objects without changing texture coordinates.
- Reconnect only geometrically matching pairs on exactly two-triangle edges, within the same material and with matching skin weights and normals. Skip nonmanifold edges, unsupported shape keys, and custom point attributes.
- Compare a before/after multiset of polygon material IDs, corner positions, **all UV layers**, skin weights, and vertex normals. Roll back the mesh if any check fails. Preserve the armature and embedded Actions.
- Do not claim that runtime seam connectivity equals the artist-authored source topology. Rebuild only demonstrably equivalent connectivity from cooked data.

A separate, local diagnostic of candidate seam reconnections (without normals or a Blender round-trip) found thousands of potentially reconnectable pairs. That estimate is **not** a claim about the final FBX; real Blender validation and the original animation rig remain required.

## Testing

Synthetic regression tests cover: seams with genuinely different UV corners, matching rig influences, conflicting skin weights, material boundaries, nonmanifold edges, absent/invalid UV export receipts, and no automatically generated UV1. The sampled proprietary mesh must remain outside CI/repository and can be used manually for a Blender 4.2+ import/export verification.
