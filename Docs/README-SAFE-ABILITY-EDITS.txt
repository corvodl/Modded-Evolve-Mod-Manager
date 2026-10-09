V2.1 LAYOUT: README-PORTABLE-APP.txt supersedes older folder/dependency
locations below. Build and open commands are at the package root; source is
in source/, build dependencies in .build/, and new app data in Data/.

SAFE ABILITY VALUE EDITS

Start with the working test PAK you confirmed in-game. Install/stage that PAK,
then create a FRESH extraction in the updated manager. Do not reuse an old
workspace or a PAK built by the old XML converter.

This update retains every original binary XML table, offset and string position.
It uses in-place edits when possible. For shared strings or different lengths,
it appends new strings and redirects only the edited field references. Original
node/child/attribute tables remain at their existing positions. Structural XML
changes are still refused. The extended-string path needs in-game validation.
It does not repair a source PAK already built with the older converter.

EXAMPLE: Goliath Charge damage (private bot test)
1. Select Game/libs.pak from the list, then Unpack PAK into a fresh project.
2. Open my files, then libs\Abilities\Goliath\GoliathCharge.xml.
3. Find the DirectDamage effect and change ONLY this attribute:
   <param name="damage" value="403|403|444" />
   to:
   <param name="damage" value="800|800|900" />
4. Save the XML, then Build Mod, Add Mod, Play With Mods.
5. Check Charge behavior/damage, ability descriptions and survival.

403 -> 800 keeps three digits and can use in-place editing. 403 -> 1000
uses the new experimental extended-string path. Shared values use that path too.
Keep pipe separators and per-level values in place. Do not rename param names,
classes, display/localization keys, abilities, or remove/add/reorder XML elements.
Test one ability value at a time. Original Charge damage is 403|403|444.

INSTALL UPDATE
Extract into a separate folder and open Open-Evolve-Mod-Manager.cmd, then set
existing stage/swap/project paths. If using the EXE, run Build-Windows-EXE.cmd
again and use its new output. An old EXE does not pick up these source changes.
Existing game files, keys, journals and backups are not included in this ZIP.

VALIDATION / LIMIT
This conservative method follows the equal-length binary string patch used in
the health test you confirmed working. The specific Charge damage example has
been checked at byte/semantic level, not tested live in-game here. General XML
rebuilding remains unsupported. Shared/variable-length value edits now use an
append-only string-table extension; they have not yet been confirmed in-game.

SHARED STRING FIX - YOUR CHARGE / ROCK THROW EDITS
Rock Throw PlayerCollisionRadius=3 shared storage with the root levels=3.
Changing the radius to 9 now leaves levels=3 unchanged. Your two uploaded XMLs
passed semantic and table-position checks. Charge needs no file extension;
Rock Throw adds six bytes for its edited cooldown and radius strings.

UPDATING WITHOUT LOSING YOUR EDITS
Close the manager. Extract this updated ZIP over the manager PROGRAM folder,
preserving Projects, manager_settings.json, keys and backups (none are in ZIP).
If using the EXE, rebuild it and use the new executable output.
Your current workspace can be reused if based on the working layout-test PAK.
Click Review Changes, then Build Mod. Do not unpack over your edited files.
Install only after a successful build, then test in a private bot match.
