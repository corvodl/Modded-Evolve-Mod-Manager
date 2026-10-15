# Numbered milestones with in-place patch revisions

## Publishing

Regular pushes to `beta` and `main` build tested Windows artifacts as
before. The release workflows now compare the branch's packaged
`source/VERSION.txt` to the **published rolling manifest**:

- A **new version** starts revision **0** and creates a new, immutable
  numbered milestone, such as `v1.1.18-beta.51`. The rolling
  `Beta`/`Main` release also points to those verified artifacts.
- A subsequent source fix **without changing VERSION.txt** increments the
  revision: `v1.1.18-beta.51 · Patch 1`, Patch 2, etc. Only the **rolling
  channel** is updated. The original numbered GitHub release is not
  overwritten or renamed.
- Manual Actions runs can select `auto`, `milestone` or `patch`.
  Explicit `patch` refuses a changed base version; explicit `milestone`
  refuses a version already present. Downgrade attempts and invalid prior
  manifests fail, rather than silently reset the revision.
- Every update gets **unique, version-and-revision-stamped ZIP and installer
  asset names** so download caches cannot return an earlier patch.
  `update-manifest.json` is uploaded to the rolling release **last**.
  Older rolling ZIP/installer duplicates are deleted after the new manifest
  is live; numbered milestones retain their original downloads.

A patch rebuilds the **complete installer/portable ZIP**. This is not
yet binary-delta patching; only the release numbering becomes simpler.

### Versioned manifest

Schema 2 adds `revision` (a nonnegative integer), `release_type`
(`milestone` for revision 0, `patch` for a positive revision),
channel, exact source commit, immutable per-revision asset filenames,
byte counts and SHA-256 hashes. The exact branch HEAD is rechecked
before publication. The updater verifies the manifest against the
rolling GitHub release asset and embedded build commit, channel and
revision stamps before installation. Schema-1 manifests and older
installations without a revision stamp are supported as revision 0.

### Updater behavior

The manager checks version **and** revision: a newer revision is offered
on the same version, while older revisions (or older milestones) are
not offered as automatic updates. On equal versions/revisions,
the stable channel wins. Both auto-update and manual checks retain
their existing channel preferences; no experimental Actions artifacts
are silently installed.

**Bootstrap note:** Existing beta.50 installations use the older
version-only updater and cannot automatically discover a patch of
beta.50 without first installing a revision-aware build from GitHub.
Once the revision-aware updater is installed, future same-version patches
work normally.

## One %LOCALAPPDATA% installation directory

For the normal per-user installer, app binaries and **all manager-owned
files** are under:

`%LOCALAPPDATA%\EvolveModManager\`

Its children include `_internal`, `Docs`, `Data` (your persistent
settings/Asset Library/projects), `Data\Logs` (startup/update diagnostics),
and a temporary `UpdateWork` staging area. Updates no longer create
sibling `%LOCALAPPDATA%\EvolveModManagerUpdates` or
`EvolveModManagerQtPreview` locations. Customized install directories use
their own `UpdateWork` and `Data\Logs` inside that same chosen folder.

## Backups and cleanup

No **persistent application-update backups** are kept after success.
The detached updater briefly moves only the app-owned binaries and
runtime into `UpdateWork\<transaction>\rollback`, validates the new
manager's startup acknowledgement, and deletes the temporary rollback
copy and downloaded ZIP. If validation fails it first restores those
temporary files, then deletes the rollback copy on successful recovery.
An incomplete rollback deliberately leaves emergency recovery files,
rather than discarding the only surviving executable.

Game-file originals, mod swap journals, user projects and keys are
**not** application-update backups and are not cleaned or replaced.
To intentionally revert to an older milestone, install the retained
numbered GitHub release manually. An installer from an earlier patch
within the same milestone is **not guaranteed** to remain downloadable:
the rolling release keeps only the latest revision.

The updater does not auto-delete unrelated preexisting folders created
by much older installations. Verify no old updater is running before
manually cleaning obsolete sibling folders.
