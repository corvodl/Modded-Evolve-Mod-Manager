# ParasiteHost 2012 texture findings

Read-only validation against the supplied Oct 30, 2012 build (17947), on
October 14, 2026. No proprietary game payloads are included in this repository.

The main character body textures are streamed across two archives beneath
`build_3/Game/streaming/`:

| Virtual texture | dds_low.pak | dds_high.pak | Encoding |
| --- | --- | --- | --- |
| objects/characters/monsters/parasitehost/parasitehost.dds | .0 through .4 | .5 | DXT1 |
| objects/characters/monsters/parasitehost/parasitehost_ddn.dds | .0 through .4 | .5 | CTX1 |

All 12 fragments passed ZIP CRC verification. Each texture header declares
512 x 512 and eight mips. Fragment .0 includes the DDS header and the three
smallest mips; fragments .1 through .5 progress toward the highest mip.
There is no standalone DDS file at either exact virtual body texture path
in this installation. Preserve the fragment names and virtual directories.

`Objects.pak` also contains a different texture set beneath
`Objects/characters/monsters/cocoon/ParasiteHost/`. Do not substitute it by
basename for the main character's textures.

The material resolver was exercised with the actual extracted, build-specific
character and texture records. DDS reconstruction produced a 512 x 512 image,
but visual inspection showed scrambled pixels. A diagnostic 16-bit byte swap
changed the colors without fixing the spatial layout. This Xbox 360 build's
platform texture layout still requires validated conversion; successful Pillow
decoding must not be described as correct rendering. No conversion is shipped.
ParasiteHost stages 1, 2, and 3 resolved visible material slots 10 (Host_Body)
and 11 (Host_Saliva). These are lookup results, not visual or in-game validation.

CryEngine's [Resource Compiler documentation](https://www.cryengine.com/docs/static/engines/cryengine-5/categories/23756816/pages/44959586)
explains that PC and console texture encodings can differ. That is supporting
context, not proof of the precise conversion required for this 2012 payload.

The body normal map is present but requires a CTX1 codec, as does the known
spikes normal map. Importing it does not enable normal-map decoding. Legacy
CryTek bone/weight and skeleton validation are still required before DBA
playback; this release does not add those decoders.

To reproduce in the manager, assign Objects.pak, dds_low.pak and dds_high.pak
to the same 2012 build in the Asset Library. Open the character with that build
selected. Diagnose Texture Links should resolve the body's exact virtual
path and retain the unsupported CTX1 diagnostic. Original archives must remain
unchanged. A detached character export needs both streaming sets supplied
with their original paths, or a texture folder explicitly selected by the user.
