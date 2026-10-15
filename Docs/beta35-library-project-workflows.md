# Beta.35 Asset Library / Modding integration — review checklist

This work combines build-separated imported archives with the existing
Stage 2 custom-signed workspace writer. It does NOT add an arbitrary
historical-to-Stage 2 converter or write imported library source files.

## User workflows implemented in this draft

- **Asset Library → All Assets / By Build**: select a build snapshot when
  looking up archive membership. Sorting retains archive identity via Qt.UserRole.
- **Import All Game PAKs**: explicitly select a source build and browse to the
  source directory. This is a read-only bounded archive scan and does not
  reconfigure the launch target. Unsupported archives fail individually.
- **Asset preview**: Ctrl/Shift multi-selection in the shared tree, with stable
  virtual-path identities. Folders expand to at most 500 deduplicated files for
  supported batch actions. Export original files asynchronously into a
  separately named directory; cancellation cleans temporary output.
- **Edit / Add to Mod Project**: choose an imported Stage 2 archive that has
  an exact verified custom-signed staged counterpart; create an independent
  workspace using the existing extractor; open the selected virtual asset in
  the editor. Library hashes and storage are never edited.
- **Open Map**: select a supported map and explicit Stage 2 build; group
  matching archives by map source identity within that build. Refuse missing,
  ambiguous, historical, or conflicting staged mappings; extract bounded
  independent map workspaces and open supported placement editing.
- **Modding**: projects and modified files are now the primary visible tables.
  Advanced archive operations remain available behind an explicit toggle.
  Project build, offline test and stage actions use the exact manifest's
  verified staged source rather than an incidental sorted PAK row.
- **Navigation/Settings**: integrates PR #41's offline scalable picture icons,
  theme-aware focus and the original Check for Updates button first in
  Settings. The original updater and protected navigation state are unchanged.

## Workspace identity and build boundaries

`project_catalog.py` persists editable workspace identities and provenance,
including source build/archive and workspace baseline SHA evidence. A map
collection is not treated as one signable PAK; each workspace has separate
archive ownership. Moved or missing workspaces remain visible as unavailable.
Stage 2 writer validation still checks source/key/entry identities and hashes
at build time. A successful Save is not equivalent to Build, stage, or
in-game verification.

Unassigned or historical imports remain usable as **reference-only** assets.
Cross-build transfer plans do not authorize staging. If an archive lacks the
current manager's unique verified custom-signed target, editing is refused
instead of generating an unbuildable mod.

## Release gates

- [ ] Source scanning, membership, relocation, cancellation and project
      provenance synthetic tests pass on Windows.
- [ ] Existing Asset Library, full Qt, level editor, materials, rigs and
      texture export regression suites pass on integrated head.
- [ ] Windows full application build, package content inspection and Inno Setup
      installer candidate build pass **without release publication**.
- [ ] Compiled screenshots for main, Settings, Asset Library, Modding and
      Play & Restore are generated and reviewed for clipping/contrast/action
      placement. Light/dark, compact/comfortable and HiDPI still require
      explicit review where automated capture is unavailable.
- [ ] Confirm no earlier PR #41 stale version changes override the
      currently published beta.34 source.
- [ ] Only once checks pass, update version and synchronized curated
      public notes to the next unused Beta tag (intended beta.35).
- [ ] Merge the integrated PR after review, verify the beta publication
      workflow succeeded, installer/ZIP and update manifest correspond to
      the merged commit and version.
- [ ] Close PR #41 as superseded by integrated PR #42 rather than merging
      the old beta.34-based branch independently.

## Explicit limitations

Native historical CryEngine conversions, binary reference rewrites, unknown
terrain encodings, additional original-entry creation and skeletal or
animation compatibility are unsupported. Some terrain preview and GPU
renderer fidelity needs testing with legally held assets on actual Windows
hardware. No proprietary game data was used to implement these features.
