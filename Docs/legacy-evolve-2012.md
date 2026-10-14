# Historical Evolve 2012 PAKs — editing and transfer research

This tool is separate from the Evolve Stage 2 signed CryPAK editor. It accepts
ordinary ZIP-compatible .pak archives whose entries use stored (method 0) or
DEFLATE (method 8). It does **not** treat these archives as Stage 2 signed PAKs.

## Using the legacy editor

In **Modding → File → Edit Legacy Evolve (2012) PAK…** (also in **Asset
Library → File**) select one extracted PAK such as Scripts.pak, Textures.pak,
Sounds.pak or Videos.pak. If the files arrived inside Game.zip or an old RAR,
extract the individual PAK files first. The editor creates a sparse project
under the manager's Projects/Legacy_PAK_Projects directory; the input remains
unchanged.

Browse files, search by virtual path, edit small text/Lua/XML/schema files,
export any native file, import replacement bytes, revert overrides, and build
a **new** unsigned legacy PAK in a separate output location. The build copies
unmodified entries from their original archive, preserves member timestamps
and compression method, and verifies every CRC before publishing the output.
Never overwrite the original file or game installation.

DDS previews can reconstruct numbered .dds.0–N streams and companion .dds.0a–Na
8-bit alpha planes from the older CryEngine packaging scheme. Unsupported
compression, including CTX1, remains exportable/replacement-capable as raw
bytes. Highest available mip preview/export may still depend on the local DDS
decoder for that particular format. FSB/FEV and USM are binary-export and
replacement workflows, not built-in audio/video playback.

Only archives matching this known ZIP layout are accepted; archives with
unsafe entries, ZIP encryption, symlinks, unknown methods or extreme sizes
are rejected. No signing keys, injectors or running games are used.

## Taking early-build assets into Stage 2

Early-stage textures and images, static UI assets, videos, and simple XML/Lua
definitions are reasonable research candidates, **not guaranteed conversions**.
Copying an entire historical PAK into Stage 2 will not work: the signing
requirements, CryEngine asset layouts and game schema have changed.

Recommended offline-only experiment:
1. Export one asset from the legacy editor.
2. Find its closest equivalent in a verified Stage 2 PAK and compare metadata.
3. Convert the format and dimensions as needed.
4. Use the existing signed Stage 2 Modding project tools to stage a copy.
5. Observe rendering and offline logs; never assume functional compatibility.

Audio events need FSB/FEV remapping; maps, rigged models and animations may
need substantial engine-format conversion. This editor does **not** implement
a one-click transfer, launch an old game, or alter multiplayer code.
