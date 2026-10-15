# Evolve Stage 2 Level Scene Preview — Experimental

This is a **copy-only static-mesh level placement preview**, not a CryEngine level compiler or a game/account modification.

## Source selection and archive identity

Open an extracted editable levels-map project containing .lyr layers. The Maps menu can choose another prepared project. The Asset Library remains read-only.

Compiled level archives reuse bare paths such as brush/designer_100.cgf, leveldata.xml and tile0_0.raw. Those are **not globally unique identifiers**. The original archive source path from the asset-library manifest must match the selected map for root-relative assets; copied workspace assets override library sources. Conflicting shared assets are reported, not guessed.

## Supported in this stage

- Verified CrChF static geometry from supported .cgf, .skinm, and .chrm streams, including bounded float3/stride12 and existing half4/stride8 positions.
- World transform hierarchy with CryEngine w,x,y,z quaternions, per-instance scale, and source-space sizing; no per-model centering.
- Depth-tested OpenGL scene viewport with picking by mesh bounds, orbit/pan/dolly, perspective/orthographic views, and conservative frustum culling.
- Background CPU geometry/material preparation, cancellation, context-thread-only texture uploads, shared mesh resources, and bounded memory.
- Approximate diffuse materials with verified material associations and static UV transforms. Explicit copied-project material overrides use undo/redo, backups and conflict-detecting atomic saves.
- Bounded read-only inspection of source .cry editor ZIP archives and Heightmap.dat / VegetationMap.dat XML metadata. **Their binary terrain and placement payloads are NOT decoded.**

## Remaining gaps and validation

- This is not yet a reconstructed **textured full map**: no validated elevation samples, vegetation instance placement, cooked terrain tiling, procedural roads, Designer polygon remeshing, prefab graph expansion or fully rigged CDF actors.
- Parent nonuniform scales combined with child rotations can induce shear; the current TRS-only scene path does not fully model that.
- Unsupported geometry stays diagnostic rather than receiving invented proxy meshes. Approximate diffuse rendering is not CryEngine shader parity; no scene GLB export or game-ready compiled level package is promised.
- Exact Tutorial / Desert Canyon screenshot and landmark validation, texture UV correctness, map-switch collision checks, and 8,638-object performance metrics require the full newer extracted Asset Library, not the historical ZIP or evidence JSON.
- Source XML saves change only copied layer/mission files; they cannot rebuild terrain, physics, navigation or lighting.

The GitHub CI tests use synthetic fixtures, no proprietary game archives. Do not call the viewport feature fully complete until actual map results are verified.
