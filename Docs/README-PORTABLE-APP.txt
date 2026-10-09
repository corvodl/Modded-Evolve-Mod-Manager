EVOLVE MOD MANAGER - PORTABLE APP
================================
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
This is an untextured, read-only 3D preview; .chr/.chrm skeleton rigging and Blender export
are not yet visualized. The preview never modifies the PAK or model.
