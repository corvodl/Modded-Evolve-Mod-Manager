> **REQUIRED BEFORE USING THIS MANAGER:** Install **Evolve Stage 2 through the [Modded Evolve website](https://modded-evolve.com/)** using its official client installer. This manager requires the game files installed by that client; it does **not** download or install Evolve itself. A separate Steam or Legacy installation is not a substitute.

# Evolve Stage 2 Mod Manager v2.9.15 (experimental)

A portable Windows modding manager for Evolve Stage 2, with PAK editing, a three-tab asset explorer, backup/restore, and offline-only modded launching.

## New in this experimental build

- **Right-click file actions:** Context menus in Files/Text, Images/Textures and Models include preview/edit, safe replacement or import, export, Show in File Explorer, Open with Default App, and Copy Game File Path. Folders offer reveal and expand/collapse actions.
- **Verified restore:** Restore Extracted Original is enabled only if an EditorBackups copy exactly matches the original extraction checksum. Whole-stream split DDS restore is intentionally not provided via the right-click menu.
- **GPU preview:** Supported Windows hardware drivers provide OpenGL-based model rotation and diffuse texture rendering, with a Software fallback.
- **Multi-PAK editor view:** After clicking **Unpack Selected PAKs**, the Edit Files window lists every archive in the completed batch above the Files, Images, and Models tabs. Click either archive to browse and edit its own files, without closing the editor. Each archive retains an independent workspace and Build Mod target. Unsaved text prompts before switching.
- **UV diffuse material preview:** The Models tab now renders supported CryEngine CrChF meshes using their UV coordinates and material-specific diffuse textures. Texture rendering happens on a background thread; rotate, zoom, and toggle wireframe as before.
- **Automatic cross-PAK textures:** Extract model/material and texture PAKs as a batch or separately into the same manager Projects folder. Selecting a model searches verified extracted workspaces automatically; duplicate paths are reported instead of guessed.
- **Loose texture folder (optional):** In Models, use **Choose Texture Folder** only when your textures are outside the extracted manager Projects directory.
- **Activity Log:** Stays closed at startup for a cleaner workspace; opens when an operation runs and whenever you click Activity.

## Viewing files from two unpacked PAKs

1. Ctrl-click or Shift-click the PAKs in **Make a Mod**.
2. Click **Unpack Selected PAKs** (not **Unpack PAK**), and wait until the Activity Log reports batch completion.
3. **Edit Files** opens with an **Unpacked PAKs** list at the top. Both selected PAK names appear there. Click either PAK to switch between their file trees.
4. **Review Changes → Build Mod** affects only the active PAK selected in the editor; repeat separately for each PAK that you changed.

The manager does not merge PAKs. For automatic textured model preview, the other unpacked PAKs are searched behind the scenes.

## How to preview Goliath with textures

1. Unpack the PAKs containing `goliath1.skinm` (or `goliath1.skin`), `goliath1.mtl`, and its DDS textures. Keep each PAK in its own workspace.
2. Open **Edit Files → Models**, then select the Goliath mesh. The manager searches previously unpacked, compatible PAK workspaces for the materials and DDS textures automatically; **Textures** applies the diffuse material when found. Re-select the model after unpacking additional PAKs.
3. If the textures are in a separate ZIP, extract them into a folder and choose **Choose Texture Folder** from the Models tab. The manager does not bundle original game assets or textures.
4. Rotate with the mouse, scroll to zoom, toggle **Wireframe** to inspect geometry, or use **Reset view**.

**Rendering limitations:** This is an approximate, untextured-or-diffuse-only 3D preview, not full CryEngine rendering. Damage blends, advanced normals, specular response, emissive/glow, subsurface scattering, rigging, and animation playback are not reproduced. Unsupported mesh/texture formats gracefully fall back to the untextured preview.

**Offline only:** Modded game sessions disable online play. If you circumvent that restriction, account suspensions or bans are your responsibility; the maintainers are not responsible for them.

This archive is a **Windows build kit**, not a precompiled EXE. Run `Build-Windows-EXE.cmd` with Python 3.11 x64 to create the portable Windows application; the resulting app bundles Python and its dependencies. The public GitHub release remains separate until verification.

## Credits

- **Discord:** @CorvoDL
- **GitHub:** https://github.com/corvodl/Modded-Evolve-Mod-Manager
- **Modded Evolve / required game installation:** https://modded-evolve.com/

## Hardware-accelerated 3D preview (experimental)

The Models tab defaults to **GPU (Auto)** on Windows. If an accelerated OpenGL driver is
available, it uses indexed GPU rendering and real-time diffuse textures while rotating and
zooming the mesh. **Renderer → Software** switches back to the original CPU preview. On
systems with no accelerated OpenGL driver (including some virtual machines and remote
desktops), the manager automatically falls back to software without affecting game files.

This is a read-only material approximation: no normal-map shading, CryEngine-specific
materials, animation, or edited-model conversion is implied.

### Streamlined interface (experimental 2.9.15)

A black/red modernized layout keeps Make a Mod, Play & Restore, Settings and Credits uncluttered. Click **Help ?** in the manager or editor for full instructions; **Activity** opens the detailed log. The log stays closed on initial startup and opens when operations begin. All existing PAK, backup and recovery workflows are unchanged.

## Importing an existing modified PAK

On **Make a Mod**, click **Import Modified PAK…**, choose the edited `.pak`, and confirm its matching staged filename. The manager verifies the archive's RSA signature against your **current local signing key**, exact archive entry order and count, and that some entries changed. A separate original-signed or differently keyed archive is not automatically compatible; it must first be rebuilt and signed with this manager. Only after verification does the existing guarded Add Mod workflow stage the PAK and save an undo backup. **Import never modifies installed game files.** The PAK browser now shows separate name, folder and unpacked-project columns with searchable, resizable rows.
