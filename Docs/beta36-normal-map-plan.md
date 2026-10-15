# Beta.36 — normal-map export and preview implementation checklist

Status: planned; no implementation or validation claimed. Base: published beta.35 branch.

## Scope
- Discover CryEngine normal textures via material bindings and common `_ddn` naming, with cross-PAK Asset Library lookup and explicit ambiguity diagnostics.
- Reassemble the highest available DDS mip level from the correct streaming sidecars, checking dimensions and avoiding silent fallback to low-resolution data.
- Decode supported DDS compression/channel conventions into tangent-space normals; preserve source DDS when an encoding is unsupported. Never assume green-channel inversion: determine convention by format/material evidence and verify on test fixtures.
- Export a self-contained `textures/` directory with diffuse, normal and other available maps, collision-safe names, provenance and diagnostics.
- Connect normal maps as Non-Color data through Blender Normal Map shader nodes; verify normal strength and tangent-space orientation, including GLB/glTF handling where applicable.
- Use the same decoded normal textures in the GPU preview with proper tangent basis; provide an explicit fallback for missing tangents/normal maps.
- Never mutate original game PAKs. Keep Stage 2-only verification and offline defaults.

## Acceptance gates
- Synthetic DDS fixtures for BC3/DXT5nm, BC5 and uncompressed normals; channel-swizzle, reconstruction and mip-sidecar tests.
- Asset lookup and ambiguous-reference tests; highest-resolution and folder layout assertions.
- Blender import validation for material node wiring and visual normal orientation; GPU screenshot regression.
- Full Windows tests, EXE, installer, screenshot and packaging gates must pass; review required before merge.
- Update VERSION.txt, release_notes.py and README.md only when preparing a verified beta.36 candidate; publish prerelease only after green gates.
