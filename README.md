# Evolve Stage 2 Mod Manager — v1.0.0

A portable Windows manager for inspecting, editing and rebuilding Evolve **Stage 2** PAKs, with guided offline launching and recovery of original game files.

> **Required: Install Evolve Stage 2 through the [Modded Evolve website](https://modded-evolve.com/)** and its normal client **before** using this manager. The Mod Manager does **not** download or install the game and does **not** include game assets. A Steam or Evolve Legacy installation is not a substitute for the client-installed Stage 2 game.

**Version:** 1.0.0 · **Platform:** Windows x64 · **Distribution:** self-contained portable ZIP · **Use:** private/offline mod testing

## Download and install

1. Download **EvolveModManager-Windows.zip** from the [Main Windows release](https://github.com/corvodl/Modded-Evolve-Mod-Manager/releases/tag/Main) and extract the **entire** `EvolveModManager` folder to a writable location.
2. Run `EvolveModManager.exe`. The **Instructions** tab is shown first and provides a direct link to the required Modded Evolve website.
3. After the normal client has installed Evolve Stage 2, close the game and client. Click **Set Up Manager**, select the client-installed **EvolveGame** folder, and select the normal Modded Evolve client executable.
4. The manager builds local signing resources, an injector and staged custom-signed PAKs from **your installed game files**. It does not alter the original installed PAKs during setup. Allow substantial free disk space for staging; the amount depends on the game files and original-snapshot option.
5. Open **Modding** to edit archives. Use **Play & Restore** to play privately/offline and restore originals afterward.

The portable Windows release bundles Python and its required application dependencies. A separate Python installation is only necessary to **build from source**.

**When updating an existing installation:** Preserve the `Data` directory. It contains projects, locally generated keys, staged mods, settings and recovery logs. Never distribute your private `Data` folder or signing key as part of the public app-only ZIP.

## Interface and getting started (v1.0.0)

| Tab | What it does |
| --- | --- |
| **Instructions** | Required Modded Evolve installation, first-run setup, modding and restoration walkthrough |
| **Modding** | Archive browser, unpacking, extracted-file editing, building and adding mods |
| **Play & Restore** | Guided offline launch, swap state, and restoration/recovery of original game files |
| **Settings** | Projects, stage and launch-helper folders; maintenance and advanced workflows |
| **Credits** | Project links and attribution |

The header shows the existing project icon beside **EVOLVE / MOD MANAGER**. The bottom-right footer displays **v1.0.0**, read from the packaged `VERSION.txt` file. The UI keeps its black/red theme; verbose activity remains in the optional **Activity** window.

### Readable PAK browser and right-click tools

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

Version **1.0.0** marks the application's first official release, **not** a claim that all mod formats are fully supported. Successful signatures do not guarantee valid gameplay. Do not rename signed archives arbitrarily, change unsupported model layouts or discard backups. GPU models are approximate visualizations, not the game renderer. Use mods for private/offline testing only.

## Credits

Project by **@CorvoDL**. [Source repository](https://github.com/corvodl/Modded-Evolve-Mod-Manager) · [Required Modded Evolve client](https://modded-evolve.com/).
