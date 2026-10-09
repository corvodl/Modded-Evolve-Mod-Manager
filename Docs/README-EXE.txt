V2.1 LAYOUT: README-PORTABLE-APP.txt supersedes older folder/dependency
locations below. Build and open commands are at the package root; source is
in source/, build dependencies in .build/, and new app data in Data/.

V2 UPDATE: Read README-V2-NEW-FEATURES.txt for the integrated editor, custom
icon, and Refresh Original PAKs workflow. This supersedes older editor instructions.

EVOLVE MOD MANAGER - BLACK / RED EDITION (EXPERIMENTAL)

THIS DOWNLOAD IS SOURCE + A WINDOWS EXE BUILD KIT, NOT A PREBUILT EXE.

BUILD ONCE (you / the person distributing the manager)
1. Extract this package into its own writable folder.
2. Install Python 3.11 x64 if you do not already have it.
3. Double-click Build-Windows-EXE.cmd. Internet is required for dependencies.
   If twofish cannot compile, install Microsoft C++ Build Tools with
   "Desktop development with C++", then retry.
4. After SUCCESS, open the EvolveModManager.exe inside the newly created dist\EvolveModManager-<timestamp> folder.
5. Share dist\EvolveModManager-Windows.zip with others.

USE THE BUILT APP (no Python install required)
1. Extract the entire Windows ZIP into a writable folder.
2. Double-click EvolveModManager.exe.
3. In Settings, select your existing signed mods, one-click folder and projects.
Keep EvolveModWorker.exe and _internal beside the main EXE.
The worker performs background jobs; users open only EvolveModManager.exe.
Settings and new projects live beside the main EXE. Avoid Program Files.

UPGRADING
You can keep this manager in its own brand-new folder.
Optionally COPY manager_settings.json from your old manager folder, or choose
folders in Settings. Point My editing projects at your existing Projects folder.
Keep your existing swap folder, journal, backups, signing keys and staged PAKs.
Existing launcher helper scripts are reused; missing helpers can be added in GUI.
Do not copy the manager into PauseSwapTest or into the game's install folder.

WHAT CHANGED
Black/charcoal panels, red tabs and primary buttons, readable light text.
Executable-aware background jobs and writable settings paths.
The current CryXML namespace fix and existing launch/restore safeguards remain.
This update does not establish that every gameplay XML edit works in-game.

VALIDATION
Source integration and child-worker checks can run without touching game files.
The Windows build runs dependency checks using the built worker.
Windows EXE creation, native UI appearance and actual game launch still require
Windows validation. A successful dependency check is not a live gameplay test.

SOURCE LAUNCH
The build-kit shortcut Open-Evolve-Mod-Manager.cmd opens the most recently built EXE.
If no EXE is built, run Build-Windows-EXE.cmd first.
Use EvolveModManager.exe directly in the portable Windows release.

SAFE XML UPDATE
Read README-SAFE-ABILITY-EDITS.txt before editing. Rebuild the EXE to include it.


V2.5 PREPARED-FILE RECOVERY
If leftover *.customkey-ready files are found in the installed game,
the manager offers to preserve them by renaming in place. No existing PAK,
private key, or backup is overwritten. The old setup may need to prepare
its copies again; saved copies and their names are listed in Data/ArchivedPrepared.
If original backups or incomplete prepared files exist, the operation stops.
