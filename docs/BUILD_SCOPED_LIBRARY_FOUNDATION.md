# Build-separated Asset Library — initial foundation

This draft introduces a snapshot registry in `source/build_collections.py`, scoped asset APIs, and conservative reference-only transfer planning. It does not recognize all historical archive formats or make them compatible with Stage 2.

- Register an `early`, `legacy`, `stage2` or `unknown` snapshot with an editable label, optional location and user-supplied evidence. A location change does not alter identity.
- Old imported manifests remain unchanged, preserve their IDs and extracted files, and appear under `Unassigned` until explicitly assigned. An extracted archive can belong to multiple snapshots without duplication.
- `build_index(build_id)` deliberately filters records to one membership. Never use `extracted_index()` as a substitute when build context is known. `All Builds` is inventory only.
- `compare_builds` classifies normalized-extracted payload hashes as identical, changed, added, removed, or unknown. These are **not** original payload hashes for CryXmlB conversions.
- `prepare_transfer` returns an explicit, read-only reference plan and does **not** stage, convert, repack, write a game archive or verify in-game compatibility. Expected target baseline hashes are recorded (format-checked, not read-back verified); no writer is authorized.

## Current gaps / no compatibility promise

These APIs are not wired into every viewer, shared cache, map consumer, or project operation. Archive-specific texture/model preview and optional map-mount resolution can be scoped, but existing callers without a build context remain a migration risk. Existing unscoped consumers must be audited before treating preview/dependency isolation as complete. The build catalog is separate from extracted storage; the library UI supports registering and relocating snapshots, assigning imported archives, importing into a selected build, removing memberships, and filtering/sorting/comparing snapshots. Concurrent catalog writers are not yet coordinated across processes. Transfer dependency discovery follows only explicit references from bounded, safe text XML; binary formats remain unscanned and closure is incomplete unless independently verified. Original-payload provenance, safe writer staging, and structural compatibility analysis remain future work. Do not auto-map skeletons, animations, materials, DDS fragments or maps between builds. Early/Legacy archive parsers and archive verification keys must be validated separately.

## Validation checklist for historical assets

1. Record game build/version evidence, archive path and SHA-256 without assuming an executable layout.
2. Validate signatures, directory layout and the supported encryption methods on legally held test archives. Refuse unsupported formats.
3. Compare original raw payload hashes with separately recorded normalized extracted hashes; inspect missing and ambiguous references.
4. Check mesh topology, skeleton/bind pose, material conventions, stream fragment ordering and texture formats before attempting structural conversion.
5. Check target workspace original-entry identity and exact verified baseline SHA before any writer operation; test rollback and unchanged original archives.
6. Test only in an isolated, offline Stage 2 installation, including load, rendering, animations, and restoration. Mark `in-game verified` only after documented evidence.
7. Exercise snapshot collision, removal, relocation, cancellation, cache invalidation, map and texture-stream isolation and sorted UI selections when integrations are implemented.

Synthetic tests: `python -m unittest test_build_collections test_build_collections_qt test_build_transfer_dependencies` with `source` on PYTHONPATH; the Qt test requires PySide6 and `QT_QPA_PLATFORM=offscreen`. Consult the draft PR checks for actual CI outcomes.
