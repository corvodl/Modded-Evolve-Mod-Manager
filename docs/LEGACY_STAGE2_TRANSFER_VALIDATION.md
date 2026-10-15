# Legacy-to-Stage-2 transfer validation

**Status: experimental staging infrastructure, not a working Legacy migration.**

The existing manager transfer action is review-only. This branch adds a separate programmatic transaction for already signed Stage 2 archives in an isolated staging folder. It is not connected to the production Install button.

## Safety and limitations

- Whole-archive add/replace staging has hash preflight, signed-CryPak verifier, backup, rollback, and cancellation.
- New archive names require explicit opt-in and still require proof of mount discovery.
- This branch does not convert terrain, author new CryPak entries, sign new PAKs, modify playlists/GFX, or install content into a live game.
- Historical ZIP sources remain read-only.

## In-manager preparation (safe reference workflow)

In Settings → Asset Library, select a historical build and source PAK,
then choose **Prepare Transfer to Stage 2**. Choose a registered Stage 2
target build, paste one or more *exact* source asset paths (one per line), and
identify the target project and optional expected baseline hash. The manager
discovers bounded explicit XML dependencies and presents a review of
added/replacement candidates, reuse, missing assets, conflicts, and
non-scannable binary dependencies. Nothing is installed.

After saving the review-only plan, you may optionally create an **external
candidate bundle** of hash-verified loose files. This is a NEW directory
outside the managed Asset Library and existing target project. Its
`candidate-manifest.json` explicitly reports `signed=false`,
`installable=false`, and `game_compatibility_verified=false`. The
source files remain immutable. Missing and ambiguous sources block this
export; incomplete binary dependency inspection is reported, not hidden.

A loose-file candidate cannot be used directly as a signed Stage 2 PAK.
Do not rename the bundle to `.pak`, add it to a live installation, or
interpret the review manifest as a game-side compatibility result.

## Technical staging pilot

1. Copy an intact Stage 2 installation into a separate test folder. Never use the original installation.
2. In that test folder place `.stage2-transfer-staging.json` containing `{"schema":1,"purpose":"isolated-stage2-transfer-staging"}`.
3. Prepare another directory with already-signed PAKs and a `transfer-package.json` manifest, e.g.:

```json
{"schema":1,"kind":"stage2-verified-pak-transfer","files":[{"operation":"replace","path":"Game/Libs.pak","source":"Game/Libs.pak","sha256":"<new PAK SHA-256>","baseline_sha256":"<original PAK SHA-256>"}]}
```

The example PAK name is illustrative only, not a suggested resource for the first pilot. Both paths must match verified inventory evidence.

4. Invoke `install_package(package, test_copy, recovery_folder, verify_archive=crypak_verifier(stage2_public_key))` from `transfer_deployment.py`. Keep the recovery directory and journal.
5. Call `restore_package(recovery_folder)`. Verify hashes and absence of added files. Any intervening user modification blocks restoration rather than being overwritten.

An archive signature and valid deployment receipt do not prove Stage 2 mounted the archive or used its resources.

## Runtime gates

1. Existing-path dust-mesh replacement loads and is used in Stage 2.
2. Distinct new signed PAK is demonstrably mounted and its unique asset used.
3. Desert_Harsh plus exact companion PAKs is registered, selectable, and loadable under Stage 2 Hunt.
4. Verify terrain, materials, collision, traversal, navigation, wildlife, spawns, objectives, and round completion.
5. Restore all changed and added files, including any menu/playlist edits, without affecting unrelated content.
6. Only then investigate Legacy Nest, Rescue, and Defend, including callbacks, scoring, UI, and matching private multiplayer.

Do not label any gate successful without exact version, logs, and in-game evidence.

## Narrowly scoped diagnostic evidence to gather

- The signed target archive and source archive inventories for `objects/effects/2k_meshes/blowing_dust_plateau_mesh.cgf`, plus archive hashes and extracted payload hashes.
- The exact relative locations, names and hashes of Desert_Harsh level.pak and its data, texture and terrain companion PAKs.
- Redacted Stage 2 mount logs showing which PAK paths were discovered.
- A target-build Hunt playlist example and map-registration/menu callback traces; do not replace the target playlist schema with Legacy XML.

## Synthetic regression tests

```powershell
$env:PYTHONPATH="source"
python -m unittest source.test_transfer_deployment -v
python -m unittest source.test_build_collections -v
```

The synthetic verifier is only a test stub, never a production substitute for the signed CryPak verification function.