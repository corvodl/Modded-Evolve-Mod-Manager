# Easy Mod Installation & Quick Commands (PR #82)

The Modding page offers **Install Mod…** and **Installed Mods…** without a
new sidebar page. Ctrl+K opens a small searchable list of ordinary actions.

## Review-first import

1. Drop a signed PAK or a ZIP containing **1–24 PAKs**, or click
   **Import Older Mods…** to select a user-owned directory from an earlier
   manager. The game installation and active signing stage are never scanned
   as legacy sources.
2. **Review Mod** validates *every* PAK before any catalog change: bounded ZIP
   paths, sizes, compression ratios and symlinks; verified stage target (with
   explicit stage-relative names for duplicate basenames); RSA-signed CDR
   identity, modified asset list and SHA256. One invalid target rejects the
   whole bundle.
3. **Confirm & Stage** shows targets, asset counts and enabled conflicts. The
   importer rechecks every source and signature, reuses identical full-SHA256
   files already in the immutable library, and performs exactly one atomic
   ModProfiles catalog update for the entire bundle. Matching previously
   imported mods are re-enabled rather than copied again.
4. Staging calls the existing profile worker, which journals backup/restore
   **per signed target PAK**. An interrupted stage can require manual recovery;
   it is never reported as successful without the worker and journal checks.
   Installed game files are not changed by profile staging.
5. An **Installation History…** panel records a privacy-minimal receipt with
   target PAKs, changed-entry counts and SHA256. It is first marked
   `imported_not_staged` and changes to `staged_verified` only after the
   profile worker finishes and the expected journal entries are present.

The compact **Installed Mods** table shows the mod name, target PAK, whether
that PAK exists in the configured signed stage, and its enabled/staged state.
These are *target compatibility indicators*, not guarantees a mod will work
at runtime. Controls for profile creation, sharing, reordering, restoring and
installation history remain in the existing dialog, not another sidebar tab.

ZIPs containing scripts are treated as untrusted data. They are never
executed; only the validated PAK members are imported. Legacy imports require
an explicit directory selection and signed archive validation before adding
user-owned files to the library.

## Library, conflicts and undo

Installed Mods… opens the existing Mod Profiles library with enable/disable,
reorder, remove, shareable manifest, target conflict warnings and staging.
Two enabled mods targeting the same PAK are rejected by the profile plan:
reordering does not bypass the conflict. A single target is replaced only
after explicit user approval.

**Undo Applied Mods…** restores profile-managed staged versions using the
existing per-PAK rollback journal. The game and launcher must be closed,
and active/unsafe swaps must be resolved first. User-owned downloaded source
mods and separate project files are never removed by undo.

## Compact keyboard navigation

Ctrl+K opens an on-demand searchable dialog, including Install Mod,
Installed Mods, Undo Profile Staging, Home, Modding, Asset Library, Settings,
Projects, Updates, Restore Game Files, and Activity. Running a command
invokes the ordinary UI method with its existing confirmation and privilege
checks. No Developer or online-write shortcuts are exposed.

## Validation / release

Run the synthetic regression tests:

    python -m unittest -v test_easy_mod_install test_easy_mod_bundle test_qt_easy_mod_installer

Windows CI must also pass Qt navigation, RSA/PAK verification, profile
journaling, portable ZIP, installer and worker smoke tests. Real-game
installation/restoration and UAC behavior require an offline Windows test
installation before release. PR #82 stays draft until the tests pass.


## Game Storage — Settings

The old three always-visible path fields are replaced by one **Mod Data
Location** picker and a **Storage Breakdown** panel in the same compact
dark/red style as the rest of the manager. The colored bar is proportional to
**measured disk usage** and labels show actual GiB/MiB, not mock percentages.
The five categories are Active setup (red), Old setups (blue), Projects (gold),
Asset Library (teal) and Other (gray). Storage is scanned off the UI thread
on entering Settings and on demand; there is no startup disk scan or periodic
background polling.

**Advanced folder paths** is collapsed by default. It preserves direct access
to existing signing stage, launch/swap and projects paths for compatibility.
The primary Mod Data Location setting chooses where NEW setup/refresh output
will go. Changing it or opening Advanced does NOT move, relink, delete, or
overwrite the current signed PAKs, swap journal, project backups, or Asset
Library. Save Settings to preserve the location preference.

**Smart setup reuse** checks the configured client/game identity, signing
status, complete signed inventory, helper scripts and safe restored/prepared
swap journal. If the existing installation is a valid match, Set Up Manager
reuses it without generating new PAK copies; changed identities or uncertain
recovery states fall back to the original confirmed setup flow.

**Review Storage & Cleanup…** inspects direct children of the chosen
GameSets root with recognized Setup_/EvolveRefresh_ names and complete
refresh reports. It measures each setup and disables selection of active
setups, damaged/incomplete setups, non-restored swap journals, remaining
recovery backups, or setups referenced by current projects/Mod Profiles.
Deletion is never automatic; the user chooses eligible rows and explicitly
confirms permanent removal. The manager rechecks eligibility, game/launcher
closure, original file recovery and prepared markers immediately before
each deletion. This removes an **unused old setup folder only**, never the
installed game directory, current signing stage, or Projects.

Original game snapshots remain optional for FUTURE setups via the existing
Backups & Recovery preference. Live game swap backups remain mandatory.

Regression tests: test_game_storage.py and test_qt_game_storage.py, included
in Windows Qt, installer and portable ZIP CI test lists.
