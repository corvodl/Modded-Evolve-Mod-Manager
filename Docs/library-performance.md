# Library loading and export performance

Baseline: beta commit `63544b4`. Measurements below are preparation steps,
not promises about total UI, map rendering, or export duration.

## Findings and implemented changes

1. `library_model_index` parsed the entire selected manifest inside its entry
   loop. For the local 2012 Objects PAK this meant 8,314 repeated JSON reads.
   Read its archive name once. Index construction is no longer quadratic in
   manifest size.
2. `build_index` previously indexed all imported builds before filtering.
   Pass the archive membership into `extracted_index` before file validation;
   cache entries are separated by library root and membership. The existing
   bounded index cache and manifest-change invalidation remain in use.
3. Reading or exporting a single asset validated every file in its PAK.
   Select manifest paths before filesystem checks for native file reads/copies,
   DDS viewing/PNG export and the initial character export lookup. The selected
   file still receives path, symlink, presence and size checks. Complete archive
   exports still inspect all files being copied.
4. Map mounting resolved each indexed file twice and walked its parents to
   recover an archive identity already present in the index. Resolve map
   ownership once per archive, keyed by both library root and archive ID.
   Keep the compatibility path for untagged indexes and preserve map fencing.
5. Shared textures were decoded/resized repeatedly across materials and models.
   Cache decoded pixels by SHA-256 of the assembled DDS bytes, decode mode and
   requested dimensions. This benefits viewer and export callers. Copies keep
   callers independent; an LRU limits retained pixels to 128 MiB (four bytes
   per pixel accounting) and 128 images. Files/stream fragments are still read
   and assembled so changes cannot silently reuse old pixels. No quality or
   format support was reduced.

Import authentication, explicit library verification, build/install checks,
ambiguity detection and original-file protection have not been removed.
Routine viewing does not add full PAK verification. No proprietary assets are
included in this PR.

## Measurements

Run `python source/benchmark_library_loading.py` on Windows. The synthetic fixture
has 1,200 selected files and 2,400 files in an unrelated archive. Both runs used
the same Python environment and machine; these are individual runs, not a
statistical benchmark. Mount timing follows index warming deliberately.

| Operation | Before | After initial index changes |
| --- | ---: | ---: |
| Model index, cold | 1.1421 s | 0.3867 s |
| Model index, warm | 0.6174 s | 0.1893 s |
| Export one native file | 0.2864 s | 0.0048 s |
| Map mount, warm index | 0.3675 s | 0.0049 s |

Cold model-index manifest reads fell from 1,207 to 7. Warm reads fell from
1,205 to 6. Call-count regressions guard against reintroducing this behavior
without fragile timing assertions in CI.

Read-only measurement against the local imported 2012 Objects PAK and its build
dependencies: 29,241 indexed paths, 20.373 s cold and 4.303 s warm after changes.
There is no controlled before measurement for that real library; an earlier
long-running investigation is not used as a quantitative baseline.

## Remaining profiling targets

- `imported_archives` reparses manifest JSON to produce summaries on catalog
  calls. A bounded summary cache with reliable invalidation could reduce this.
- Cold index construction still validates files in the selected build. Lazy
  per-file validation would require auditing every consumer of index records.
- `_content_fingerprint` hashes conflicting candidates repeatedly. Caching only
  by name would break conflict detection; measure this separately before adding
  a stat-keyed or request-local cache.
- `prepare_scene` includes archive dependency resolution, scene assembly,
  geometry decode, terrain tiles/atlas generation, material work and GPU upload.
  Geometry/residency caches already exist. The mount benchmark does not measure
  those phases, and end-to-end representative map timings remain needed.
- Full map GLB output still needs unique geometry, images and serialization.
  Shared decoded pixels reduce repeated texture work, but no end-to-end GLB
  speedup or animation/rig change is claimed.

## Validation

82 focused synthetic tests passed locally, covering build isolation, imports
used by previews, damaged selected files, native/PNG/GLB export behavior,
map ownership, normal maps and legacy material resolution. New tests cover
bounded manifest reads, avoiding unrelated file stats, isolated scope caches,
pixel content/quality changes, caller independence and texture-cache eviction.
The PR adds a Windows CI workflow for this suite. No full release build or
interactive game/map smoke test is represented by these results.
