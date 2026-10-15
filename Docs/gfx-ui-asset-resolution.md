# GFX compiled imports and same-build UI texture inspection

This change extends the read-only/static preview. It does **not** emulate
Evolve's Scaleform runtime, callbacks, localized text, or layout driven by
ActionScript.

## Verified source relationships (Evolve Stage 2 UI_Data and UI_Assets listing)

- `UI/UIElements/MainMenu.xml` points to `main_menu.gfx`.
- `UI/TexturePreload.xml` references `Libs/UI/effects*` for `NoLevel`.
- `UI/MainMenuNews.xml` explicitly references
  `Libs/UI/FlashAssets/images/News/news_img*.dds`.
- The UI_Assets inventory lists
  `Libs/UI/effectsModeSelect.gfx` and
  `Libs/UI/effectsMainMenuFocus.gfx`,
  not the corresponding `.swf` files.
- It lists `main_menu_I*.dds.0`,
  `effectsModeSelect_I*.dds.0`, and
  `effectsMainMenuFocus_I*.dds.0` companions.

The UI_Data XML entries generally do **not** assign a particular DDS to
a precise movie symbol/display-list position. A `Preload` is a resource
availability directive, not a render instruction.

## Behavior

1. Resolve the exact `.swf` import in the same selected build/project.
   If absent, validate same-directory `.gfx`/`.cfx` bytes and reheader
   them as `FWS`/`CWS` for FFDec's local SWF import. The required SWF
   virtual path is kept. Malformed compiled files, unsafe paths, and
   cross-build/ambiguous candidates are rejected. Original bytes remain
   unchanged in the library.
2. For a selected GFX, list matching `<movie>_I<hex>.dds(.0)` images and
   imported-movie companions. Scan relevant same-build UIElement XML and
   documented preload/news paths for existing DDS assets. Do not
   attribute unrelated XML or same-basename textures across builds.
3. In the separate **Referenced textures** tab, decode one selected DDS
   on demand using the existing stream-aware CryEngine decoder; it can
   locate higher mip fragments from the matching build. Do not preload
   hundreds of DDS files or inject guessed images into an FFDec frame.
4. PNG export remains the FFDec movie raster, **not** a composite of
   unrelated UI_Data textures. Display unresolved and unsupported assets
   explicitly.

## Validation

- `python -m unittest test_gfx_preview test_gfx_ui_dependencies
  test_qt_gfx_editor test_gfx_library_scope`
- The GFX Windows CI also exercises real pinned FFDec rendering and the
  standard packaging candidate workflows.
- For game-data end-to-end verification, import UI_Data and UI_Assets
  extracted PAKs in the **same registered build** and open
  `Libs/UI/main_menu.gfx`. The preview should enumerate both
  `effects*` imports and their associated texture entries. No original
  PAK files should change.

**Known limitation:** Some compiled GFX bitmaps use Scaleform-specific
extension tags and external DDS storage, which FFDec may not rasterize
in the actual GFX frame. Accurate placement/compositing requires
decoding and validating those extension records against matching
compiled GFX and DDS sample bytes, not guessing symbol positions.
