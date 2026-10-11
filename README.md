# Evolve Stage 2 Mod Manager — v1.1.6

A portable Windows manager for inspecting, editing and rebuilding Evolve **Stage 2** PAKs, with guided offline launching and recovery of original game files.

> **Required: Install Evolve Stage 2 through the [Modded Evolve website](https://modded-evolve.com/)** and its normal client **before** using this manager. The Mod Manager does **not** download or install the game and does **not** include game assets. A Steam or Evolve Legacy installation is not a substitute for the client-installed Stage 2 game.

**Version:** 1.1.6 · **Platform:** Windows x64 · **Distribution:** self-contained portable ZIP · **Use:** private/offline mod testing

## What's new in v1.1.6

- Export supported CryEngine models and folders to Blender-ready GLB files.
- Models now use GPU preview by default when supported.

## What's new in v1.1.5

- Added mod profiles for organizing and switching signed PAK changes.
- Added conflict checks and reversible profile staging.

## What's new in v1.1.4

- Info buttons now open help when clicked.
- Added a What's New tab with brief version notes.

## What's new in v1.1.3

- Added contextual help and an inline launch-status refresh button.
- Fixed the Play & Restore menu label.

## What's new in v1.1.2

- Organized Settings and added display, activity and snapshot preferences.
- Added advance mod preparation and reduced redundant file checks.

## What's new in v1.1.1

- Fixed unreadable Windows fonts and strengthened screenshot checks.

## What's new in v1.1.0

- Moved the manager and editors to Qt with a smooth overlay menu.
- Added a native OpenGL model viewer.

## What's new in v1.0.7

- Manual update checks can offer experimental builds; automatic checks stay stable-only.

## What's new in v1.0.6

- Added a sliding menu and optional desktop shortcut.

## What's new in v1.0.5

- Refined the Menu button and simplified Help labels.

## What's new in v1.0.4

- Moved navigation into a collapsible sidebar for more workspace.

## What's new in v1.0.3

- Fixed Windows control sizing and card backgrounds.

## What's new in v1.0.2

- Updated app icons, colors and control styling.

## What's new in v1.0.1

- Added instructions-first setup and simplified update controls.

## Download and install

1. Download **EvolveModManager-Windows.zip** from the [Main Windows release](https://github.com/corvodl/Modded-Evolve-Mod-Manager/releases/tag/Main) and extract the **entire** `EvolveModManager` folder to a writable location.
2. Run `EvolveModManager.exe`. The **Instructions** tab is shown first with a link to the required Modded Evolve website. No installation folder picker opens until you click Set Up Manager.
3. After the normal client has installed Evolve Stage 2, close the game and client. Click **Set Up Manager**, select the client-installed **EvolveGame** folder, and select the normal Modded Evolve client executable.
4. The manager builds local signing resources, an injector and staged custom-signed PAKs from **your installed game files**. It does not alter the original installed PAKs during setup. Allow substantial free disk space for staging; the amount depends on the game files and original-snapshot option.
5. Open **Modding** to edit archives. Use **Play & Restore** to play privately/offline and restore originals afterward.

The portable Windows release bundles Python and its required application dependencies. A separate Python installation is only necessary to **build from source**.

**When updating an existing installation:** Preserve the `Data` directory. It contains projects, locally generated keys, staged mods, settings and recovery logs. Never distribute your private `Data` folder or signing key as part of the public app-only ZIP.

## Interface and getting started (v1.0.4)

| Tab | What it does |
| --- | --- |
| **Instructions** | Required Modded Evolve installation, first-run setup, modding and restoration walkthrough |
| **Modding** | Archive browser, unpacking, extracted-file editing, building and adding mods |
| **Play & Restore** | Guided offline launch, swap state, and restoration/recovery of original game files |
| **Settings** | Projects, stage and launch-helper folders; maintenance and advanced workflows |
| **Credits** | Project links and attribution |

### Blender model export and GPU preview

In **Modding**, choose **Export Model Folder** to convert supported unpacked CryEngine `.skinm`, `.chrm`, and `.cgf` render meshes from a selected folder into separate Blender-compatible `.glb` files in a different folder. Unsupported encodings are skipped with a report; original files remain unchanged.

In **Edit Files → Models**, choose **Export for Blender (.glb)** for the currently selected preview mesh, including meshes resolved from a companion file. The native CryEngine export and same-layout native import actions remain available.

On Windows with a working OpenGL driver, the model pane now tries the embedded GPU viewer by default. It falls back to software preview on unsupported platforms or GL initialization errors; **Open GPU Viewer** retains the dedicated controls. GLB exports preserve mesh geometry and available UVs/material slots but do **not** reconstruct bones, rig weights, animation, or missing DDS textures. Arbitrary Blender meshes cannot be imported back into Evolve's native model format through this exporter.

### Mod Profiles & Library

In **Modding**, click **Mod Profiles** to create named configurations such as Balance, Textures, or Testing. Add existing signed PAKs built by the manager or imported from your projects. Each profile keeps a reference to its original mod file; it does not bundle game assets.

Check the mods you want active, then choose **Apply selected profile**. Two enabled mods cannot target the same PAK; resolve the conflict by combining edits in one workspace before applying. Profiles are staged through the existing signed-archive verification and per-PAK recovery backups. Close the game and client first; profile staging never replaces live installed game files directly.

Use **Restore prior staged state** to undo changes managed by Profiles. This restores the state that was staged before Profiles first changed that archive; it does not forcibly discard other separately installed mods. If a staging operation is interrupted, inspect Activity and resolve the preserved backup journal before another modded launch. Signed PAK files remain on disk if you delete a profile.

### Readable PAK browser and right-click tools

Select **☰ Menu** at the top-left to open the section list, then choose the page you want. The menu automatically closes so the content retains the full window width.

The main list is arranged **Folder → Description → File → Type / Project → Size**. Descriptions are **inferred from filenames**, not verified by analyzing archive contents. The actual signed filenames remain unchanged.

| Description | Filename |
| --- | --- |
| Goliath Models | `characters_monsters_goliath_data.pak` |
| Goliath Textures | `characters_monsters_goliath_ts.pak` |
| Caira Textures | `characters_hunters_merc_caira_ts.pak` |
| Game Libraries | `libs.pak` |
| Interface Data | `UI_Data.pak` |
| Hunters Audio | `sounds_hunters.pak` |

Search by name **or** description; click table headings to sort. Ctrl/Shift-click selects multiple PAKs, and the list supports **All files**, **PAK archives** and **Game files** filters.

**Right-click a PAK** to unpack, inspect file details and paths, reveal its installed-game or staged copy in File Explorer, copy either path or open already unpacked files. Multiple selected PAKs offer **Unpack Selected PAKs**. Inspection and reveal actions are read-only: they do not modify installed game archives.

### Unpack, edit, build and add a mod

1. In **Modding**, select a PAK and click **Unpack**, or select several and choose **Unpack Selected PAKs**. Each archive stays in its own workspace even when unpacked together.
2. Open **Edit Files**. The editor offers **Files / Text**, **Images / Textures** and **Models**. For batch extraction, switch between the different PAKs using the extracted-archive selector.
3. Make changes, then **Review** them, **Build Mod**, and **Add Mod**. Each modified PAK is built and staged separately; the tool does not merge unrelated PAKs.
4. Right-click an extracted file for supported import/replace, export, reveal, copy-path, or verified restore actions. Original extracted files are backed up where supported.

**XML and text:** Edit readable XML, CryXML and configuration content in the workspace. Structural CryXML changes and unexpected game values may be incompatible; always review and test.

**DDS textures:** Preview textures, export PNGs and import compatible DDS or PNG edits for supported BCn/DXT formats. CryEngine streaming textures (`.dds.0`, `.dds.1`, etc.) require all fragments from the same texture to be available together. Dimension, format, mip and size constraints are validated. PNG compression can be lossy.

**Model tools:** Inspect supported `.cgf`, `.cga`, `.chr` and `.skin` geometry, export native files and experiment with same-layout replacements. The GPU/Auto OpenGL preview can display approximate UV/diffuse textures; a software fallback is available. It is not a full CryEngine renderer or Blender/OBJ round-trip, and animations, rigging and complex materials are not reconstructed.

**Cross-PAK textures:** If models, materials and textures reside in different archives, unpack them into separate workspaces within the manager's Projects location. The model preview can find compatible extracted diffuse textures from other workspaces. Ambiguous or absent matches require inspection, not guesses.

### Loose files and external PAKs

The game browser lists certain loose editable files from the installed game. Choosing **Edit Files** creates a **private copy** under `Data/Projects/LooseGameFiles`. Editing it does not overwrite the installed game file or automatically place it in a signed PAK.

**Import Modified PAK** accepts a previously edited archive only if it matches the selected signed filename, the current local signing key and the expected archive-entry structure. The guarded importer validates these properties before adding it to staged mods; it does not directly install a file into the live game.

## Playing with mods and restoring originals

Go to **Play & Restore → Play With Mods** for private/offline testing. Follow the guided launcher steps, wait until the launcher is ready, and then press Play in the normal Modded Evolve client. Modded game files may be incompatible with normal online/profile services.

**When finished:** Close `Evolve.exe` and `ModdedEvolveLauncher.exe`, then click **Restore Game Files**. The usual restore relies on a recorded swap state and retains prepared mod copies for later use.

**Missing-journal recovery:** If `*.customkey-original` backups are present but the swap journal says “Nothing to restore,” the manager checks the actual game folder and offers an independent recovery option. It preserves the currently installed modified files as uniquely named `*.customkey-recovery-mod-*` files, restores the original archives and `inject.dll` using same-drive renames, and saves a recovery manifest to `Data/RecoveryLogs`. Recovery stops on unsafe or failed file operations. Do **not** delete original backups or override recovery warnings.

For ordinary online play, restore the original game state and use the normal client to verify or repair game files if necessary.

## Portable updates and data safety

- **Verified automatic updater:** Eligible Main-channel builds check GitHub for the latest verified commit at startup and offer an optional update. The update displays download/preparation progress and restarts the manager after applying the package.
- **Failure recovery:** The Windows installer records progress and attempts rollback to the previous application version if installation or the updated GUI startup fails. Logs are retained under `%LOCALAPPDATA%\\EvolveModManagerUpdates`.
- **Data preservation:** The updater replaces **application files**, not `Data`, user projects, signing keys, game PAKs or existing recovery records.
- **Full setup export:** Private complete-bundle exports may include local staging and keys and can be large. The public app-only release excludes original PAKs, generated private keys and personal game files.
- **Activity and inspection:** Detailed logs are hidden at startup but open when tasks run or when the **Activity** button is selected.

## Build from source

The repository is a **source/build kit**. On Windows, install **Python 3.11 x64**, then run `Build-Windows-EXE.cmd`. It configures an isolated environment, runs unit and integration tests, freezes both `EvolveModManager.exe` and `EvolveModWorker.exe`, performs bundled dependency checks and creates `dist/EvolveModManager-Windows.zip`. Some native packages may require Microsoft C++ Build Tools **on the build machine**.

The [Windows CI workflow](https://github.com/corvodl/Modded-Evolve-Mod-Manager/actions/workflows/windows-release.yml) publishes the tested Main-channel release, including a SHA-256 update manifest tied to the source commit. Only verified assets matching the latest `main` revision are offered as automatic updates.

## Limitations

Version **1.0.1** marks the application's first official release, **not** a claim that all mod formats are fully supported. Successful signatures do not guarantee valid gameplay. Do not rename signed archives arbitrarily, change unsupported model layouts or discard backups. GPU models are approximate visualizations, not the game renderer. Use mods for private/offline testing only.

## Credits

Project by **@CorvoDL**. [Source repository](https://github.com/corvodl/Modded-Evolve-Mod-Manager) · [Required Modded Evolve client](https://modded-evolve.com/).
