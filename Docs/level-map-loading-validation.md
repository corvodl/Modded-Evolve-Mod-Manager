# Level map loading: review and validation

Based on beta.33 (`c5ce33d70ea87ce41dfc9933f934d8d0e7fafd38`).

## Implemented behavior

- Viewport and export share read-only assembly of editable source placements and complementary mounted map placements. GUID conflicts retain source precedence and report provenance; imported XML is not added to editable ownership.
- Explicit nested entity model properties and nested placement transforms are recognized. Map ownership fences remain in force.
- Dependency discovery repeats after imports, follows material overrides, and discovers streamed texture fragments even with a resident fallback DDS. Verification remains mandatory; inventory reuse is confined to a load.
- Terrain is divided into uniform-detail chunks with matching boundary samples/normals. Preview detail is configurable and bounded; export uses source samples.
- Observed terrain tile envelopes are strictly checked. Atlas previews are explicitly opt-in because real channel order and world mapping have not been validated.
- Logical placements remain separate from camera-prioritized CPU residency. Resource accounting includes shared geometry and diffuse/normal/specular images. Preview uses balanced texture decoding; export reloads original resources independently.
- Generation-scoped progress, cancellation, early scene availability, background map parsing, and bounded per-frame texture uploads extend the existing loading flow.
- Asset-library indexes cache up to four independent roots.

## Tests run

92 tests passed across `test_level_map_loading`, `test_level_dependency_loader`, `test_level_terrain_decode`, `test_level_asset_mount`, `test_level_blender_export`, `test_asset_library`, `test_qt_level_editor`, `test_level_editor`, `test_level_source_metadata`, `test_material_preview`, `test_model_preview`, `test_viewer_material_fixes`, `test_texture_resolution`, and `test_preview_performance`.

Three tests passed in `test_qt_level_renderer`, including a textured terrain framebuffer and stale/cancelled progress checks. Run these separately with `QT_QPA_PLATFORM=windows` and `QT_OPENGL=desktop`. Mixing the offscreen UI test setup with framebuffer tests prevents a valid rendering context; the software backend also mismatched PyOpenGL in the tested Windows environment.

## Remaining limitations / review focus

This is a draft implementation, not a claim of complete engine-equivalent map reconstruction.

- No proprietary assets or real-map benchmarks were available. Tile channel order, gamma, orientation, world alignment, and landmark seams are unverified. Export leaves terrain untextured.
- Adaptive terrain LOD/stitching, detail-material paint-mask blending, prefab/archetype expansion, vegetation and unsupported procedural/compiled geometry remain unimplemented.
- Mounted runtime assembly defaults to mission_mission0. Primary workspace mission/layer activation still needs review against real mutually exclusive gameplay variants.
- Residency prioritizes distance/facing, not a complete visibility scheduler. Old/new result overlap and decoder temporaries can exceed the retained-resource budget; it is not a process peak-memory guarantee. A resource individually exceeding the budget is diagnosed.
- GPU uploads are count/time limited, but conversion and byte preparation still occur in paintGL. One upload can exceed the time allowance.
- Scene/terrain can arrive before object resources, but object resources are delivered in batches rather than individually.
- Initial editor construction and some project-opening paths can still parse synchronously; existing map-selector loads parse on a worker.
- Full-source export retains its existing combined-GLB memory behavior and can require substantial memory.

## Local tester checks

Use original Tutorial and Desert Canyon assets through the verified importer. Check GUID conflicts and parent alignment; source/runtime overlap; inactive gameplay layers; terrain detail and boundaries; tile colors, seams and recognizable landmarks; object texture coverage and streamed fragments; repeated camera movement; map switching and cancellation during each phase; and exported full-source coverage.

Record cold/warm load duration, time to first useful view, CPU and GPU memory, and visible frame stalls. No speedup claim should be made before these measurements.

Original game archives, release notes, version metadata, automatic updater behavior, and protected access controls are unchanged.
