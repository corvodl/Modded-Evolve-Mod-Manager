# Evolve Stage 2 Mod Manager

A Windows modding tool for **Evolve Stage 2** that helps you unpack game PAK archives, edit supported files, build mods, and test them with the Modded Evolve client—without having to run the individual tools yourself.

The manager has three main areas: **Make a Mod**, **Play & Restore**, and **Settings**. It prepares its own working PAKs from **your installed game**, so the normal application download does **not** include Evolve or its game files.

> [!IMPORTANT]
> **Experimental software — offline use only.** Using mods through this manager disables online play. A successfully rebuilt PAK does **not** guarantee that edits will function correctly in-game. If you bypass the online-play restriction and connect with modified game files, your account may be suspended or banned. **The project maintainers and contributors are not responsible for account bans or other consequences of bypassing this restriction.** This is an independent modding utility, not an official Evolve release.

## Features

- **One-time setup from an existing installation:** Reads the installed game, checks supported PAKs and `inject.dll`, and creates local game-file snapshots, custom signing keys, a matching injector, and mod-ready PAKs.
- **PAK browser and extractor:** Search available PAKs, unpack supported entries, and create separate editing projects.
- **Built-in file editor:** Browse and edit supported text files and exported CryXmlB (binary XML) values. Open project folders to use other editors when needed.
- **Review, build, and add mods:** Check changed files, rebuild and sign a PAK, and add it to the prepared mod set.
- **Guided game launch:** Prepare the modified files, open the normal client, and start a modded session.
- **Restore and undo:** Restore installed game files after playing; separately undo a mod added to the prepared set using its backup.
- **Game update workflow:** Generate a new base PAK set after the original game is updated or repaired.
- **Recovery checks:** Detect leftover prepared files or recovery backups rather than silently overwriting them.

## Requirements

**To use the built Windows application:**

- Windows **64-bit**.
- An installed copy of **Evolve Stage 2** and the **Modded Evolve client**.
- Access to the game's `EvolveGame` folder (containing `bin64_SteamRetail/Evolve.exe`) and the normal client launcher executable (usually `ModdedEvolveLauncher.exe`).
- Plenty of free disk space. Initial setup may need **at least twice the total size of your installed PAKs, plus approximately 1 GiB**, in the manager's storage location. Preparing a game launch may require additional space on the game drive.

Players using the **built application** do **not** need to install Python or its packages separately.

## Install the manager

1. Download **`EvolveModManager-Windows.zip`** from a published release, if one is available. If the project currently provides only the source/build kit, see [Building from source](#building-from-source).
2. Extract the entire ZIP to a **writable folder**, such as `Documents/EvolveModManager`. Do not run it directly from the ZIP or place it inside the game folder.
3. Open **`EvolveModManager.exe`**. Keep `EvolveModWorker.exe` and the `_internal` folder alongside it; they are required parts of the application.

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
| Other binary assets | May be extracted, but require an appropriate external editor; not all formats can be rebuilt successfully |
| Adding, deleting, or renaming files inside PAKs | **Not supported** by the current PAK writer |
| Structural XML changes | **Not supported**; modify existing values only |
| Online multiplayer/profile services | **Disabled while using mods**; bypassing the restriction risks account penalties |

The built-in text editor has a **5 MiB per-file limit**. CryXmlB edits involving shared strings or changed string lengths use experimental handling and need in-game testing. The tool is **not** a general-purpose CryEngine PAK editor and does not support every archive or injector layout.

## Keeping your game and projects safe

- **Always close the game and client** before setup, building/installing staged mods, or restoring files.
- **Restore Game Files after each modded session.** Do not manually delete recovery backups, journals, or prepared files.
- If setup finds leftover `*.customkey-ready` files, it may offer to **preserve them under new names** before continuing. This requires your approval.
- If setup reports `*.customkey-original` backups or partially prepared files, **stop** and recover the previous setup first. Do not force setup or erase those files.
- The manager stores generated game snapshots, signing keys, and projects under its **`Data/`** folder. **Keep this folder** if you want to retain your mods and setup.
- **Do not publish `Data/`**, generated private keys, game PAKs, or full setup exports in the public source repository. The normal app-only release is designed to exclude these items.

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

## Building from source

The repository's **build kit is not a precompiled Windows EXE**. To produce the portable app:

1. Use **Windows x64** with **Python 3.11 x64** installed and available through the `py` launcher.
2. Download or clone the source/build kit and extract it to a writable directory.
3. Run **`Build-Windows-EXE.cmd`** from the project root.
4. The script creates a local `.build/` environment, installs dependencies, runs automated checks, and packages the executables with PyInstaller. **Internet access is needed** to fetch build dependencies.
5. After a successful build, distribute **`dist/EvolveModManager-Windows.zip`**. Recipients extract it and run `EvolveModManager.exe`—they do not need Python.

If the `twofish` dependency cannot build, you may need **Microsoft C++ Build Tools** with **Desktop development with C++** installed on the build computer.

**Do not distribute your `.build/` folder, initialized `Data/` folder, private keys, game PAKs, or personal project backups as part of the normal player release.** The automated tests check the packaging and many file-handling paths, but testing against an actual Windows game installation is still necessary.

## Disclaimer

**Offline use only.** Mods created or launched through this manager disable online play. This restriction is intentional.

If you bypass or work around this restriction to access online services while using modified game files, **you do so entirely at your own risk**. Such activity may violate server rules or terms of service and could result in account suspension, permanent bans, or other penalties. **The project maintainers and contributors are not responsible for account bans, lost access, or any other consequences resulting from attempts to bypass the online-play restriction.**

This is an **experimental, community-made tool** for users with their own installed game files. It is not affiliated with or endorsed by the Evolve developers or online service operators. It does not distribute Evolve or replace the Modded Evolve client. Back up important files before making changes.
