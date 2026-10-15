# Scaleform bitmap placement: verified evidence and implementation stage 1

## Supplied Evolve Stage 2 source samples (not committed)

- `main_menu.gfx` and `main_menu_I9.dds.0`, `main_menu_IB.dds.0`,
  `main_menu_I108.dds.0`
- `effectsModeSelect.gfx` and 170 associated DDS fragments
- `effectsMainMenuFocus.gfx` and 60 associated DDS fragments

**All images came from the same game build.** None of the copyrighted
game GFX/DDS contents are checked into this PR. Tests generate new,
tiny synthetic GFX files with equivalent extension record structures.

## What the compiled data proves

**Extension 1009**: image atlas table, with a `uint16` atlas index,
`uint16` type/format fields, logical width and height, and a
length-prefixed source `.tif` name. The matching cooked asset is the
same virtual path with `.dds.0` (or complete `.dds`) extension.

**Extension 1008**: exactly twelve bytes, parsed as six little-endian
`uint16` fields: bitmap character, image index, crop x0, y0, x1, y1.
This is a **pixel rectangle inside the atlas**, not an on-screen
position. Reconstruct it by selecting the referenced cooked DDS and
cropping at these exact coordinates.

In the examined samples:

| Movie | Images declared | Images present | Bitmap mappings | Bounds outside DDS | Standard first bitmap FillStyle links |
|---|---:|---:|---:|---:|---:|
| main_menu | 3 | 3 | 61 | 0 | 55 |
| effectsModeSelect | 170 | 170 | 170 | 0 | 170 |
| effectsMainMenuFocus | 60 | 60 | 180 | 0 | 180 |
| **Total** | **233** | **233** | **411** | **0** | **405** |

For `main_menu.gfx` the static first-frame display list reaches
three of the 61 parsed bitmap-filled resources. Both effects movies
have no ordinary root first-frame placements. Their sprite instances
can be driven by parent movies and/or game callbacks; no position is
invented for the nonplaced characters.

### Examples of verified atlas crops

- Bitmap #9 in `main_menu` uses atlas index 0,
  `main_menu_I9.dds.0`, crop `(698,590)-(762,654)` and a
  standard `DefineShape #10` bitmap fill.
- Bitmap #11 in `main_menu` uses atlas index 1,
  `main_menu_IB.dds.0`, crop `(810,206)-(874,270)`.
- Bitmap #3 in `effectsMainMenuFocus` uses atlas index 30 and
  a `(0,0)-(720,256)` crop (the atlas is 1024×1024).
- `main_menu_I108.dds.0` is a 2048×1024 physical atlas for a
  1338×1005 logical image. Never mistake the physical padded
  dimensions for the logical GFX resource dimensions.

## New GFX viewer feature

**Bitmap placement** is a read-only tab beside the existing frame and
referenced textures. It lists each bitmap character, exact compiled
atlas source and rectangle, linked shape IDs (where safely
interpretable), matching-build DDS availability, and whether a
shape is reachable from the first static root timeline frame.

The selected symbol's exact atlas crop is decoded on demand from
the existing stream-aware DDS texture decoder, not loaded in bulk.
No original game data, bitmap pixels or exported PNG are modified.
The tab does **not** superimpose pictures over the FFDec GFX raster.

## Next phase before claiming exact in-frame placement

Reconstruct per-frame display-list state including
`PlaceObject`/`PlaceObject2`/`PlaceObject3` matrices, depth,
remove/move operations, sprite-local matrices, mask clipping,
color transforms, and the full bitmap FillStyle transform.
Validate those transforms against same-state in-game screenshots;
FFDec does not necessarily render Scaleform's external DDS extension.
For dynamically instantiated content, capture UI/game callbacks or
runtime state. The label **verified bitmap binding** is therefore
distinct from **verified in-frame placement**.

## Validation

`python -m unittest -v test_gfx_placement_diagnostics test_qt_gfx_editor`
is included in Windows GFX CI. The synthetic tests cover valid image
and atlas references, missing DDS, invalid rectangles, nested
first-frame sprites, and the viewer's on-demand cropping.
