# Beta.28 level and Blender export development status

This is an experimental implementation note, not proof that the level editor or beta.28 release is finished.

## Verified terrain source evidence

Two original signed map PAKs were inspected read-only using the existing CryPAK verifier and importer. Tutorial includes a source HeightmapData block with 512 by 512 little-endian 16-bit height samples (524,288 bytes, authored MaxHeight 256). Desert Canyon contains 1024 by 1024 samples (2,097,152 bytes, authored MaxHeight 1024). Both have the same named section, duplicated length fields and a following HeightmapLayerIdBitmap_ver2 section. Authored terrain elevations matched the map's positioned roads and decals when the first source grid dimension was mapped to world X and the second to world Y. Neither source PAK nor any original Asset Library file was modified.

The experimental editor now triangulates verified source heights and draws bounded source-coordinate terrain in the existing OpenGL viewport. Terrain is currently lit but **not authored-texture-mapped**, editable, engine-ready or rebuilt as collision/navigation data. Synthetic tests cover elevation, orientation, triangle winding, malformed fields and OpenGL framebuffer rasterization.

## Managed dependency loading

A worker enumerates the selected map's top-level source PAK, compiled level.pak and terraintexture.pak where installed, then scans verified shared-archive inventories for exact models/materials/DDS resources and streaming fragments. Extracted dependencies are placed in a separate managed cache, not the original library. Other-map root-relative assets are fenced out; ambiguous shared providers remain reported rather than guessed. Map selection can construct a separate editable project copy, with direct signed-repack disabled for source-derived copies.

## CDF GLB rigging

Compatible assemblies retain their common skin. Incompatible native bind poses now export independently skinned mesh parts, each with its own correctly validated inverse-bind armature, joints, weights and material assignments. This prevents the previous unconditional unrigged fallback. Independent armatures are not yet retargeted into one animation-ready shared skeleton. Actual Blender import and bone-pose verification remain outstanding.

## Known release blockers

Compiled terrain, cooked terrain tile texture orientation, vegetation instance payloads, prefab/archetype expansion, procedural road meshes, portions of designer/merged-world geometry and terrain painting are not verified or implemented. The actual Windows installer, packaging gates, screenshot validation on real maps and Blender bone editing still require verification. Do not publish beta.28 or claim full map coverage on this basis alone.

No copyrighted source asset bytes or private signing keys are included in this repository.