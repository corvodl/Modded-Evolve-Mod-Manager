# Character stage assembly validation

The character viewer now recognizes CA_SKIN_UNIQUE attachments, reads the actual mesh subset count instead of its flags, and resolves extensionless material names from verified MtlName 0x802 chunks. Packed half-position/color/UV streams are supported. Monster stage labels preserve their source names (Teleporter1, Teleporter2, Teleporter3, etc.).

Monster and creature assemblies retain their skin body parts. Hunter body accessories stay assembled while separate equipment remains selectable. Primary skin bind matrices establish the export rest pose; unused accessory bones cannot override weighted body bones. Accessory palettes are mapped by bone name, allowing flattened accessory hierarchies. Missing bones, primary hierarchy mismatches, conflicting weighted bind poses and malformed palettes remain errors. Eight-influence skins retain JOINTS_1/WEIGHTS_1 in export and playback.

## Read-only local asset checks

Against the extracted Asset Library on 2026-10-12:

- 123 discovered CDF-backed entries successfully assembled and generated rigged GLBs, including body, creature and equipment definitions. This count is not 123 distinct playable characters.
- All 123 resolved at least one used diffuse texture; no material-association failures were reported. Source shader features such as animated material modifiers remain approximations.
- Teleporter2 diffuse preview was rendered and inspected: 20,987 vertices, 36,670 triangles, five subsets correctly assigned to its body material.
- 76 actor models passed expanded animation checks: 24 monster stages (eight families), 29 hunter models/variants, and 23 creatures. Every one of 14,672 converted DBA clips was parsed and sampled at its midpoint for finite transforms; three representative motions per actor were sampled for vertex deformation. This is not a visual review of every frame of every clip.
- All 19 available standalone native CAF selections in this inventory passed export/playback checks. 442 layered references were classified as requiring a base animation, including Kala's jetpack deploy CAF whose additive flag is absent from its name. Layered motions are not falsely advertised as standalone playback.
- The reported Goliath3 uphill/right run clip was separately tested through the character-preview conversion path: 198 channels and 199 bones, playable without the incompatible ankle-rest error.
- Lennox exposed a DDS resize defect: premultiplied RGBA filtering erased RGB where auxiliary alpha was zero. Color and alpha are now resized independently; original torso mean RGB (48.94, 41.17, 36.39) is preserved instead of collapsing to approximately (1.88, 1.38, 1.13). A zero-alpha DDS regression covers the shared hunter/creature/monster decoding path.
- Explicit material AlphaTest cutoffs now survive assembly, CPU/GPU preview, GLB export and animated reimport. Character assemblies honor material transparency by default; the manual ignore-alpha diagnostic remains available. Opaque diffuse auxiliary alpha remains ignored.

## Regressions

113 tests across 16 local modules passed, including 12 synthetic character-stage tests. The viewer-materials CI workflow includes the new tests and executes additional archive suites on Linux. Synthetic fixtures cover distinct stage labels, unique skins, packed vertices/UVs, subset flags/counts, extensionless material lookup, monster body parts, persistent hunter accessories, differing CHR/skin rest poses, rejected weighted conflicts eight-influence playback/export, additive CAF flag classification and transparency-cutoff round trips.

No original game, installed application or Asset Library files were modified. No game model/texture/animation bytes are included in the repository. Procedural attachment physics and rigid CA_BONE socket geometry remain outside this skin-assembly path. Existing equipment selection does not claim to simulate gameplay loadouts.
