EVOLVE MOD MANAGER v1.0.2 - PORTABLE APP
==========================================
Build with Build-Windows-EXE.cmd, then share dist\EvolveModManager-Windows.zip.
Each recipient extracts the whole folder and opens EvolveModManager.exe.
Click Set Up Manager, select EvolveGame and the normal Modded Evolve client EXE.
The manager generates the signing keys, inject-custom.dll and custom-signed PAKs
locally by reading the original game installation; original game files are not
changed by setup. Preparation requires significant free disk space (at least
2x the size of installed PAKs plus roughly 1 GiB for setup alone).
The release includes required executables/runtime libraries, not game PAKs.
Existing custom keys and projects must stay in the recipient's Data folder.
Do not send that Data folder in public releases.
Private/offline testing; normal online services may reject modified assets.

Texture editing (standalone DDS only):
- Unpack a PAK, open Edit Files, and select a .dds file.
- Export PNG saves the full-size top mip for editing in an image editor.
- To replace a texture, encode your edit as a DDS with matching format, dimensions, mip levels, and total file size; use Import DDS.
- Review Changes, Build Mod, and Add Mod to apply the new texture.
- PNG cannot be imported directly; .dds.N streaming texture parts are not supported.

Split CryEngine DDS mip streaming (EXPERIMENTAL):
- Unpack the whole PAK: all .dds.0 through .dds.N pieces for the selected texture must be present in ONE editing workspace.
- In Edit Files, select any .dds.N part. The manager assembles the complete BCn/ATI DDS for preview and PNG export.
- Use an external image tool to export a complete DDS with the same block compression, dimensions, mip count and exact data size, then choose Import DDS.
- All original streaming pieces are backed up together. The manager will redistribute mips and refuse incompatible replacements.
- Only tested with two representative Evolve Stage 2 BC3/ATI2 sets. Backups and in-game validation are still required.

Experimental CryTek model tools:
- Select an existing .cgf/.cga/.chr/.skin from an unpacked PAK. Recognized CryTek chunked geometry displays structural information.
- Export Model saves native binary for an external tool (NOT an editable Blender or OBJ export).
- Import Model only accepts recognized same-size CryTek files with identical header and chunk table. Other model layouts are export-only.
- This is not a full model editor; skeletons, new geometry layout, Blender roundtrip and animations are unverified.
- Material .mtl and character .chrparams text files can be edited using the existing editor.

File Explorer tabs (v2.9 experimental):
- Files / Text: edit XML, CryXML and other readable text; Save File changes to the project.
- Images / Textures: select a complete DDS or a .dds.0 streaming set to preview, Export PNG, Import PNG, or Import DDS. The streaming set must include all fragments.
- Import PNG converts the edited PNG back into DXT1/DXT3/DXT5/ATI2/BC5 compressed DDS and creates mipmaps using Pillow. Image dimensions must match. Unsupported layouts are rejected, not silently changed. The conversion is lossy; check your textures in game.
- Models: inspect and Export Model as the original CryTek file, or experimentally Import Model with an identical chunk-table layout and file length. No full 3D viewer or Blender conversion exists.
- Review Changes, Build Mod and Add Mod after editing. Keep EditorBackups and test offline.

Note: Some .dds.0 files are complete single-mip DDS textures, even if no .dds.1 exists.
The Images tab can preview and import those files directly. Missing/unsupported streams display
a non-blocking explanation in the preview panel rather than a popup during selection.


SPACE-SAVING SETUP (OPTIONAL)
-----------------------------
During first-time Set Up Manager and Update Base PAKs, choose whether to keep a
second permanent copy of the game's original PAKs:
  YES: Keep a full original snapshot (extra disk use).
  NO:  Build signed PAKs directly from the installed game; do not store a
       duplicate permanent original snapshot (less disk use).
The tool verifies and never overwrites installed originals during setup.
This choice does NOT turn off the temporary .customkey-original recovery files
created during a live modded launch. These are rename-based swap backups, not
additional copies, and are required for safe automatic restoration.

Selective loading limitation: the custom injector contains a new RSA public
signing key. Consequently every signed/encrypted PAK that Evolve loads must
match that key; it is not safe to swap only PAKs whose gameplay values changed.
Plain unsigned ZIP PAKs are already left untouched. The manager could adopt
changed-only swaps later only if a verified dual-key loader becomes available.

A temporary .customkey-ready file is copied next to each original PAK for
prelaunch swapping, so the game drive still needs free space for those files.
The current setup option also does not delete earlier saved full snapshots.

Main window layout: drag the horizontal divider directly below the PAK search list
to give the list more or less room. Maximizing/resizing the main window
expands the list; the activity log remains in its separate window.

CryEngine skinned character models (Goliath sample):
- .skin/.skinm and .chr/.chrm companion model files can be inspected in Models.
- CrChF v7 chunk and mesh stream metadata (vertices/indices/LODs) is shown where available.
- .cdf/.animevents/.lmg/.bspace/.comb are XML text and appear in Files/Text.
- Import Model remains same-layout experimental binary replacement; Blender mesh round-trip is not supported.
- If exporting for external tools, keep each model beside its matching .skinm/.chrm companion.

Models tab: 3D model preview supports CrChF v7 .skinm mesh companions (including Goliath).
Select .skinm or its .skin file when the .skinm is present in the same extracted folder.
Drag to rotate, scroll to zoom, switch wireframe on/off, Reset view to recenter.
This is a read-only 3D preview; .chr/.chrm skeleton rigging and Blender export
are not yet visualized. The preview never modifies the PAK or model.

Multi-PAK asset extraction (experimental):
- In Make a Mod, Ctrl/Shift-click 2-30 staged PAKs and click Unpack Selected PAKs.
- Each signed PAK gets its own independent workspace. No conflicting files are merged.
- After extraction, select any of those PAKs and choose Edit Files to open its workspace.
- Under Models, select a model and click Find Model Textures to locate its material
  and referenced DDS/.dds.0 files across the batch collection. Missing/ambiguous
  resources are reported rather than automatically replaced.
- Material .tif references are checked against cooked .dds and .dds.0 names.
- Rebuild/Install ONE PAK at a time. Batch extraction does NOT automatically make
  the 3D preview textured if the extracted .mtl and referenced DDS are missing.
- Batch extraction can consume significant disk space. Originals remain unchanged.

Approximate textured model preview (v2.9.7 experimental):
- The Models tab reads CrChF v7 UV stream type 2 (2 float32 per vertex) and
  material subset assignments and maps Diffuse DDS textures through the .mtl file.
- Split DDS fragments must all be extracted together, within their original PAK
  workspaces, or placed together in a user-selected flat texture folder.
- Select a mesh (.skinm, or its .skin with mesh companion) and check Textures.
- If materials are not found automatically across the unpacked batch, extract a
  texture archive to a directory and click Choose Texture Folder in Models.
- Rotation/zoom and wireframe remain available. Textured rendering runs in a
  background thread to keep the UI responsive. It is an APPROXIMATION of the
  diffuse material, not original CryEngine shaders (normal/specular/damage/SSS).
- This preview does not edit any material, texture, PAK, or model bytes.

Activity Log visibility:
- The Activity Log opens automatically when the manager opens.
- Closing it is optional; it reopens whenever you start a manager operation,
  so progress and errors are visible even if the main window is busy.

Automatic model texture lookup:
- Unpack the model/material PAK and the texture PAK using the same manager setup.
- Both an extracted batch and separate complete workspaces inside Settings > Editing projects are scanned.
- Re-select the model in the Models tab to refresh preview after extracting another PAK.
- If two workspaces contain the same texture path, lookup stops as ambiguous rather than guessing.
- The Credits tab lists @CorvoDL, the GitHub repository and the Modded Evolve website.
- Install Evolve Stage 2 through https://modded-evolve.com/ before using the manager.

Batch PAK editor: Use Ctrl/Shift to choose 2-30 PAKs, click Unpack Selected PAKs,
and wait for completion. Edit Files then shows an Unpacked PAKs list at the top.
Click an archive name to switch its Files/Text, Images and Models trees in the SAME
window. Every workspace remains separate; Build Mod works on the currently selected
archive only. The editor prompts before leaving unsaved text changes.

Add Mod after first setup:
- Fresh or restored setups may not yet have any per-PAK swap journal records.
- Build Mod and Add Mod now work before the first Play with Mods action.
- Mod installation changes staged PAKs only; installed game files are untouched.
- Play with Mods generates the prepared swap journal when necessary.
- Existing prepared swaps still require verified matching records and backups.
- If recovery files are detected, restore your game instead of deleting the journal.

Model texture lookup: repeated extractions of identical .mtl/DDS assets no longer
cause false ambiguity. The model's own PAK material is used first. Different
unrelated material versions remain ambiguous (see Find Model Textures for paths).
If you have made multiple copies of the same PAK project, removing outdated
project copies from the Projects folder can help. Never remove live swap backups.

3D preview performance:
- Mouse drag and wheel zoom use a quick untextured shaded preview while moving.
- When the mouse stops, the selected model renders full UV materials again.
- Quality: Fast, Balanced (default), Detailed; lower quality cuts render resolution.
- Old background frames cancel when you turn or select a new mesh, so they do not
  block the new view or replace it with outdated textures.
- Wireframe and Textures switches remain available, with no change to game files.
- This is still a software rasterizer; real-time textured 3D during dragging
  would require a separate hardware-accelerated renderer.

GPU model viewer (experimental, Windows):
- Models tab uses hardware OpenGL when a compatible graphics driver is present.
- Fully textured meshes rotate and zoom live with GPU depth testing and indexed triangles.
- Renderer selector: GPU (Auto), or Software for unsupported drivers.
- On Microsoft GDI Generic / Basic Render Driver or failed GPU initialization, the existing
  slower software viewer is used safely instead.
- Fast/Balanced/Detailed control texture resolution for the GPU viewer.
- Driver-based OpenGL needs a supported GPU driver; WSL, remote desktop, headless VMs
  and some older GPUs may not have a usable accelerated OpenGL context.
- Rendering is read-only. It is approximate diffuse shading, not CryEngine shader parity.

File context menus (v2.9.15):
- Right-click a file in Files/Text, Images/Textures or Models to see actions for that file.
- Text/XML: Import/Replace File, Save, Export File and Windows Explorer commands.
  CryXML still prohibits structural edits. The original extracted file is backed up.
- Images: Import PNG as DDS, Import compatible DDS, Export PNG, Export raw file.
  Split DDS fragment imports keep their existing all-parts validation and backups.
- Models: Native import (experimental), native export, find texture references and reveal.
- Folders: Show in File Explorer, Copy Game Folder Path, Expand/Collapse.
- Restore Extracted Original is enabled only when an EditorBackups copy has the EXACT
  checksum of the original extracted file from the manifest. Unsupported/multi-part
  split streaming DDS restores are disabled until atomic group restoration exists.
- Replace unknown binary files only after explicit confirmation; game compatibility
  remains unverified. No action writes to the installed EvolveGame directory.
- The context menu also works with Shift+F10 for selected files/folders.

UI: Help (?) contains detailed instructions; Activity shows full logs. The main tabs use compact controls.

UPDATES
Main Windows builds check GitHub for newer verified releases. In Settings, use Check for Updates to download and apply an update. Close Evolve before updating. The updater replaces only application files, preserves Data (projects, PAKs, keys, backups, settings), and keeps a recovery backup if replacement fails. Updates are offered only after the matching main commit has passed Windows packaging. Experimental builds do not automatically switch to main.

Import Modified PAK: from Make a Mod, choose Import Modified PAK. The file must be signed with this setup's current RSA key and have the same basename and archive entry layout. The manager checks and backs up the current staged PAK, leaving the installed game alone.

UPDATING: A verified new Main release prompts automatically when the manager starts. Approve it to see download/extraction progress, then installation progress in a separate window. The manager restarts when the update finishes. The Data folder is not replaced.

MAIN GAME FILE BROWSER (v2.10.3)
--------------------------------
Columns: Folder | File | Type / Project | Size, with Size right-aligned.
Use the filter for All files, PAK archives, or loose Game files.
Loose files are discovered from the installed game folder recorded by setup.
Use Edit Files (or double-click) to create an editable copy in
Data/Projects/LooseGameFiles. Text can be edited internally and binary files
with external tools. The installed game is NEVER overwritten when saving a
loose file. Loose-file copies cannot be staged as signed PAK mods.

UPDATER RECOVERY (v2.10.4)
-------------------------
The separate installer now logs progress even if its window fails, confirms
that the replacement GUI is responsive before removing the application backup,
and restores the old application if installation or startup fails.
Troubleshooting logs: %LOCALAPPDATA%\EvolveModManagerUpdates\<id>\update.log
Preserve .update-backup-* folders if rollback is incomplete. Data is untouched.


ORPHANED GAME SWAP RECOVERY (v2.10.5)
---------------------------------------
Restore Game Files inspects the actual game folder for .customkey-original
backups even if the launch journal says 'missing', 'prepared', or 'restored'.
When backups exist without a usable journal, the manager asks for confirmation
and uses a separate recovery worker. It preserves every currently installed
modified file alongside its original with a unique .customkey-recovery-mod-*
name, restores the original PAK/inject.dll by same-drive rename, and keeps a
per-file recovery manifest in Data/RecoveryLogs. A failed rename stops safely;
inspect the activity log and recovery manifest before retrying. The launcher
and game must be closed. Recovery does not delete mods or Data/Projects.

PAK BROWSER DESCRIPTIONS AND RIGHT-CLICK (v2.10.6)
--------------------------------------------------
The main Make a Mod list shows Folder | Description | File | Type / Project | Size.
The Description column is guessed from the PAK filename (NOT a contents scan).
For example, characters_monsters_goliath_data.pak is 'Goliath Models' and
characters_monsters_goliath_ts.pak is 'Goliath Textures'. The actual PAK path
and filename remain unchanged. Description text is searchable and sortable.
Right-click a PAK to unpack, inspect metadata/locations, reveal its original
installed-game PAK or the separate staged PAK in Explorer, copy paths or open
existing unpacked files. Right-click on a selected multi-PAK group to unpack
all selected archives using the regular guarded batch workflow.
All file-location actions are read-only and do not change installed game files.

OFFICIAL v1.0.0 GUI
-------------------
The manager opens to the Instructions tab with a required first step:
install Evolve Stage 2 through the normal Modded Evolve client available at
https://modded-evolve.com/ . The manager does not include the game itself.
The UI tabs are Instructions, Modding, Play & Restore, Settings, Credits.
An icon appears beside EVOLVE / MOD MANAGER, and the bottom-right footer shows
v1.0.0 (read from the packaged VERSION.txt).
Right-click the Modding PAK list to inspect/unpack/reveal/copy source paths.
Original filename and inferred description are separate columns.
Keep Data/ intact when updating, including local keys and recovery journals.

MAINTENANCE RELEASE v1.0.1
-------------------------
First boot always displays the Instructions tab. Neither fresh setups nor
unconfigured exported bundles open folder-selection dialogs automatically.
Click Set Up Manager on Instructions after reading the game prerequisite;
that button handles either local setup or connecting an exported full bundle.
The redundant top-right Set Up Manager and Settings > Install Update buttons
are removed. Settings > Check for Updates discovers verified releases and asks
whether to install immediately when an update is available.
Existing initialized Main builds still check for updates on startup.
The tabs, spacing, fonts and archive table have been refreshed; Instructions
now uses a scrollable card layout on smaller displays. Footer shows v1.0.1.

VISUAL UPDATE v1.0.2
--------------------
The user's supplied icon is the black claw/sword mark on a red background.
Both the header PNG and taskbar/Windows EXE icon use this artwork without blue.
The build regenerates 16/32/48/256 ICO frames from the tracked hunt.png.
Native Tk notebook tabs and action buttons have antialiased rounded capsules,
and onboarding/Play & Restore/Credits use rounded charcoal panels.
The established offline modding, safe PAK handling and recovery behavior is unchanged.

COMPACT CONTROLS v1.0.3
------------------------
The actual compiled Windows v1.0.2 screenshot showed oversized controls and
notched/blank card rendering. v1.0.3 shrinks the native ttk pill element
images, border slices and widget padding, reduces the Play button extra pady,
and draws stable antialiased rounded cards. A Windows executable screenshot is
captured as a tested release artifact. All game/mod/restore logic is unchanged.

ON-DEMAND NAVIGATION v1.0.4
---------------------------
The top notebook tabs are hidden to maximize workspace width. Select the
compact Menu button beside the Evolve logo to show the five navigation
sections in a temporary left panel. Choosing a section or pressing Escape
closes the panel. Existing edit, play, restore, update, and setup behavior
remains unchanged. The menu is collapsed when the program starts.
