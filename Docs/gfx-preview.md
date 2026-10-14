# Scaleform GFX preview, full FFDec editing, and GFX rebuild

The file explorer in **Modding** and the **read-only Asset Library** recognizes
compiled CryEngine / Scaleform `.gfx` files and opens a graphical *static*
preview. The UI shows the movie's dimensions, frames per second, total root
frames and nested sprite symbols. Use **Symbol**, **Frame**, and **Raster**
(1×/2×/4×; up to 36 megapixels), then **Fit** or display zoom. The checkerboard
is for transparency inspection only. **Export selected PNG…** writes FFDec's
rendered RGBA frame/sprite at the requested resolution; it is not a screenshot
of the enlarged/scaled viewer. Existing raw export still works.

## Required optional renderer on Windows

The Mod Manager does not bundle JPEXS or Java, silently download executables,
or reinterpret a GFX as DDS/PNG. This avoids redistributing FFDec's GPLv3
third-party components without reviewing license compliance and corresponding
source obligations. The main app, installer and portable ZIP continue to work
without this extra tool, but GFX rasterization clearly reports it unavailable.

1. Download the **official stable FFDec 26.3.0** Windows ZIP
   from https://github.com/jindrapetrik/jpexs-decompiler/releases/tag/version26.3.0
   and extract it outside the game and Mod Manager's signed staging directories.
2. For the ZIP archive, SHA-256 must be
   `35f4930eb7c380afe66f2117f90b006deac0631473ad7500bb39c78f68645ecd`.
   The test CI checks the exact pinned upstream archive. If manually installed,
   verify the ZIP before use.
3. In the GFX viewer click **Configure FFDec…**, select `ffdec-cli.exe` from
   the installation, or `ffdec.jar` if a compatible Java runtime is installed.
   For the JAR, Java is an explicit dependency; optionally set
   `EVOLVE_GFX_JAVA` to the Java executable. Set `EVOLVE_GFX_FFDEC` to the
   trusted FFDec CLI/JAR path to configure noninteractively. No automatic
   update/download occurs. This choice is saved with manager settings.

FFDec runs in an isolated temporary layout with 28-second timeout, cancel,
size limits and bounded diagnostic output. External movie imports are permitted
only when an exact relative name is resolved unambiguously within the current
project or registered Asset Library build. ImportAssets URLs and paths
outside the scoped virtual folder are not fetched. Missing imports are shown
as diagnostics; font/image data from different builds are not substituted.
Cache entries include source and dependency hashes plus build and frame
selection.

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
