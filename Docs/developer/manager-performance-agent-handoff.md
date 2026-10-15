# Agent handoff: speed up the Evolve Mod Manager

**Audience:** Chat, an agent without access to the user's local files.  
**Prepared:** October 15, 2026.  
**Repository:** [corvodl/Modded-Evolve-Mod-Manager](https://github.com/corvodl/Modded-Evolve-Mod-Manager).  
**Context:** [PR #76](https://github.com/corvodl/Modded-Evolve-Mod-Manager/pull/76), branch `fix/stable-121-p2-home-client-mod-navigation`.  
**Inspected commit:** `6e54febca9c7625b570c6d8c01968db6049ee8ab`.

## Instructions for the receiving agent

Improve overall responsiveness and actual throughput across application startup, GPU model preview, Map Editor loading, and PAK extraction/import. Preserve visual quality, asset provenance and verified file handling.

This document supplies the architecture, concrete source observations, selected code excerpts, proposed changes and acceptance criteria. You do not need the user's machine to understand or prioritize the work. The source links are pinned to the inspected commit; use a connected GitHub tool if available. If you cannot access source remotely either, produce a patch specification and request the named files from an implementation agent. Do not invent current code or claim changes were applied, benchmarked or tested.

Before editing, refresh PR #76's head and reconcile intervening changes. The findings below describe the inspected commit, not every future revision. Keep individual changes reviewable. This is a performance investigation and implementation brief, not authorization to merge or publish a release.

**Evidence boundary:** Source paths and control-flow observations were inspected locally. No representative end-to-end performance benchmark was run for this report. All predicted speedups, bottleneck rankings and proposed targets remain hypotheses until measured. No numeric speedup is promised.

## 1. Executive assessment

The manager already uses background workers, cancellation/generation tokens, geometry caches, texture caches and map residency limits. Recommending “use the GPU,” “add threading,” or “cache assets” without addressing existing implementations would duplicate work.

The most concrete opportunities are:

1. **PAK CPU throughput:** the Windows Twofish path invokes encryption per 16-byte block and performs bytewise XOR in Python. Replace the expensive loop with a verified native bulk operation before assuming storage or insufficient threads are the limit.
2. **GPU first-frame latency:** character preparation is largely off-thread, but all material textures and their mipmaps are uploaded synchronously. Publish geometry first, then progressively upload verified textures on the GL context owner.
3. **Map texture churn:** receiving a streamed residency update currently clears all GPU textures, including terrain. Preserve unchanged resources across updates instead of deleting and uploading them again.
4. **Map first-open latency:** initial editor construction still parses `LevelProject` synchronously, while subsequent map-selection paths already prepare projects in workers.
5. **UI population:** file trees and outliners are rebuilt with thousands of Qt items. Searches can trigger the entire process on each text change. Debounce immediately, then move to persistent models and lazy population.
6. **Repeated discovery and cache misses:** improve dependency identities and incremental indexing; avoid enlarging caches or removing their TTL without correct invalidation.

Treat startup latency, time to first useful frame, time to full-quality readiness, cancellation latency and steady-state frame rate as separate metrics.

## 2. Architecture and existing optimizations

The application is a Windows-focused Python/PySide6 desktop GUI using NumPy, Pillow and PyOpenGL. Models are parsed and prepared on the CPU, then rendered with `QOpenGLWidget`. “GPU preview” does not mean file I/O, CryEngine parsing, material resolution or image decoding currently run on the GPU.

### Startup and browsing

- `QtManager.__init__` constructs Home, Instructions, Asset Library, Modding, Play, Settings, Credits and What's New pages before normal use. Developer content starts as a placeholder and is separately gated.
- Initial Modding inventory discovery is already deferred until first use and processed in a background worker.
- Startup schedules shared-asset warming after 500 ms, automatic library warming/import after 1,300 ms and an update check after 2,500 ms. Scheduling a callback later is not itself proof that all work inside that callback is off-thread.
- Workspace `populate()` sorts records, groups models, stats files and creates `QTreeWidgetItem`s. Asset Library rows are capped at 2,500 after filtering; editable project trees do not use that same cap.
- `search.textChanged` connects directly to workspace `populate()`. Map outliner filtering also connects directly to a full rebuild.

### Model preparation and rendering

```text
Selection
  -> QtWorkspaceEditor._load_model_background
  -> prepare_model_cached
  -> resolve_model
       read model / build or reuse index
       assemble character or find preview mesh
       resolve materials and decode textures
       discover animation references
       prepare or load cached geometry
  -> _model_ready on GUI thread
  -> viewport creation/reuse
  -> texture uploads + mipmap generation
  -> rendered frame
```

- Visible normal GUI operation uses a `QThreadPool` limited to two model workers. Offscreen/minimal or not-yet-visible editors take a synchronous path. **Offscreen test times do not characterize production model loading.**
- Generation checks reject stale results, but the shown `resolve_model` call does not accept a cancellation token. Rejecting a stale result does not cancel the CPU work that produced it.
- Prepared-model cache: **3 entries, 30-second TTL, 384 MiB accounting budget**; results over half that budget are not admitted. The estimate does not constitute a process-wide RAM cap.
- The key includes file/manifest size and mtime, folder markers, equipment, assembly mode and `id(index)`. The current caller already retains its index between views to avoid needless identity changes.
- A separate disk geometry cache exists. It fingerprints mesh arrays and stores compressed `.npz` data. A disk-cache hit occurs after the mesh was obtained; it does not skip all parsing or texture work.
- Decoded texture pixels also have a cache, bounded at 128 MiB. Preview texture resolution supports up to 4K for the highest setting; map material loading uses the balanced path.

### Maps

```text
Open editor -> LevelProject parse -> widgets/outliner/content
  -> prepare_scene worker
       ensure_map_archives
       mount scoped asset index
       assemble scene
       decode terrain chunks and optional atlas
       create residency store / decode initial resources
  -> GUI receives scene
  -> render and upload textures incrementally
  -> camera changes request further residency loads
```

- Initial `UnrealStyleLevelEditor.__init__` directly calls `LevelProject(workspace)`.
- Existing-map selection and library-map preparation already have worker paths. Reuse their design rather than introducing a second incompatible lifecycle.
- Dependency discovery is iterative, bounded to 32 iterations, because imported models/materials can reveal further textures.
- Scene preparation already supports a partial callback, CPU residency budgets, shared model geometry and generation-aware results. In the inspected order, the first partial callback occurs after dependency preparation, scene assembly and terrain/atlas work.
- Map texture upload already uses a count and deadline budget: two uploads and a nominal 4 ms deadline per paint. A single GL upload can still exceed that deadline.
- Outliner population creates up to 25,000 rows; content population up to 22,000 file rows. Caps limit allocations but are not virtualization.

### PAKs

```text
Import request -> background controller
  -> sequential per-archive import_pak
       unchanged-source shortcut
       source SHA-256 / signature verification / directory parsing
       entry read -> Twofish CTR -> optional DEFLATE -> length + CRC checks
       optional CryXML conversion -> write -> extracted hash
       final source SHA-256 -> manifest -> atomic publish
```

- Automatic import already skips unchanged sources using stored metadata. Do not replace this with rehashing the entire library on each launch.
- Asset Library imports use a temporary `.importing` directory and publish only after completion. Editable workspace extraction has its own incomplete marker and round-trip validation.
- Ordinary historical ZIP and signed CryPAK paths are distinct. Both need coverage when changing the import pipeline.
- The modern `decrypt_entry()` path reads a whole entry, decrypts it into another buffer and optionally decompresses it into another. It reopens the source archive for each entry.
- Cancellation in the modern import loop is checked between entries, so one large entry can delay cancellation.

## 3. Source anchors and decisive excerpts

All links below are optional navigation aids. The substantive findings are included in this document.

| Area | Files / symbols to inspect or change |
| --- | --- |
| Startup and orchestration | [qt_manager.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/qt_manager.py): `__init__`, `_load_initial_modding_inventory`, `_warm_asset_library`, `_asset_library_finished`, `open_level_editor` |
| Browser/model lifecycle | [qt_workspace_editor.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/qt_workspace_editor.py): `populate`, `_load_model_background`, `_model_ready`, `_paint_model` |
| CPU model pipeline | [async_model_resolve.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/async_model_resolve.py): `resolve_model` |
| Model cache | [studio_polish_117.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/studio_polish_117.py): `model_cache_key`, `prepare_model_cached`, `_detached` |
| Geometry cache | [asset_geometry_cache.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/asset_geometry_cache.py): `_fingerprint`, `cached_geometry` |
| Model GPU path | [qt_gpu_viewer.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/qt_gpu_viewer.py): `set_mesh`, `_upload_textures`, `paintGL`, `set_materials` |
| Texture decode | [material_preview.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/material_preview.py): `_decode_texture`, `_decode_texture_pixels`, `load_preview_materials` |
| Map UI lifecycle | [qt_level_editor.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/qt_level_editor.py): constructor, `_prepare_existing_map`, `_populate_outliner`, `_populate_content` |
| Map scene/GPU | [qt_level_scene_render.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/qt_level_scene_render.py): `prepare_scene`, `_receive`, `_request_visible_resources`, `_clear_gpu_textures`, `paintGL` |
| Residency | [level_residency.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/level_residency.py): `SceneResidency.load` |
| Map dependencies | [level_dependency_loader.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/level_dependency_loader.py): `ensure_map_archives`; also `level_asset_mount.py` |
| Import and indexes | [asset_library.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/asset_library.py): `import_pak`, `extracted_index`; controller in `qt_asset_library.py` |
| Extraction | [evolve_pak_workspace.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/evolve_pak_workspace.py): `cmd_extract` |
| Crypto/decompression | [evolve_gameplay_editor.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/evolve_gameplay_editor.py): `decrypt_entry`; [evolve_video_pak_tool_fixed.py](https://github.com/corvodl/Modded-Evolve-Mod-Manager/blob/6e54febca9c7625b570c6d8c01968db6049ee8ab/source/evolve_video_pak_tool_fixed.py): `Twofish.encrypt_blocks`, `twofish_ctr` |

### A. Windows crypto overhead is concrete

Current Python-backed block encryption:

```python
return b''.join(self.impl.encrypt(blocks[i:i+16])
                for i in range(0, len(blocks), 16))
```

CTR construction also builds counters in Python and uses:

```python
out.extend(x ^ y for x, y in zip(part, keystream))
```

The cipher implementation may itself be native; the repeated Python calls and byte operations are still present. The Linux fallback has a bulk libgcrypt call, but that does not make the Windows package bulk-native. Measure the Windows packaged path.

### B. Texture upload is synchronous

The model viewport walks diffuse, normal and specular image groups, converts upload pixels, calls `glTexImage2D`, and attempts `glGenerateMipmap` for each nonempty image. Its `gpu_upload_ms` measures elapsed CPU time around those calls, **not necessarily completed GPU execution**.

### C. Map streaming deletes reusable GPU state

The streamed-result branch of `_receive()` currently does this:

```python
if details.get("streamed"):
    self._streaming = False
    self._clear_gpu_textures()
    self.assets = assets or {}
    # issue reporting omitted
    self.update()
    return
```

`_clear_gpu_textures()` deletes all cached texture IDs and the terrain texture. This is a specific candidate for unnecessary reupload work on camera-driven residency changes.

## 4. Instrumentation first: distinguish waiting from work

Add opt-in structured spans using a monotonic timer. Each event should include operation ID, request generation, stage, elapsed time, counts/bytes, cache hit or miss, queue wait and cancellation outcome. Use logical asset IDs or sanitized relative paths in shareable logs; avoid private absolute paths and profile data.

Instrument these stages independently:

| Operation | Required timing boundaries |
| --- | --- |
| Startup | process entry, Python imports, window construction, first paint, first accepted interaction, background jobs start/finish |
| Model | queue wait, index lookup, parse, assembly, texture resolve/read/decode, animation discovery, geometry cache read/build/write, UI install, GL submission, first geometry frame, final-quality frame |
| Map | project parsing, dependency scan/import, index mount, scene assembly, terrain decode, atlas, first proxy, first real geometry, first textured view, residency updates, outliner/content creation |
| PAK | source hash, directory/signature verification, input read, CTR, inflate, CRC, XML conversion, output write/hash, final source validation, manifest publish |

Use a GUI heartbeat to record event-loop delay while operations run. Collect CPU utilization, disk bytes/throughput, peak working set, cache bytes, pending tasks and GL upload counts. Report GPU vendor/renderer/version and whether software fallback was selected. GPU utilization alone cannot identify parsing or I/O bottlenecks.

For GPU timing, distinguish CPU submission from actual GPU completion. Use asynchronous timer queries where supported and avoid inserting `glFinish` into every production frame. A completed `frameSwapped` can serve as a user-facing frame milestone, not as proof that every asset has reached final quality.

Compare four states: first import; extracted assets with empty derived caches; warm repeat; source modified and correctly invalidated. A fresh process is not necessarily a cold OS filesystem cache. Use at least five runs for an initial comparison, report median/range, and use enough samples before making tail-latency claims.

## 5. Prioritized implementation work

### P1-A: Make first interaction and first map frame independent of optional work

**Change:** Build a lightweight loading shell for the initial Map Editor, parse `LevelProject` off-thread and install the result on the GUI thread using the existing generation/cancellation pattern. Unify first-open and map-switch paths. Show placement proxies as soon as a valid project exists; do not wait for every terrain/material dependency.

For manager startup, measure eager page construction and module imports. Lazily construct costly pages on first entry while preserving navigation indexes and Settings behavior. Keep Developer authentication and disposal unchanged. Schedule warmups as lower-priority work that yields to interactive model/map requests; simply adding a longer fixed delay is not a robust scheduler.

**Acceptance:** Initial map parse never runs in the GUI constructor; closing or switching while loading installs no stale project. Home can accept interaction while optional warmups are active. Existing restore/update behavior remains intact.

### P1-B: Eliminate search-triggered bulk widget rebuilds

**Immediate:** Debounce search for approximately 150–250 ms, reuse a normalized searchable catalog and avoid restatting every file on every keystroke. Use manifest sizes for provisional display; validate files at actual access and refresh asynchronously when needed.

**Follow-up:** Replace large item-based trees with a `QAbstractItemModel`/tree view, lazy folder expansion and incremental insertion. Preserve selection by stable virtual path or `(layer, object index)`, not item object identity. Search the full catalog, not just the currently materialized rows. Avoid silently hiding assets behind a result cap.

The Map Editor outliner/content panels need the same approach. Do not rebuild their complete catalogs after a single transform change.

**Acceptance:** Rapid typing produces one settled search job; missing/stale results never replace newer queries. A 25,000-object fixture remains searchable and selectable. Tree work is batched within a measured GUI time budget.

### P1-C: Retain map textures across residency changes

Replace full invalidation with a diff of resource identities. Key textures by source/build identity, dependency fingerprint, material role, preview quality and renderer context/share group. Retain unchanged IDs; delete only evicted or changed resources. Keep terrain independent of object residency.

Retain the current bounded upload scheduling, but add byte budgeting and instrumentation. Preserve context-loss/recreation cleanup. A nominal 4 ms deadline cannot interrupt one large `glTexImage2D`; count the overrun and reduce next-stage granularity where possible.

**Acceptance:** An update that changes no texture sources issues zero texture deletes/uploads. Panning away and back within the residency budget reuses resources. Switching builds or modifying a texture does not reuse stale pixels. Context destruction releases resources correctly.

### P1-D: Stop obsolete model jobs and publish useful stages earlier

Thread a cancellation token through `resolve_model` and expensive subordinate loops. Keep generation checks as a second defense. Maintain one current foreground request and a bounded queue; coalesce duplicate requests for the same fingerprint. A newer user selection should not sit behind two obsolete full texture decodes.

Split the result into geometry, material/texture stages and animation-reference stages. Render a clearly identified loading preview as soon as verified geometry is ready, then update materials and animations. Do not call a placeholder “fully loaded.” Apply stages only to the matching generation.

**Acceptance:** Selecting A, B, C rapidly shows only C as the current final result and obsolete work exits at defined checkpoints. UI/GL objects are never mutated from CPU workers. Final assembled equipment, skeleton and textures match the existing pipeline.

### P1-E: Accelerate the existing crypto path without changing the format

Benchmark synthetic buffers with the current Windows backend first. Implement or integrate a maintained, license-compatible native bulk Twofish-CTR backend with bounded buffers. Reuse a key schedule within a safe worker-owned session instead of rebuilding it per entry when valid. Preserve the exact little-endian 128-bit counter and `block_offset` semantics.

Keep the current implementation as a reference/fallback until the native path passes differential tests. Do not substitute AES, alter IV generation, omit signatures or weaken CRC/length checks. This is an implementation optimization of the existing archive workflow.

**Acceptance:** Byte-identical results across randomized lengths, partial final blocks, nonzero offsets and counter wraparound; methods 13 and 14 still extract identically; corrupted ciphertext is rejected. Benchmark read, crypto, inflate and write separately. Verify DLL/native dependency packaging on a clean Windows installation.

### P2-A: Stream modern PAK entries and reuse file handles

Refactor an archive reader session that owns a verified directory and bounded file handles. Stream encrypted chunks into native CTR, incremental DEFLATE, rolling CRC and output hashing. Keep XML conversion bounded; XML may still require a complete entry representation.

For chunks not aligned to 16 bytes, preserve unused CTR keystream bytes correctly. Do not merely advance `block_offset` by a rounded chunk size. Check cancellation between chunks and do not publish a file or archive before required validation finishes. Keep case-insensitive duplicate checks, output containment, symlink handling, declared sizes and decompression limits.

A worker must not share a seekable file pointer unsafely with another worker. Preserve the final source-change detection guarantee; removing the final hash solely for speed is not an equivalent implementation. A metadata check alone does not prove identical source bytes.

Only after profiling, compare one, two and four bounded workers. Use a global memory budget and storage-aware concurrency. Python CPU loops do not automatically scale with threads under a normal GIL-enabled runtime. Process serialization can erase gains if whole texture/mesh buffers are copied between processes.

**Acceptance:** Large-entry memory is bounded by chunk/conversion limits rather than archive size. Cancellation during one large entry responds promptly. No complete manifest is exposed after cancellation or corruption. First-import output and second-import idempotence match baseline.

### P2-B: Make caches precise and useful beyond 30 seconds

Introduce dependency fingerprints that include all material/texture fragments, build scope, equipment, quality and parser/renderer revisions. Start with immutable manifest/index snapshots and generation IDs. Directory mtime alone is insufficient to detect arbitrary descendant edits; `id(index)` is not a persistent content identity.

Then evaluate longer prepared-model retention, byte-budgeted LRU and shared immutable geometry. Keep writable animation vertices/normals isolated, as `_detached` already does. Account for duplicated arrays, retained source bytes, decoded images and active viewers; current cache accounting is not a total-memory bound.

Measure the geometry-cache fingerprint, compression and write costs. Consider deferred lower-priority writes or an uncompressed/appropriately mapped derived format only if measurements justify it. A failed cache write must remain nonfatal. Avoid fingerprinting the same unchanged mesh repeatedly.

For `extracted_index`, initial construction validates every entry and cache hits still clone the mapping/list structure. Consider immutable shared snapshots with narrowly invalidated additions rather than copying a large catalog into each consumer. Preserve access-time containment and existence checks.

**Acceptance:** Warm reopening after 30 seconds avoids unnecessary preparation when dependencies are unchanged. Editing a DDS fragment, material, model or build membership invalidates the correct results. Separate builds with identical basenames never cross-contaminate.

### P2-C: Reduce map preparation and residency work before upload

Move the earliest partial scene publication ahead of optional terrain atlas and full material work. Stream terrain chunks rather than requiring the entire tuple to be available before the first useful result, while retaining verified-source and export rules.

Cache archive inventories/provider mappings by validated build/archive identity so each dependency iteration does not repeat broad discovery. Update the scoped index with newly imported providers. Keep exact-path resolution, ambiguity diagnostics and complete split-DDS dependency checks.

Current residency requests iterate over the scene to rank distances/facing and can request the whole candidate set, not a strict visible subset. Add a spatial index and view-frustum/working-set selection, with a margin to avoid pop-in. Add bounded hysteresis so slight camera moves do not repeatedly evict/reload the same assets.

`SceneResidency.load` hashes `im.tobytes()` while deduplicating decoded images, including already resident assets. Carry a stable decoded-content/source identity from decode/cache time instead of repeatedly converting and hashing entire images on camera updates. Preserve deduplication and account for shared bytes correctly.

**Acceptance:** Small camera movements do not rescan/decode/hash the complete scene. Cancellation and generation changes stop old publication. Object placements and available assets remain complete even when only a visible working set is resident.

### P3: Renderer modernization, only after the earlier measurements

The inspected model/map draw paths pass NumPy arrays through `glVertexPointer`, `glNormalPointer`, `glTexCoordPointer` and indexed draw calls. There is no persistent VBO/IBO setup in those inspected draw functions. Assess persistent GPU geometry buffers, per-resource updates and instancing for repeated map objects if frame submission is significant.

This is primarily a frame-rate/submission optimization, not a solution to archive decryption or material discovery. Keep animation upload requirements explicit. Avoid a wholesale renderer rewrite in the same change as importer work.

For supported DDS formats, direct compressed texture/mipmap upload could avoid CPU decompression, RGBA expansion and mip generation. Treat this as a separate feature with strict format/capability gating and fallback. Preserve split-stream reconstruction, orientation, alpha semantics and normal-map handling. Preview improvements must not reduce full-resolution exports or silently downgrade final 2K/4K appearance.

## 6. Proposed budgets and benchmark scenarios

These are **starting acceptance targets to calibrate**, not measured current performance or promises for every machine:

- A loading shell/acknowledgment within roughly 200 ms for user-triggered operations.
- GUI event-loop delay below 50 ms for most work, with no unexplained stalls over 200 ms.
- Cooperative cancellation within 250–500 ms at bounded CPU/I/O checkpoints; report exceptional uninterruptible driver/native calls.
- GPU upload scheduling initially around 2–4 ms of CPU submission per frame, with byte limits and explicit overrun reporting.
- Warm operations demonstrably reuse work; no percentage target until cold/warm baseline data exists.

Benchmark a small static model, a full character with equipment/animations and 4K materials, a previously unseen map, the same prepared map on reopen, camera traversal across repeated map objects, one large sound/video PAK, a many-small-file PAK, and a mixed multi-PAK import. Include a large library and 25,000-object synthetic UI case.

For each, record input counts/bytes, hardware, packaged/source mode, cache condition and quality. Compare identical inputs and quality. Never report a reduced-quality placeholder time as final-quality load time. On real GL hardware, record first geometry, first textures, final textures and stable frame times separately.

## 7. Regression plan for the implementation agent

Existing suites to extend, grouped by concern:

| Concern | Existing tests under `source/` |
| --- | --- |
| Model/texture correctness | `test_gpu_preview.py`, `test_gpu_texture_quality.py`, `test_texture_resolution.py`, `test_texture_pixel_cache.py`, `test_asset_streaming_models.py` |
| Preview/export separation | `test_textured_export.py`, `test_preview_performance.py` |
| Browser/index behavior | `test_qt_unified_explorer.py`, `test_qt_asset_preview.py`, `test_asset_library_preview.py`, `test_multi_workspace_editor.py` |
| Map UI/scene/dependencies | `test_qt_level_editor.py`, `test_qt_level_renderer.py`, `test_level_map_loading.py`, `test_level_dependency_loader.py`, `test_level_asset_mount.py` |
| Terrain/export | `test_level_terrain_decode.py`, `test_level_blender_export.py` |
| Import/archive variants | `test_asset_library.py`, `test_asset_library_bulk.py`, `test_historical_library.py`, `test_legacy_pak_workspace.py`, `test_shared_pak_cache.py` |

Do not assume files named “performance” benchmark the production GPU path; some exercise legacy CPU/Tk previews. Offscreen tests are useful for state and correctness, but this code explicitly changes its execution mode offscreen. Run real-window GPU benchmarks on representative Windows hardware and verify packaged native dependencies separately.

Add deterministic tests for upload/delete counts on unchanged residency; stale-result suppression; cancellation within a large entry; incremental XML/CRC correctness; invalidation after same-basename cross-build changes; corrupted derived-cache fallback; context recreation; and immutable cached geometry under animation. Use timing assertions mainly for controlled benchmarks, not fragile CI unit tests.

**Known baseline caveat:** During the previous PR work, `test_layout_presets_remain_semantic_after_changing_sides` failed in the offscreen environment with explorer width 331 versus preview width 856. It also failed using the earlier PR-head editor. Reproduce and track it separately; do not count it as a new performance regression or silently remove the assertion. The previous 43 passing tests were not an end-to-end performance benchmark.

## 8. Invariants and recommended delivery order

Keep originals read-only; preserve signature/CRC/source-mutation checks, atomic manifests, archive path validation and explicit incomplete states. Retain exact virtual paths/build isolation, animation correctness, full-resolution export, 2K/4K final texture quality and responsive cancellation. Optional cache failures must not block opening an asset. Avoid disabling antivirus or reducing validation as a claimed optimization.

Qt UI/model mutation belongs on the owning thread. The simplest first implementation keeps GL calls on the widget context owner and sends CPU-prepared data through queued results. Shared worker GL contexts are possible, but require explicit ownership and synchronization; they should not be introduced casually. See [Qt's QOpenGLWidget documentation](https://doc.qt.io/qt-6/qopenglwidget.html) and [QAbstractItemModel documentation](https://doc.qt.io/qt-6/qabstractitemmodel.html). Confirm APIs against the package's supported PySide6 versions. See [Python's threading documentation](https://docs.python.org/3/library/threading.html) for the GIL limitation in conventional builds; do not assume a free-threaded runtime.

Repository conventions also require brief public release notes, exclusion of Developer features from public release notes, authentication on each Developer entry and no persisted unlocked state. Performance work must not alter those behaviors.

Recommended sequence:

1. Add instrumentation and capture reproducible baselines.
2. Address first-map synchronous parsing, search rebuilds, stale model work and unnecessary map GPU invalidation.
3. Add staged model results and bounded texture uploads.
4. Optimize and validate native bulk crypto, then streaming extraction and measured concurrency.
5. Improve dependency-aware caches/indexes and map working-set selection.
6. Consider persistent GPU buffers/instancing and compressed DDS upload only if remaining measurements justify them.

For each change, deliver the concrete cause, before/after spans under identical conditions, maximum memory/queue behavior, correctness tests, cancellation behavior and packaging requirements. Describe remaining unknowns explicitly. The desired outcome is a manager that becomes useful sooner, completes real work faster and remains correct under large assets—not simply one with more background threads.

## 9. Implementation progress and repeatable benchmark commands (October 15, 2026)

This appendix records work delivered on PR #76 after the original report's inspected commit. The report's acceptance criteria in sections 4–8 remain authoritative; an implementation claim does not substitute for evidence from the required representative benchmarks.

### Implemented code paths

- Model file-tree and map outliner search changes are debounced at 200 ms. Full tree virtualization and incremental row insertion are **not implemented**.
- Model preparation now checks a cancellation signal at CPU-stage boundaries and rejects obsolete cache publication. Deep decoder-level cooperative cancellation and geometry-first CPU staged publication remain open.
- The GPU model preview displays verified geometry before completing all diffuse, normal and specular texture uploads. Each paint submits at most one original-dimension texture (with mipmaps where available). Real Windows GL measurements, GPU completion timing and upload byte budgets are still required.
- Initial Map Editor opening now uses a worker to parse placements and a cancellable nonmodal loading shell; the widget receives the already prepared project. Project construction from direct standalone editor callers still supports the old synchronous fallback.
- Map texture streaming preserves GPU uploads for unchanged live image objects and the independent terrain atlas; CPU residency reuses immutable decoded-image fingerprints where identity remains stable.
- Map residency now uses a scene spatial grid to find nearby placement material variants, with retention margin for previously loaded resources. Validate view-frustum coverage and camera motion against representative maps; do not assume this synthetic index is equivalent to full frustum-culling or camera path benchmarks.
- Signed method 13/14 Asset Library imports use bounded CTR/DEFLATE streaming, validate signed entry sizes and CRC, retain final source SHA-256 checks and atomic manifest publication, reuse worker-owned source handles/cipher schedules and poll cancellation per chunk. Original method-13/14 semantics and ZIP import remain separate. Native Windows bulk Twofish block encryption and clean-machine dependency packaging remain **open**.
- `performance_trace.py` records opt-in, monotonic, path-free stage spans to `DATA_HOME/Diagnostics/performance-spans.jsonl` when `EVOLVE_PERF_TRACE=1`. Startup import/first-swap/GPU completion telemetry needs further work.

### Reproduce initial measurements

From the `source/` directory in an environment containing the program's supported Python/Qt/decryption dependencies:

```powershell
python performance_benchmarks.py spatial --repeats 5
python performance_benchmarks.py crypto --repeats 5 --bytes 1048576
python performance_benchmarks.py map --workspace "<extracted-level-workspace>" --repeats 5
python performance_benchmarks.py model --workspace "<extracted-model-workspace>" --relative "Game/characters/example.skinm" --repeats 5
$env:EVOLVE_PERF_TRACE = "1"
python qt_manager.py
```

These emit median/min/max and per-run times. **They do not themselves benchmark real OpenGL first geometry/final texture frames.** Retain locally the machine/OS/GPU/driver, game build, package version, cache condition (fresh import, derived-cache-cold, warm repeat, source edited), representative workload, memory and cancellation findings. Never share private source paths, user account identifiers, game keys or entire source PAKs in an issue log.

### Remaining release gates

- CI passes on the **exact release candidate SHA**, including native Windows Qt, GFX, map/Blender, signed PAK, materials, updater, installer and portable packaging. Any earlier green job against a superseded SHA is not sufficient.
- Ship and validate a maintained license-compatible native bulk Twofish backend on a clean Windows installation, with differential randomized real-cipher and counter-offset tests. Present code reuses schedules and vectorizes XOR but does **not** have this backend.
- Replace large item-based Qt trees/outliners with virtualized/lazy models while preserving exact virtual path selection, 25,000-object full-text search and cancellation of stale UI jobs. Debounce alone is not completion.
- Validate dependency-fingerprinted cache invalidation for split DDS and material fragments, build isolation, externally modified source files, 30-second-plus warm reopening, and correct memory accounting. Current cache TTL remains bounded at 30 seconds.
- Profile/rework remaining background warmup priorities and deep model/terrain partial publications, input/read/inflate/write stage budgets, spatial/frustum selection and GPU buffer submissions. Confirm final 2K/4K material quality and rigging are unchanged.
- Record at least five representative before/after samples and the actual packaged Windows GPU first-geometry, first-texture and final-quality frame milestones, plus cancellation latency and peak memory. Measure a map, complete character and large signed PAK on real assets.
- Change stable version and publish **v1.2.2 only after the above gates have succeeded**, as requested. Do not publish just because a document or synthetic CI tests exist. Keep PR #76 draft until then.


## 10. Second implementation pass and verification boundary (October 15, 2026)

This dated appendix supersedes implementation-status statements in §9 **only** where the code has subsequently changed. Requirements and correctness rules in §§4–8 remain in force.

### Newly implemented since the first pass

- **Native Windows bulk Twofish:** `native_twofish_bulk.py` is a per-worker libgcrypt ECB batch provider for original CryEngine little-endian CTR counters; the existing Python Twofish binding remains the fallback. CI Windows release workflows now install signed MINGW64 MSYS2 libgcrypt/libgpg-error packages, stage only approved DLL names, include available runtime dependencies, carry COPYING/COPYING.LIB + notices and require the packaged worker smoke test to execute the original 128-bit Twofish known vector through the native path. Windows desktop/package compatibility and actual speedups are **not yet demonstrated** by a completed release build.
- **Virtualized asset/navigation trees:** `VirtualTreeModel` implements a lazy, paginated `QAbstractItemModel` with stable virtual-path roles, 256-row batches and on-demand size checks. Large File Explorer inventories, map outliners and map content browsers use virtual trees, while smaller projects retain the current familiar widgets. Search still filters the complete catalog rather than only visible rows. Regression fixtures include 3,500 file records and 25,000 logical items. This is not a claim of 50 ms GUI budgets until measured on the actual Windows binary.
- **Five-minute dependency-checked prepared-model cache:** unchanged entries retain the existing 384 MiB/three-entry limit, and cache reuse re-stats a bounded watch list of model, resolved MTL, known diffuse DDS and split DDS fragments, parent folder changes and owning provider manifests. Source edits, missing files and separate build workspaces invalidate affected entries. The original derived-geometry and texture caches remain independent. Extra material/normal/specular provenance not surfaced by the resolver needs real-assets verification.
- **Staged model CPU publication:** verified decoded geometry and prepared arrays are sent over a generation-aware Qt signal *before* material and animation discovery. A visibly incomplete preview is identified as such. The final stage installs full model materials and native animations; the existing GPU viewer stages full-resolution diffuse/normal/specular uploads across paints. Final upload triggers a subsequent repaint. `frameSwapped` markers differentiate first visible geometry, first texture and final-quality frame from CPU submission timing.
- **Deep cooperative model cancellation:** character CDF assembly, individual part loading, material/DDS resolution and CAF/DBA catalog discovery now check the worker cancellation token. Old results remain generation rejected. Uninterruptible native image decode and driver operations must still be timed on real assets.
- **Earlier map placement publication:** assembled source placements are delivered to the Qt scene before optional terrain and tile-atlas preparation, with `pending=True`. Terrain, atlas and CPU material residency follow without changing level export rules. A spatial working-set index, unchanged texture retention, weak image fingerprints, per-frame map upload byte budgeting and first geometry/texture/final-visible-frame markers reduce unnecessary repeat work and expose overruns.
- **User-first background startup:** optional shared/default PAK warmups yield to active previews; installed game default-PAK discovery runs off the Qt event thread. An opt-in startup observer records the main window's first paint and first accepted input, with event-loop heartbeat and sanitized GPU vendor/renderer/version metadata.
- **Bounded signed import:** verified signed PAK method 13/14 entries use source-handle/key-schedule reuse, bounded CTR chunks and raw DEFLATE drains, per-entry rolling hashes/CRC and cancellation, original source SHA-256 comparison before atomic manifest publication. Read/CTR/inflate/write stage times are emitted separately when tracing is enabled. Twofish reference compatibility, corrupt-entry refusal and original historical ZIP routing remain required.

### Added focused regression suites

`test_native_twofish_bulk`, `test_qt_virtual_tree`, `test_model_dependency_fingerprint`, `test_model_geometry_stage`, `test_model_deep_cancellation`, `test_background_warmups`, `test_level_early_publication`, `test_map_frame_milestones`, `test_pak_streaming` and earlier test fixtures are wired to appropriate non-publishing Qt, materials, and map workflows. These are newly written source-level tests; **do not call them passed unless their exact-head CI run succeeds**.

### External acceptance evidence still required before merging or releasing v1.2.2

1. Run the complete Windows CI / GFX / materials / software-GL map / signed PAK / installer / portable ZIP suites against one **final immutable commit SHA**. GitHub Actions has been queued and repeatedly cancelled for superseded heads during rapid development.
2. Execute a clean-machine Windows installer and portable ZIP, and verify the native libgcrypt DLL dependencies, actual bulk crypto dispatch, corresponding licenses and known vectors in the packaged application. Confirm the original reference fallback still works without the optional DLL.
3. On representative Windows hardware and **real game assets**, capture at least five comparable before/after measurements for startup/first interaction, complete-character geometry/material/animation readiness, initial map/terrain, moving through repeated map instances, and sound/video/mixed PAK extraction. Capture peak RAM, cancellation latency, full-resolution final quality and real GPU vendor/driver. No figures should be fabricated or inferred from source code.
4. Any P3 renderer VBO/instancing or compressed DDS path should be implemented only if the preceding measurements identify that particular bottleneck. A risky full renderer rewrite is expressly *conditional* in §5, not a release requirement without evidence.
5. Review possible regressions around original map/file selection, Windows DLL distribution, source-changed DDS fragments, material ambiguity and native animation/rigging; release only if they remain correct.

The implementation is pushed to **draft PR #76**. It is **not** merged or published as v1.2.2. The external acceptance items above cannot be honestly marked complete from a source-only remote edit session.
