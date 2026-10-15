# Legacy Evolve to Stage 2 transfer proof of concept

Status: **experimental; review-only for maps**. No claim of automatic conversion, playable transferred maps, or restored Legacy modes.

## Evidence and constraints

The user's October 13, 2026 inventory found 380 Legacy and 233 Stage 2 PAK files, with 377 signed Legacy directories and all 233 Stage 2 directories verified. Three Legacy PAKs were empty ordinary ZIP placeholders. These are catalog-verification results, not a proof of full compatibility.

Some sampled model assets share CryEngine CrChF v7 chunk structures. Native geometry still requires per-file validation. The inspected Legacy catalog had no authored Heightmap.dat or .lyr sources, so compiled terrain is not a substitute for a normal editor source project. Differing Legacy LuaQ / Stage 2 Jee script signatures and playlist schemas are not interchangeable without separate research.

The supplied map inventory lists 22 level PAK locations. Desert_Harsh, Forest_Rapids, Forest_Ruins, Ice_Alpine, Ice_Lake, Lava_Depths, Lava_Paths, Defend levels and several DLC levels are absent from the Stage 2 map directory inventory. No map is certified to load in Stage 2.

## New Asset Library workflows

**Prepare Legacy Map Review…** requires a registered Legacy or early build, an imported archive belonging to one map, and a separate registered Stage 2 build. It inventories map-specific compiled level, terraintexture, Level_<map>_data and Level_<map>_ts archives, identifies absent target archives, counts files, and detects incompatible virtual-path collisions within the chosen map. Another map's compiled terrain must never satisfy a local reference.

A user-selected new output folder (outside game/build locations and the Asset Library) receives extracted source files separated by archive identity, verified individually against manifest SHA-256 values. It includes transfer-review.json and a prominent not-deployable notice. Failed or cancelled copies remain marked INCOMPLETE.txt. This bundle never rewrites, signs, installs, mounts or registers a map and does not claim complete global/shared dependencies.

**Review Compatible Legacy Model…** is an opt-in single-file replacement of a matching *existing* model virtual path in an editable Stage 2 project. It verifies both build/archive identities, exact source and target hashes, exact Stage 2 staged PAK ownership and existing project baseline, then checks CryEngine model headers/chunk tables/stream lengths using the current native replacement guard. The user explicitly approves before any change. The original project file is backed up in EditorBackups and only the isolated workspace copy changes; no game or signed PAK is altered. In-game model compatibility is still untested.

All other asset types, including DDS sidecars, materials, CAF/DBA animations, and binary script files remain review-only through the existing generic transfer planner. Neither a GLB export nor a matching container version is evidence that a transferred character will work with Stage 2 skeletons.

## Full imported-build export to a separate Stage 2 comparison

After choosing **Import ALL Game PAKs** into a registered Legacy build and
importing the target Stage 2 PAKs into a distinct Stage 2 build, select that
Legacy build in the Asset Library filter and click
**Export Legacy Build → Stage 2…**. This does not require selecting hundreds
of individual PAKs. Choose the entire imported build or a selected PAK, an
optional virtual-folder prefix, and one of three output types:

- **Compare entire selection:** a complete virtual-path classification
  and SHA-256 inventory JSON without duplicating the extracted payloads.
- **Export changed / absent assets:** a separate review folder containing
  imported Legacy assets whose normalized extracted SHA differs from the
  imported Stage 2 catalog, or which have no imported target match.
- **Export all assets:** the same separate review folder, including exact
  SHA-256 matches. This can require substantial disk space.

Every source file is hashed while exported and stored under the original
imported PAK identifier. Names are compared case-insensitively but no differing
content is picked arbitrarily: duplicate-path conflicts are listed and skipped.
Legacy map files are scoped by map ID so one map's compiled terrain does not
match another's. DDS stream fragments and animation/gameplay files retain
their original bytes for research, not automatic decoding or runtime porting.
Incomplete imported PAKs are listed as **blocked archives** rather than
silently counting as transferred.

The target Stage 2 build is used for provenance, comparative statuses and
future project choices. The exporter **does not write the target build,
modify the Stage 2 game, create new signed PAK entries, or register maps**.
A target path reported absent means absent from the *imported target
catalog* and may still exist elsewhere in the actual game.

For actual editing, use the separately guarded **Review Compatible Legacy
Model…** action for one native model that already exists in a Stage 2
custom-signed editing project. Textures/materials/animations and new map
entries still require separate compatibility checks and/or writer support.

## Gaps before actual playable Legacy map ports

1. Complete build/map-scoped shared dependencies, material DDS streams, compiled nav/collision and resource list audits.
2. Verified added-entry and new-archive packaging, signing, mounting and Stage 2 map registration.
3. Rollback capable of removing new paths as well as restoring existing replacements.
4. Offline runtime validation of Hunt load, terrain, collision, traversal, wildlife, spawns, AI/navigation, scoring, full-round completion.
5. Separate engine/native/script/gameplay/UI checks before attempting Nest, Rescue, Defend, ArenaMode or Evacuation.

Neither original game data nor signing keys or private Data should be committed or packaged.