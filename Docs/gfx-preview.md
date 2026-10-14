# Scaleform GFX preview, full FFDec editing, and GFX rebuild

The file explorer in **Modding** and the **read-only Asset Library** recognizes
compiled CryEngine / Scaleform `.gfx` files and opens a graphical *static*
preview. The UI shows the movie's dimensions, frames per second, total root
frames and nested sprite symbols. Use **Symbol**, **Frame**, and **Raster**
(1×/2×/4×; up to 36 megapixels), then **Fit** or display zoom. The checkerboard
is for transparency inspection only. **Export selected PNG…** writes FFDec's
rendered RGBA frame/sprite at the requested resolution; it is not a screenshot
of the enlarged/scaled viewer. Existing raw export still works.

## Bundled FFDec renderer and Java on Windows

The Windows installer and portable ZIP now package an independent,
unmodified **FFDec 26.3.0** (`ffdec.jar`) and an independent private
**Eclipse Temurin OpenJDK 21.0.12+8 JRE**. Both render the preview and power
advanced full-GFX editing automatically. You do not need to install FFDec
or configure Java. The full Windows build verifies the pinned ZIP hashes and
**includes source archives and third-party license/legal notices**:

- FFDec GPLv3-or-later, original upstream package SHA-256
  `35f4930eb7c380afe66f2117f90b006deac0631473ad7500bb39c78f68645ecd`;
  sources at `_internal/third_party/FFDec/Sources/FFDec-source-b2ef2d3.zip`.
- Temurin JRE 21.0.12+8, original upstream package SHA-256
  `b8aa18fef5edb69bee8618f99677d66d0873d22cb40d974c15ac9ffcdecf73ba`;
  OpenJDK corresponding source at
  `_internal/third_party/FFDec/Sources/OpenJDK21U-jdk-sources_21.0.12_8.tar.gz`,
  and JRE legal notices under `_internal/third_party/FFDec/jre/legal`.
- Third-party attribution: `_internal/third_party/FFDec/NOTICE.txt`.
  Official projects:
  https://github.com/jindrapetrik/jpexs-decompiler and
  https://github.com/adoptium/temurin21-binaries

Each app build downloads and verifies these upstream distributions **at build
time**, not in an installed game or running Mod Manager. FFDec remains a
separate GPL tool launched as its own Java process. Users can inspect,
modify and redistribute the upstream source under its respective terms.

A manual FFDec 26.3.0 override is still available via **Change FFDec…**.
An invalid renderer path remembered from an older beta falls back to the
bundled FFDec and private Java automatically. No update of your signed PAKs
or Data folder is required.

The renderer runs in an isolated temporary layout with a 28-second timeout,
cancel, size limits and bounded diagnostic output. Scaleform ImportAssets
checks the exact virtual filename first, then a **unique actual .gfx/.swf
movie in the same UI subtree** (for example under `Libs/UI/FlashAssets`)
from the selected game build. It never substitutes unrelated DDS files for
missing SWFs, and ambiguous duplicates remain missing with diagnostics.
Different game builds are not mixed. ActionScript runtime callbacks are still
not simulated.

External in-game font, animation and script dependencies **not present in
the imported same-build PAKs cannot be bundled or fabricated**. The full
library imports are needed for the best GFX preview.

## Edit and rebuild a GFX inside a Mod Project

Only an **editable Modding PAK workspace** supports write actions. Asset
Library originals stay read-only; choose **Edit / Add to Mod Project** first.
Select the desired `.gfx` in the file explorer and click **Edit full GFX…**
(or choose **Edit → Edit Scaleform GFX Movie…**). The manager makes a private,
hash-bound `GfxEditSessions/<id>/movie.gfx` copy. No archive is rewritten.

There are several supported authoring paths, all using pinned FFDec 26.3.0:

- **Replace selected resource:** choose an existing image (PNG/JPEG), SVG
  vector shape, text (.txt), audio, or binary-data tag by real character ID.
  FFDec replaces that tag and writes a new compiled GFX edit copy.
- **Export / rebuild ActionScript:** export editable AS sources to a new
  folder, modify the `.as` code, then use **Rebuild from edited
  ActionScript…**. FFDec decompiles/recompiles the movie; scripts may use
  APIs and native callbacks not present in Stage 2.
- **Export tag XML / rebuild XML:** FFDec round-trip for lower-level sprite,
  button, timeline, symbol, and tag metadata changes. XML input is bounded
  and rejects DTD/external entities. Keep a clean XML copy for rollback.
- **Open full FFDec editor…:** launches the external FFDec graphical authoring
  UI on the private session copy for additional editing of fonts, buttons,
  ActionScript, timelines, image/shape tags, and supported native objects.
  After saving there, select **Reload FFDec changes** in the manager.
- **Import edited GFX…:** bring back another edited `.gfx` result; the
  manager validates GFX headers, bounds, imports, and file size.
- **Undo staged edit:** reverse the last internal session rebuild. Ordinary
  project original bytes are untouched until approval.

Review the updated frame/symbol preview. When ready, click **Apply to Mod
Project** and explicitly approve. The manager checks that the original
extracted project GFX still has its original SHA-256, validates the staged
movie, creates a verified timestamped copy in **EditorBackups**, then
atomically replaces only the extracted project entry. Only the regular
**Build Mod / sign / offline test** workflow can place those edits into an
actual Evolve mod. No editing step automatically replaces an installed game
PAK. If the edited file breaks the game, restore its original via the
editor's existing **Restore Original Extraction** or project undo.

This is broad authoring through an **external FFDec dependency**, not a
claim that every Scaleform extension, font, script API, interactive state,
or game-specific compiled object is editable natively in Qt. FFDec can
fail on unsupported engine-specific tags; in that case the original project
remains safe and the edit is rejected or left isolated. No final game
compatibility can be claimed until the changed movie is tested offline.

## Actual supported output and limits

This is a **static** root-timeline or nested-sprite renderer, not a Flash
ActionScript runtime or reconstructed in-game UI. Frame selection can expose
more than the first picture. Embedded images are reported but not separately
exported as raw bitmap symbols in this initial release; choose their containing
frame/sprite or use FFDec directly. Unknown Scaleform tags may not render
exactly like the game's engine. Game-created labels, localization, and
ActionScript `openScreen(...)` callbacks cannot appear unless driven by the
game. Blank output is not a success claim for those dynamic elements.

Suggested manual verification with user-provided Evolve assets (NOT in repo):
- `Libs/UI/emblem_bg_circle.gfx`: static white circle, frame rendering and
  alpha preservation.
- `Libs/UI/watermark.gfx`: 1920×1080, 1 static root frame, imported
  `gfxfontlib_locfont.gfx`; root can be blank because the game populates
  labels via `openScreen(accountName,id)`, requiring engine localization and
  callback execution.

Synthetic tests cover uncompressed GFX, compressed CFX, bounds, frames,
sprite metadata, script/import warnings, rejection of remote/traversal imports,
stale async responses, raw hash preservation, PNG alpha, build context cache
invalidations and collisions. A dedicated Windows CI job downloads the pinned
FFDec test dependency in CI only and exercises real vector-frame rendering.
No proprietary Evolve files or keys are committed.

## UI configuration from Game/UI_Data.pak

The canonical archive is **Game/UI_Data.pak** (underscore retained).
Import it into the **same registered Evolve build** as `UI_Assets.pak`.
When a GFX is opened from that Asset Library build, **UIData references…**
uses only this build's manifest-listed XML to show the matching
`UI/UIElements/*.xml` screen registration, GFx filename/layer/alignment,
game-callable functions, button commands, localized font source filenames,
preload groups and explicitly named news DDS images.

It does **not** present those entries as a proven mapping of sprite IDs to
DDS atlas UV rectangles, does not load assets from another game build,
and does not execute `openScreen`, `updateNews`, or other game callbacks.
Compiled GFX and DDS remain read-only in the Asset Library; edits belong
in an explicit Modding project with backups. If `UI_Data.pak` is absent,
the viewer reports which archive to import.

## Supplied main-menu sample: structure-only check

The user supplied `main_menu.gfx` privately for this PR. It was NOT added
to Git or any release/test fixture. Its SHA-256 is
`7ac1133f42fcb3a6b74ddd228860beb154965d8f6a1d8f2ab4e02630bc442762`;
it is uncompressed GFX v15, 353,610 bytes, 1920×1080 at 30 FPS, with
one root frame, 169 sprite definitions (58 of them zero-frame
nonvisual/class shells), 87 named exports and three ImportAssets2 movies:
`gfxfontlib_locfont.gfx`, `effectsModeSelect.swf`, and
`effectsMainMenuFocus.swf`. Its sprite definitions and script exports
motivated the zero-frame parser fix and visual-only sprite picker;
the `.swf` imports prompted separate SWF dependency validation.

This sample was inspected at the binary/tag level without running FFDec
against those proprietary bytes, and the actual on-screen in-game main
menu cannot be certified from this analysis. The real-renderer CI tests
use only redistributed synthetic shapes and sprites.

Static preview and draft GFX editing do not change installed game PAKs,
Asset Library originals, or signing keys. Explicitly approved GFX edits do
change one extracted **Modding project** file with a verified original in
EditorBackups. Existing PNG outputs are never silently overwritten.
