V2.1 LAYOUT: README-PORTABLE-APP.txt supersedes older folder/dependency
locations below. Build and open commands are at the package root; source is
in source/, build dependencies in .build/, and new app data in Data/.

EVOLVE MOD MANAGER v2 - EDITOR / ICON / ORIGINAL PAK REFRESH
Experimental. This is source plus the Windows EXE build kit, not a prebuilt EXE.

BUILD AND UPGRADE
1. Extract into a NEW writable folder, separate from the game and swap folders.
2. Run Build-Windows-EXE.cmd on Windows with Python 3.11 x64 installed.
3. Open the EvolveModManager.exe inside the newly created dist\EvolveModManager-<timestamp> folder after the build succeeds.
4. Keep the whole output folder, including EvolveModWorker.exe and _internal.
5. Copy your old manager_settings.json beside the new EXE or select the old
   signing stage, swap folder and Projects folder in Settings.
The build embeds the supplied custom Hunt icon as the EXE icon. The app also
uses it for the window/taskbar. Windows may cache an older shortcut icon;
create a shortcut to the newly built EXE if necessary.

EDIT INSIDE THE APP
Unpack PAK -> Edit Files.
Browse the folder tree or filter by part of a file/folder name.
Select XML to edit it. Ctrl+S saves; Ctrl+F finds text in the open file.
Text formats supported: XML, TXT, CFG, INI, LUA, JSON, CSV (UTF-8, up to 5 MiB).
Binary files remain accessible using Open folder in Explorer.
XML is checked before saving. CryXmlB edits must keep the existing tags,
attributes and their order; values may change. Build Mod still performs
binary-preserving serialization and archive verification.
Each changed save preserves previous contents under the project's EditorBackups.
External changes are detected before overwrite. Reload or copy your buffer
elsewhere before resolving a conflict.
Save only updates extracted files. Use Build Mod, then Add Mod.
Folders can be browsed/opened. Renaming/adding/removing archive entries is not
supported by the existing PAK writer. Keep original paths intact.

REFRESH AFTER A GAME UPDATE
1. Close game + client. Use Restore Game Files if a swap is active.
2. Open the normal client, update/repair the game, then close it again.
3. Play & Restore -> Refresh Original PAKs.
4. Select the updated EvolveGame folder and a separate storage folder.
5. Confirm. Keep other managers, game and client closed until it finishes.
6. On success, the manager selects the new signed stage and new swap folder.
7. Unpack the new PAKs. Review/reapply edits to those new projects before playing.

Refresh stores a timestamped folder containing originals, a newly signed stage,
its signing keys, a fresh swap folder and a refresh report. It can use nearly
twice the installed PAK size, plus a 1 GiB reserve. Old stages, original snapshots,
editing projects and keys are retained; they are not merged into updated PAKs.

Installed signed PAKs must verify against the existing original public key.
The original inject.dll must still match the supported 4096-byte shim and known
key offset. Unknown archive formats, a changed shim/key, a running game/client,
or active recovery backups stop refresh rather than guessing compatibility.
Plain ZIP PAKs without comments are copied to originals but not re-signed/swapped.
Game update downloads remain the normal client's job.

Old prepared copies are preserved beside their game paths with the suffix
.customkey-ready.previous-<id>. They are no longer used for launch. The old stage
gets REFRESHED-TO.json so this manager will not accidentally launch that old set.
Do not run an older manager/helper against a superseded stage.

If building fails before activation, the previous setup remains selected.
The partially built refresh folder is retained for inspection; retry creates a
new folder. Do not select an incomplete stage.
If power loss interrupts activation, keep the report, all previous files and
journals. The report lists every old/new prepared-file location. Do not delete
REFRESHED-TO.json or manually launch the old helpers to force it past the guard.
On successful refresh, if settings fail to save, select the stage and swap paths
listed in the completed refresh_report.json manually in Settings.
The fresh swap folder preserves the previous launcher path when recognizable;
launcher_config.json can specify {"launcher": "C:\\path\\ModdedEvolveLauncher.exe"}.

VALIDATION AND LIMITS
23 automated tests passed in the development environment, including an actual
synthetic RSA-signed/Twofish-encrypted PAK refresh, extraction, XML edit,
CryXmlB rebuild, signature verification and next-launch preparation.
Failure tests cover stale external edits, invalid XML, structural edits,
active swaps, custom-signed inputs and failed refresh activation rollback.
The existing XML namespace regression test also passes.
Windows EXE compilation, native GUI behavior, actual Evolve gameplay and online
connectivity have NOT been verified here. These changes do not fix or establish
compatibility with the previously reported online profile-service issue.
