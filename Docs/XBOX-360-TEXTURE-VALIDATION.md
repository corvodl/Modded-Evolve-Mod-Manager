# Experimental 2012 Xbox 360 CryEngine texture preview

This applies only to the **2012 Xbox prototype**, **not retail Legacy Evolve
or Stage 2**. It changes no originals and cannot rewrite the console's
native PAK textures. It is a read-only visualization/export transform.

The manager looks for an exact original `CryTek 0x745` .chr in the selected
build and the observed Xbox DDS reserved surface tag (`0x1800` or `0x1040`)
before running console decoding. Ordinary PC DDS remains untouched.

## Implemented

- Reconstruct exact split .dds.N mips using existing virtual path checks.
- Untile the highest complete 2-D block-compressed mip using the Xbox Xenos
  32×32-block addressing scheme with bijective bounds validation.
- Convert the big-endian compressed 16-bit words to PC order.
- Decode BC1/DXT1 and BC3/DXT5 with the normal Pillow / normal-map pipeline.
- Decode console-only CTX1 R/G data and reconstruct tangent-space Z for
  explicitly declared normal-map slots.
- Feed the same preview material images to Qt GPU viewing and Blender export.
- Retain read-only Asset Library and existing imported DDS editing safeguards.

Small mip-tail XG padding and unusual shapes remain unsupported. The preview
requires a complete highest mip (at least 128×128 pixels) and refuses partial,
ambiguous or inconsistent surfaces. Do not reinterpret other build formats.

## Real sample evidence

Only the following **user-supplied** textures were examined locally, without
committing their bytes:

- `Parasite_Host_Spikes.dds.5` — 512×512, DXT1, block untile + 16-bit word
  order yielded a coherent red spikes UV atlas.
- `parasitehost_spec.dds.5` — 512×512, DXT5, transformed into a coherent
  grayscale/red material atlas.
- `Parasite_Host_Spikes_ddn.dds.5` — 512×512, CTX1, gave a coherent blue
  normal-map atlas with reconstructed Z.

The original **body diffuse** and **body normal** were subsequently supplied
as extracted copies of the 2012 `Game/streaming/dds_low.pak` and
`Game/streaming/dds_high.pak`. Both sets have been checked directly:

| Exact virtual file | low archive | high archive | Header | Top mip |
| --- | --- | --- | --- | --- |
| `objects/characters/monsters/parasitehost/parasitehost.dds` | `.0`–`.4` | `.5` | DXT1, reserved tag `0x1800`, 8 mips | 512×512 |
| `objects/characters/monsters/parasitehost/parasitehost_ddn.dds` | `.0`–`.4` | `.5` | CTX1, reserved tag `0x1040`, 8 mips | 512×512 |

All 12 numbered entries extracted cleanly, and each complete chain has
`174,888` bytes (128-byte header and `174,760` compressed payload bytes).
Fragment sizes are `296, 512, 2048, 8192, 32768, 131072` bytes respectively;
the `.0` contains the smallest three mips, not a usable 512-pixel preview.
Neither stream is missing any high-resolution fragment.

On these actual supplied files the Xenos block untile plus 16-bit byte swap
produced a **recognizable, coherent diffuse UV atlas**, rather than the
multicolored corruption produced by a linear DDS decoder. CTX1 R/G unpacking
and Z reconstruction produced a coherent purple/blue normal-map atlas.
This validates the *image conversion on these two inputs* but **does not
establish final material UV placement, shader normals, or appearance inside
the manager/Blender**. Those remain manual local acceptance checks.
The original ZIP members were never edited or included in commits.

Do not substitute textures from `monsters/cocoon/` or another build.

## Optional local PNG diagnostic

Use `source/xbox360_texture_diagnostics.py` with an unmodified 2012
`.chr` and all exact `.dds.N` fragments. It saves a NEW PNG outside the
Asset Library, verifies every source part and never modifies originals.

```powershell
py -3.11 source/xbox360_texture_diagnostics.py `
  --model "C:\Temp\ParasiteHost3.chr" `
  --fragment "C:\Temp\parasitehost.dds.0" `
  --fragment "C:\Temp\parasitehost.dds.1" `
  --fragment "C:\Temp\parasitehost.dds.2" `
  --fragment "C:\Temp\parasitehost.dds.3" `
  --fragment "C:\Temp\parasitehost.dds.4" `
  --fragment "C:\Temp\parasitehost.dds.5" `
  --output "C:\Temp\ParasiteHost-body-decoded.png"
```

Paths above are examples only. Use actual extracted PAK filenames (which
can reside in different PAK import folders). Supply `--normal` when
previewing CTX1 `_ddn` normals; the normal-map channels are reconstructed
only with that explicit switch. The output filename must not exist.

## Local acceptance

1. Import both source streaming PAKs into the same registered 2012 build.
2. Open `ParasiteHost3.chr` (do not import edited content into originals).
3. Check body diffuse, specular, spikes, saliva and CTX1 normal appearance.
4. Export the model through the existing Blender GLB path; verify material
   slot 10 body, slot 11 saliva and matching preview/export PNGs.
5. Compare the scene against actual source-game screenshots. If it differs,
   provide diagnostic paths and example images, not a conclusion of success.

Synthetic tests:

```powershell
$env:PYTHONPATH="source"
py -3.11 -m unittest source.test_xbox360_dds -v
```

No release is published from this PR.
