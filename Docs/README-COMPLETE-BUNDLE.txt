COMPLETE MANAGER ZIP - v2.2

This download is an updated SOURCE/BUILD KIT. Your actual keys, staged PAKs,
projects and existing swap setup are on YOUR computer, not in this download.
The new exporter collects them locally into one complete ZIP.

CREATE THE SHAREABLE ZIP
1. Build with Build-Windows-EXE.cmd and open the new EvolveModManager.exe.
2. Select your existing signing stage, swap folder and Projects in Settings.
3. Close the game and normal client. Restore original game files if swapped.
4. Settings -> Show Advanced Options -> Export Full Setup (Large ZIP).
5. Choose a new ZIP filename outside the folders being collected.
6. Wait for copying and ZIP verification to complete. Share THIS exported ZIP.
   The ordinary dist ZIP remains an app-only distribution.

CONTENTS
- EvolveModManager.exe, EvolveModWorker.exe and all bundled runtime libraries.
- Signing keys, original public key, staged PAKs and custom injector copy.
- Relocatable launcher/swap/restore helpers and first-run configuration.
- Every project in the selected Projects folder, including its editor backups,
  built PAKs and mod backups.
- External source PAKs referenced by those project manifests.
- Current original-PAK snapshot if your stage has a sibling originals folder.
- Existing helper scripts saved under Data/PreviousHelpers for reference.

The exported helper uses this manager version's launch logic and selected client
path. Arbitrary modifications to old helper scripts are archived for reference,
not executed automatically. The installed Evolve game and normal client are NOT
bundled. Live recovery journals/backups are not transplanted to another PC.

RECIPIENT
1. Extract the entire ZIP into a new writable folder, separate from Evolve.
2. Open EvolveModManager.exe. No Python or other Python libraries to install.
3. Select the installed EvolveGame folder and the normal Modded Evolve client EXE.
4. Keep both closed while the manager verifies original PAKs/injector.
5. A matching installation is required. A different game version stops setup.
6. The first Play with Mods prepares game-side copies from the bundled stage.

All mod-manager resource paths are rewritten for the extraction folder.
The only external installations needed are the game and its normal client.
The game directory still holds temporary prepared copies and original recovery
backups while using the swap workflow. Those are operational game-side files,
not missing resources from the manager ZIP.

Use the bundle on a clean/restored game installation. Setup refuses existing
customkey-ready or customkey-original files from a different manager so it
cannot silently take over that manager's recovery state.

Close game/client and fully restore/unprepare before moving an initialized
bundle again. Prepared or active journals prevent relocation. Merely using
Restore Original Game Files may preserve prepared copies; keep that initialized
folder in place and export a fresh ZIP when distributing elsewhere.

Expect a large ZIP: staged PAKs, originals and projects may take tens of GB.
The export stores large files without compression and supports ZIP64. It checks
ZIP CRCs before publishing the final filename. Existing source files remain
unchanged. Avoid editing them in another app while export is running.
Missing project source PAKs stop export so they are not silently omitted.

Developer checks cover export, included project dependencies, path relocation,
recipient matching/mismatching game files, and active-swap rejection using
synthetic encrypted/signed PAKs. Windows compilation, native UI and real-game
launch behavior still require Windows testing.
