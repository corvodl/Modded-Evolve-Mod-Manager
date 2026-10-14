# 2012 prototype CryTek `.chr` skeletal validation

**Scope:** Xbox 360-era 2012 build (`CryTek 0x745`) only. This is **not**
retail Legacy Evolve or Stage 2, and no game content belongs in the repo.

The manager now has an experimental decoder for the 48-byte header + 584-byte
historical bone records and four-influence stream type 9. It resolves subset
palettes through the body bone table, then maps them into the CDF master
skeleton by bone **name, ancestor chain and rest transform**. There is no
root-weight fallback. Incompatible or ambiguous data is rejected.

## Run on the actual extracted 2012 files

Run with Python 3.11 and the project's normal dependencies (NumPy, Pillow,
and optional glTF/export dependencies) on your own PC:

```powershell
py -3.11 source/legacy_rig_diagnostics.py `
  --body "C:\Temp\ParasiteHost3.chr" `
  --skeleton "C:\Temp\ParasiteHost3_Skeleton.chr" `
  --dba "C:\Temp\ParasiteHost3.dba" `
  --chrparams "C:\Temp\ParasiteHost3_Skeleton.chrparams" `
  --report "C:\Temp\ParasiteHost3-rig-report.json" `
  --export-glb "C:\Temp\ParasiteHost3-weighted.glb"
```

These are example local extraction paths; choose paths that exist on the
machine. The tool only reads input files, writes new outputs with exclusive
creation, and never changes the source PAK or library originals.

If decoding succeeds, inspect the JSON report for:
- 77 body vs. 89 master bones; unique names, single roots, valid ancestry and
  finite inverse/rest matrices
- Correct body-to-master mapping; **do not assume raw palette indices are
  master skeleton indices**
- Four influences per vertex, every weight sum exactly 255, every vertex
  covered by compatible palettes, including vertices used by multiple subsets
- A real glTF skin with verified active joints and inverse bind matrices
- A **synthetic** 15-degree joint displacement sample (not game motion)
- `database_found`, `clips_decoded`, and `playback_available` as
  distinct properties. A `0xCCCC000D/0x905` DBA is still opaque until
  verified controller decoding or a compatible converter succeeds

## Blender check

Import the weighted GLB into Blender. Confirm the actual Mesh has an
Armature modifier, vertex groups and nonzero weights, and the initial pose
matches the static model. Rotate one known non-root joint and confirm that
only expected weighted vertices deform correctly; then restore the pose.

A passing synthetic test does **not** replace this real-file check.
Do not call the rig supported for ParasiteHost until the report and Blender
check pass. Textures are a separate problem: 2012 Xbox streaming DDS may
still require tiling/swizzle and CTX1 conversion.

## Animation check

The 2012 `ParasiteHost3.dba` container has one opaque `0x905` chunk; one
database chunk is not one clip. The manager can attempt the pinned independent
Cryengine Converter with a copied **actual `.chr` body** and its CDF master
skeleton in an isolated temporary folder. It may not support this historical
encoding. Only converted GLBs whose bone names, parent hierarchy, rest
transforms, channel bounds, and clip timelines pass the existing validators
are exposed as playable animations.

Until this succeeds, no supported animation, scrub, loop or exported Blender
action is claimed. Do not rename the historical `.chr` to `.skin`.
If a converter fails, preserve its error and test only a locally owned copy.

## Automated tests

```powershell
$env:PYTHONPATH="source"
python -m unittest source.test_legacy_crytek_rig -v
python -m unittest source.test_model_rigging source.test_native_caf -v
```

For real validation, share **only the metadata JSON** and optionally the
console error output. Do not publish proprietary model, animation, extracted
PAK, or unredacted local installation data.
