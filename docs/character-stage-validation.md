# Character stage assembly validation

The character viewer now recognizes CA_SKIN_UNIQUE attachments, reads the actual mesh subset count instead of its flags, and resolves extensionless material names from verified MtlName 0x802 chunks. Packed half-position/color/UV streams are supported. Monster stage labels preserve their source names (Teleporter1, Teleporter2, Teleporter3, etc.).

Monster and creature assemblies retain their skin body parts. Hunter body accessories stay assembled while separate equipment remains selectable. Primary skin bind matrices establish the export rest pose; unused accessory bones cannot override weighted body bones. Accessory palettes are mapped by bone name, allowing flattened accessory hierarchies. Missing bones, primary hierarchy mismatches, conflicting weighted bind poses and malformed palettes remain errors. Eight-influence skins retain JOINTS_1/WEIGHTS_1 in export and playback.

## Read-only local asset checks

Against the extracted Asset Library on 2026-10-12:

- 123 discovered CDF-backed entries successfully assembled and generated rigged GLBs, including body, creature and equipment definitions. This count is not 123 distinct playable characters.
- All 123 resolved at least one used diffuse texture; no material-association failures were reported. Source shader features such as animated material modifiers remain approximations.
- Teleporter2 diffuse preview was rendered and inspected: 20,987 vertices, 36,670 triangles, five subsets correctly assigned to its body material.
- All three Goliath and Teleporter stages converted DBA data into playable clips. A sampled clip at three times yielded finite positions and nonzero movement for each stage. This does not establish that every clip on every character was visually validated.

| Stage | Converted clips |
|---|---:|
| goliath1 | 274 |
| goliath2 | 274 |
| goliath3 | 274 |
| teleporter1 | 178 |
| teleporter2 | 177 |
| teleporter3 | 177 |

## Regressions

110 tests across 16 local modules passed, including nine synthetic character-stage tests. The viewer-materials CI workflow includes the new tests and executes additional archive suites on Linux. Synthetic fixtures cover distinct stage labels, unique skins, packed vertices/UVs, subset flags/counts, extensionless material lookup, monster body parts, persistent hunter accessories, differing CHR/skin rest poses, rejected weighted conflicts and eight-influence playback/export.

No original game, installed application or Asset Library files were modified. No game model/texture/animation bytes are included in the repository. Procedural attachment physics and rigid CA_BONE socket geometry remain outside this skin-assembly path. Existing equipment selection does not claim to simulate gameplay loadouts.
