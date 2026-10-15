# External DDS textures in the GFX frame — experimental

The Scaleform viewer now has a **Render referenced DDS in frame** checkbox
(enabled by default when the movie has 1008/1009 extension tags and the
project or Asset Library provides a matching DDS decoder). Uncheck to view
the original untextured FFDec output.

## Method

- The existing build-scoped, stream-aware CryEngine decoder supplies
  read-only textures. Tags 1009/1008 map texture atlas source and exact
  pixel crop to external bitmap character IDs.
- A **temporary render-only GFX copy** injects DefineBitsLossless2 tags
  under those IDs and removes only the corresponding 1008 definition
  records. Original FillStyle matrices, shape geometry, PlaceObject
  transforms, depths, nested sprite timelines, masks and alpha transforms
  are preserved for FFDec rendering.
- Compiled same-build GFX dependencies imported under SWF names can also
  be given temporary images. Sandbox imports remain strictly local.
- Export PNG uses the FFDec rendered RGBA bytes and preserves alpha.
  No original PAK, GFX file or project is modified.
- UI_Data declares preloaded resources, **not image coordinates**; no
  texture is pasted at guessed screen coordinates.

## Boundaries

This is static placement recovery, not the game runtime. ActionScript-driven
movies, game callbacks, runtime localization/fonts and unsupported FillStyle
or Scaleform extension variants can remain incomplete. Movie availability
and atlas binding do not guarantee an image is displayed on a given frame.
The live game screenshot is a comparison target, not proof of exact matching.

The render budget is 128 bitmap characters, 2.5 million pixels per crop,
8 million crop pixels total and 32 MiB for the reconstructed GFX. A bounded
small physical-atlas cache avoids loading all of UI_Assets in memory.
Cancellation is checked between resource decodes; missing or corrupt
textures are reported and left out. The feature never modifies the game's
featured offer, text or underlying 3D scene.

## Tests

Windows CI uses fabricated GFX and DDS textures (no copyrighted data),
tests source-file integrity, crop, safe missing/short atlas handling and
undo-independent operation, and runs pinned FFDec 26.3.0 for an actual
positioned-color/alpha check. For a game sample, import UI_Assets and
UI_Data in the same Asset Library build and compare main_menu.gfx's static
overlay with a matching in-game screenshot.
