# Easy Mod Installation & Quick Commands (PR #82)

The Modding page offers **Install Mod…** and **Installed Mods…** without a
new sidebar page. Ctrl+K opens a small searchable list of ordinary actions.

## Review-first import

1. Drop a custom-signed PAK or ZIP containing **exactly one** signed PAK into
   Install Mod, or choose Browse. A first-time user may use the automatic
   My Mods collection created only after confirming installation.
2. Select Review Mod. The file is inspected off the UI thread:
   - ZIP paths, compressed/uncompressed sizes and symlinks are checked,
     extraction is bounded and the archive never writes outside temporary
     storage.
   - Only exact PAK basenames in the signed staged plan are considered.
     Unknown or ambiguous PAKs are rejected, never guessed.
   - The signing public key and original staged PAK are used to verify the
     matching signed filename, archive entries, and changed-file records.
     No changed entries or mismatched signatures are rejected.
   - The user sees the target, changed asset list, size and SHA256 before
     any import.
3. Select **Confirm & Stage Mod**. The user sees a final confirmation with
   target and changes; any *enabled* mod already targeting the same PAK is
   explicitly described and disabled in the same collection only after
   successful immutable import.
4. The validated PAK is copied under the existing ModProfiles ImportedMods
   folder using a content-hash-based path. Changing the selected download
   between review and import is rejected. This retains an independent
   user-owned source rather than pointing to downloads that may disappear.
5. The manager calls the existing **ModProfiles** apply workflow, which
   refuses active game/swap operations, checks the signed stage and mod
   hashes, creates original staging backups, and journals each operation.
   **No installed EvolveGame PAK is modified** by installing a profile.

If a ZIP contains multiple PAKs, import them individually using the existing
advanced Mod Profiles tools; silent multi-PAK replacement is not supported.
ZIP imports can include README/documentation files but do not execute any
embedded installer or scripts.

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

    python -m unittest -v test_easy_mod_install test_qt_easy_mod_installer

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
