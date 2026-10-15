# Visual GFX layout editing — experimental beta.50

PR #68 adds a **Visual layout editor…** command to the existing editable GFX
dialog (Tools menu and an Editing and rebuild button). It shares the same
backup-protected, undoable FFDec GFX editing session and the original
**Apply to Mod Project** confirmation. Asset Library originals remain
read-only; no source PAK is modified.

## Static placement editing

- Show a tree of **root, first-frame PlaceObject / PlaceObject2** layers
  with character IDs, depth, known exported names and real DefineShape
  bounding rectangles.
- Shapes with decoded bounds have selectable/draggable outlines in the
  1920×1080 (or other original) GFX stage coordinates. A separately
  rendered FFDec preview can be used as an optional background; neither
  raster pixels nor overlay artwork are written into the source movie.
- Edit X/Y, X/Y scale, rotation and visibility numerically. Staging changes
  only the selected root placement's MATRIX in a private GFX session.
  Hiding an unambiguous root first-frame item removes its placement tag,
  not the bitmap, script or referenced resource definitions. Undo restores
  the previous session copy. Depth and other tags remain untouched.
- Root-level sprites with unknown bounds are **numeric-only**; the editor
  does not manufacture a box or guess the final runtime position.
- Ambiguous depth, skewed/reflected transforms or unsupported
  PlaceObject3 filters/class placements are read-only. Nested sprites,
  timeline animations and runtime ActionScript objects are not blindly
  rewritten.

## Featured menu: GLACIAL BEHEMOTH / TRY / BUY

`Libs/UI/main_menu.gfx` exposes a separate tree category for dynamic
**Featured title**, **TRY** and **BUY** controls. Their location cannot
be recovered from a single static frame, so no bounding boxes are
fabricated. The editor offers guarded **hide**, **remove/disable** and
**replace title** presets already available in beta.49, plus an
experimental **absolute X/Y, scale, rotation, visibility override**
inserted at the end of the one verified `setFeatured` ActionScript
method. The imported FFDec result is always validated.

The actual game can reassign the text, visibility or transform via
callbacks, localization and the featured-offer service. A passing
synthetic test verifies script generation and GFX staging—not success
inside Evolve. Test modded menus offline before publication. Changing
the promo text does not change the 3D character in the background.

## Safety and tests

- Changes always use the current edit session and its normal Undo and
  Apply-to-Mod-Project workflow, preserving source-file hash checks.
- Validate finite bounded coordinates and matrix bit widths; unsafe or
  duplicate first-frame placements cannot be edited.
- UI tests cover visual layer selection, bounded/unbounded sprites,
  custom text fields, static staging/undo; pure backend tests cover
  root transforms, rotation and scale encoding, hide, compressed movies,
  invalid input and runtime ActionScript property generation.
- External DDS rasterization from this PR remains independent from
  actual game UI runtime behavior. It improves the static backdrop;
  it does not make dynamic widgets statically accessible.

Limitations: stage overlay does not edit nested per-frame sprite matrices,
text glyph geometry, masks, fonts or movie-level runtime callback
implementation. Those still need advanced FFDec editing or a future
verified hierarchy/timeline editor.
