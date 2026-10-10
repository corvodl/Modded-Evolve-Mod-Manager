# Evolve Stage 2 Mod Manager

> **Required: install Evolve Stage 2 through the [Modded Evolve website](https://modded-evolve.com/) before using this manager.** Use the Modded Evolve client installer and download the game through that client. The Mod Manager does not provide game files; Steam/Legacy installations are not a substitute.

A Windows modding tool for **Evolve Stage 2** that helps you unpack game PAK archives, edit supported files, build mods, and test them with the Modded Evolve client—without having to run the individual tools yourself.

The manager has three main areas: **Make a Mod**, **Play & Restore**, and **Settings**. It prepares its own working PAKs from **your installed game**, so the normal application download does **not** include Evolve or its game files.

> [!IMPORTANT]
> **Experimental software — offline use only.** Using mods through this manager disables online play. A successfully rebuilt PAK does **not** guarantee that edits will function correctly in-game. If you bypass the online-play restriction and connect with modified game files, your account may be suspended or banned. **The project maintainers and contributors are not responsible for account bans or other consequences of bypassing this restriction.** This is an independent modding utility, not an official Evolve release.

## Features

- **One-time setup from an existing installation:** Reads the installed game, checks supported PAKs and `inject.dll`, and creates local game-file snapshots, custom signing keys, a matching injector, and mod-ready PAKs.
- **PAK browser and extractor:** Search available PAKs, unpack supported entries, and create separate editing projects.
- **DDS texture workflow:** Preview standalone DDS or compatible CryEngine split DDS streaming sets, export PNG images for external editing, and import a complete matching DDS with automatic backups. PNG files cannot be imported directly.
- **Experimental model inspection:** Read recognized CryTek chunk tables, export native `.cgf`, `.cga`, `.chr`, and `.skin` files, and attempt tightly restricted same-layout binary replacements. No built-in 3D viewer, Blender conversion, or skeleton editor.
- **Built-in file editor:** Browse and edit supported text files and exported CryXmlB (binary XML) values. Open project folders to use other editors when needed.
- **Review, build, and add mods:** Check changed files, rebuild and sign a PAK, and add it to the prepared mod set.
- **Guided game launch:** Prepare the modified files, open the normal client, and start a modded session.
- **Restore and undo:** Restore installed game files after playing; separately undo a mod added to the prepared set using its backup.
- **Game update workflow:** Generate a new base PAK set after the original game is updated or repaired.
- **Recovery checks:** Detect leftover prepared files or recovery backups rather than silently overwriting them.

## Requirements

**To use the Windows application:**

- Windows **64-bit**.
- An installed copy of **Evolve Stage 2** and the **Modded Evolve client**.
- Access to the game's `EvolveGame` folder (containing `bin64_SteamRetail/Evolve.exe`) and the normal client launcher executable (usually `ModdedEvolveLauncher.exe`).
- Plenty of free disk space. Initial setup may need **at least twice the total size of your installed PAKs, plus approximately 1 GiB**, in the manager's storage location. Preparing a game launch may require additional space on the game drive.

The Windows download already includes its required runtime. **You do not need to install Python, pip, or any build tools.**

## Download and install (Windows)

1. Open the **[latest GitHub release](https://github.com/corvodl/Modded-Evolve-Mod-Manager/releases/tag/Main)** and download **[EvolveModManager-Windows.zip](https://github.com/corvodl/Modded-Evolve-Mod-Manager/releases/download/Main/EvolveModManager-Windows.zip)** under **Assets**.
2. Right-click the downloaded ZIP and choose **Extract All**. Extract it to a **writable location**, such as `Documents\EvolveModManager`. Don't open the app directly from inside the ZIP or put the manager inside the game folder.
3. Open the extracted **`EvolveModManager`** folder and double-click **`EvolveModManager.exe`**.
4. Keep **`EvolveModWorker.exe`** and the **`_internal`** folder in the same extracted application folder. **Do not move or run the EXE by itself.**
5. On first launch, follow **First-time setup** below to select your Evolve installation and prepare the necessary game files.

**No Python installation or manual compiling is required.** Keep the entire extracted application folder together when moving it to another drive or computer.

### First-time setup

1. **Close Evolve and the Modded Evolve client.**
2. Click **Set Up Manager**.
3. Select your installed **`EvolveGame`** folder.
4. Select the normal **Modded Evolve client `.exe`** (not `Evolve.exe`).
5. Confirm setup and wait for it to finish. Large installations can take a while.
6. When setup completes, the manager displays the PAKs available for editing.

During setup, the manager verifies compatible original files, generates your own signing keys and matching `inject-custom.dll`, and prepares a separate local PAK set. **Initial setup does not replace the installed game PAKs.** The launch workflow temporarily swaps files when required and retains restoration information.

## Make your first mod

1. Open **Make a Mod** and choose a PAK from the list. Use the search field to narrow the list.
2. Click **Unpack PAK**. A new editing project is created and its files open in the editor.
3. Choose a supported file and edit a value. For binary XML, **change existing values only**—do not add, remove, or rearrange XML elements or attributes.
4. Click **Save File** in the editor, then **Review Changes** in the main window.
5. Click **Build Mod** to rebuild and sign the edited PAK.
6. Click **Add Mod** to add the resulting PAK to your prepared mod set. The manager keeps a backup of the previous staged version.

**Tip:** Start with a small change to one value and test it before making several edits. A successfully built PAK may still contain values the game cannot use.

### Play with your mod

1. Open **Play & Restore** and click **Play With Mods**.
2. Wait for the manager to report that the launcher is ready.
3. Press **Play** in the Modded Evolve client window that opens.
4. When finished, **close Evolve and the client**, then click **Restore Game Files**.

**Restore Game Files** puts the installed game files back into their original state after the modded launch. It does **not** remove your editing projects or the mods saved in the prepared set. To undo a mod you added to that set, use **Make a Mod → Undo Added Mod...** and select its dated backup folder.

## Supported editing and known limits

| Area | Support |
| --- | --- |
| Evolve Stage 2 signed/encrypted PAKs | Supported formats used by this manager, including supported CryPak entry methods **13 and 14** |
| CryXmlB (binary XML) | Export to editable XML and rebuild with checks intended to preserve the original structure |
| Text files | Built-in editing for `.xml`, `.txt`, `.cfg`, `.ini`, `.lua`, `.json`, and `.csv` when they contain readable UTF-8 text |
| Standalone DDS textures (`.dds`) | Preview and PNG export; replacement DDS must match the original dimensions, encoding, mip count and size |
| Split CryEngine DDS (`.dds.0` etc.) | Experimental: preview/recombine sets found together in one workspace; a compatible full DDS may be split back into parts and backed up |
| CryTek model files (`.cgf`, `.cga`, `.chr`, `.skin`) | Experimental metadata inspection and native export; reimport is restricted to recognized same-length, same-chunk-layout files |
| Materials (`.mtl`) and character parameter text (`.chrparams`) | Edit as UTF-8 text where readable |
| Other binary assets | May be extracted, but require an appropriate external editor; not all formats can be rebuilt successfully |
| Adding, deleting, or renaming files inside PAKs | **Not supported** by the current PAK writer |
| Structural XML changes | **Not supported**; modify existing values only |
| Online multiplayer/profile services | **Disabled while using mods**; bypassing the restriction risks account penalties |

The built-in text editor has a **5 MiB per-file limit**. CryXmlB edits involving shared strings or changed string lengths use experimental handling and need in-game testing. The tool is **not** a general-purpose CryEngine PAK editor and does not support every archive or injector layout.

## Editing split DDS textures and models (experimental)

**Split DDS texture workflow:** Unpack a PAK containing all of a texture's `.dds.0` through `.dds.N` parts. Choose any part in **Edit Files** to preview the reconstructed DDS, or use **Export PNG** to edit the top mip externally. Save your replacement as a **complete DDS** with the same compression format, dimensions, mip count, and total size, then use **Import DDS**. The manager backs up every fragment and redistributes replacement mipmaps to the existing parts. Incomplete streaming sets and unsupported compression/layouts are rejected. Only specific BC3/DXT5 and ATI2 samples have been checked; game rendering must be tested.

**Model workflow:** Unpack a model, then select a `.cgf`, `.cga`, `.chr`, or `.skin` file in **Edit Files**. **Export Model** saves the unchanged native binary. For recognized CryTek chunked models, **Import Model** allows an experimental replacement only when its file length, header, and chunk table match. An unrecognized model can still be exported, but not imported through this UI. This does **not** create Blender-ready models, render 3D previews, edit rigs, or guarantee that a modified model works in Evolve.

**Safety:** Keep backups and test a single change at a time. The editor's PAK writer replaces existing archive entries; it does not add new filenames. Offline-only restrictions still apply.

## Keeping your game and projects safe

- **Always close the game and client** before setup, building/installing staged mods, or restoring files.
- **Restore Game Files after each modded session.** Do not manually delete recovery backups, journals, or prepared files.
- If setup finds leftover `*.customkey-ready` files, it may offer to **preserve them under new names** before continuing. This requires your approval.
- If setup reports `*.customkey-original` backups or partially prepared files, **stop** and recover the previous setup first. Do not force setup or erase those files.
- The manager stores generated game snapshots, signing keys, and projects under its **`Data/`** folder. **Keep this folder** if you want to retain your mods and setup.
- **Do not publicly share `Data/`**, generated private keys, game PAKs, or full setup exports. The Windows release is designed to exclude these items.

## Updating after a game update

If the Modded Evolve client updates or repairs your installed game:

1. Close the game and client, and restore any active modded session.
2. Let the normal client finish updating or repairing the game, then close it again.
3. Go to **Play & Restore → Update Base PAKs...**.
4. Select the updated game installation and a separate destination with sufficient free space.
5. After the new set is created, unpack its PAKs and **reapply/review your edits**. Old editing projects are not automatically merged into updated PAKs.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| No PAKs are listed | Run **Set Up Manager**, then **Settings → Check Setup**. Confirm you selected the right `EvolveGame` folder. |
| Setup rejects your game or injector | Confirm you are using a supported Evolve Stage 2 installation. The tool intentionally refuses unfamiliar archive/signature or injector layouts. |
| “Untracked prepared files exist” | Use the current setup prompt to preserve old prepared files if offered. If original backups or partial files exist, restore or inspect the earlier setup before continuing. |
| Build Mod fails | Open **Show Activity Log**, review the error, and check that you changed only supported values and kept the original file structure. |
| Game fails to start or online services show an error | Restore original game files and test the unmodified client. Online play is disabled when using mods. |
| Restore is blocked | Close the game and launcher. Check the activity log and preserve all recovery files; do not delete them to bypass the safety check. |

Use **Show Activity Log** to see detailed steps and errors. When reporting a problem, include the relevant error message and what you were doing, but **do not share private keys or full game-file snapshots**.

## Disclaimer

**Offline use only.** Mods created or launched through this manager disable online play. This restriction is intentional.

If you bypass or work around this restriction to access online services while using modified game files, **you do so entirely at your own risk**. Such activity may violate server rules or terms of service and could result in account suspension, permanent bans, or other penalties. **The project maintainers and contributors are not responsible for account bans, lost access, or any other consequences resulting from attempts to bypass the online-play restriction.**

This is an **experimental, community-made tool** for users with their own installed game files. It is not affiliated with or endorsed by the Evolve developers or online service operators. It does not distribute Evolve or replace the Modded Evolve client. Back up important files before making changes.
