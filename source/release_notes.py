"""Curated public notes shared by the app, README and release description.

Keep each version to at most three short bullets. Internal gated tools must
never appear here or in update names. Do not generate notes from commit logs.
"""
RELEASE_NOTES = (
    ('1.1.18-beta.25', (
        'Correct character texture UV orientation between software and GPU model previews.',
        'Keep monster CAF bone controllers synchronized on a shared animation timeline.',
        'Validate monster and character bind poses before playing incompatible rigs.',
    )),
    ('1.1.18-beta.24', (
        "Keep Lennox's cockpit mesh with the body when switching character equipment.",
        'Identify exact DDS source PAKs and missing material links for visible character parts.',
        'Improve texture diagnostics when assets are spread across different game PAKs.',
    )),
    ('1.1.18-beta.23', (
        'Redesign the animation browser with compact controls and a consistent Play action.',
        'Auto-play verified character-specific third-person DBAs when available, with safer rig compatibility checks.',
        'Improve dark material previews with studio fill and source-texture brightness diagnostics.',
    )),
    ('1.1.18-beta.22', (
        'Export every verified character CDF assembly as its own Blender-compatible GLB.',
        'Improve dark character material lighting and add an unlit diffuse inspection mode.',
        'Keep exported character models, original assets and skipped-assembly reports together.',
    )),
    ('1.1.18-beta.21', (
        'Assemble complete character models with distinct monster stage names and corrected textures.',
        'Improve Goliath and other character animations with native skin binding and eight-weight support.',
        'Keep character body attachments and selected textures consistent in previews and GLB exports.',
    )),
    ('1.1.18-beta.20', (
        'Preview supported level meshes in a depth-tested 3D viewport with camera controls and object selection.',
        'Match model assets to their source maps and edit materials safely in copied level projects.',
        'Inspect terrain and vegetation metadata while unsupported world geometry remains explicitly unrendered.',
    )),
    ('1.1.18-beta.19', (
        'Choose character textures per material and retain matching textures in supported exports.',
        'Improve original DBA animation previews and character material mapping.',
        'Improve Level Editor model references, hierarchy diagnostics and supported static mesh decoding.',
    )),
    ('1.1.18-beta.18', (
        'Export character folders directly with matching assets from imported PAKs.',
        'Browse CDF-based Full Character models separately from individual meshes.',
        'Export an assembled character GLB with embedded textures when available.',
    )),
    ('1.1.18-beta.17', (
        'Improve character material and shared-texture mapping across imported PAKs.',
        'Make Asset Library folders readable in Windows while preserving archive IDs.',
        'Show in-game character names in file explorers and fix read-only PAK switching.',
    )),
    ('1.1.18-beta.16', (
        'Show model texture diagnoses in a compact, searchable and collapsible window.',
        'Improve Wraith and Griffin material links across imported texture PAKs.',
        'Fix Blender model export path handling and reuse the shared asset index.',
    )),
    ('1.1.18-beta.15', (
        'Keep animation selection compact and scrollable inside the GPU viewer instead of opening a screen-sized menu.',
        'Show live converter activity, errors and a stop button directly in the animation browser.',
        'Run model-folder exports off the UI thread with responsive progress and safe cancellation.',
    )),
    ('1.1.18-beta.14', (
        'Export Model Folder automatically uses the selected File Explorer folder or current model directory.',
        'The Asset Library and Modding viewers ask only for an export destination.',
        'Keep original virtual paths and read-only Asset Library protections when exporting.',
    )),
    ('1.1.18-beta.13', (
        'Fix Windows converter working-directory type error during DBA playback.',
        'Preview supported layered aim/additive CAFs over an explicit base clip, with a weight slider.',
        'Use recognizable character names in the explorer while preserving original game paths.',
    )),
    ('1.1.18-beta.12', (
        'Bundle Cryengine-Converter and source for DBA previews without selecting an external EXE.',
        'Add collapsible animation controls and DBA fallback for missing CAF references.',
        'Resolve DDS streaming across imported PAKs and display physical asset paths.',
    )),
    ('1.1.18-beta.11', (
        'DBA databases can be selected for converter-backed animation playback with a compatible installed converter.',
        'Show real progress when extracting and importing PAKs or exporting individual and batch files.',
        'Improve safe batch export progress, cancellation and cleanup while keeping originals unchanged.',
    )),
    ('1.1.18-beta.10', (
        'Fix Asset Library GPU model previews expecting a Modding workspace JSON.',
        'Resolve equipment materials through verified CDF and compiled mesh links, not only same-name MTL files.',
        'Improve texture diagnostics with exact material associations and missing-link details.',
    )),
    ('1.1.18-beta.9', (
        'Give Asset Library previews the same model grouping, native GPU viewport, menus and animation controls as Modding.',
        'Keep Asset Library preview and export read-only, with file imports and modification controls disabled.',
        'Add an Open Asset Library Folder button that opens extracted references in Windows Explorer.',
    )),
    ('1.1.18-beta.8', (
        'Browse and preview extracted Asset Library PAKs in their own read-only tab.',
        'Export original PAK layouts, PNG textures and supported Blender models.',
        'Correct texture-link diagnostics for assembled character attachments.',
    )),
    ('1.1.18-beta.7', (
        'Keep the editor PAK selector in sync with the active project.',
        'Recover more cross-PAK character textures from the Asset Library.',
        'Clarify missing standalone CAFs versus potentially DBA-packed animations.',
    )),
    ('1.1.18-beta.6', (
        'Group character model variants into expandable families without hiding original files.',
        'Improve persistent Asset Library texture and game animation lookup.',
        'Show indexed blendspace/combination metadata; native graph playback is not yet supported.',
    )),
    ('1.1.18-beta.5', (
        'Show newly imported CAF animations and refresh library references without reopening the model editor.',
        'Add searchable Game Animations with availability and motion filters.',
        'Avoid re-extracting PAK companions already complete in the Asset Library.',
    )),
    ('1.1.18-beta.4', (
        'Import all installed game PAKs into the persistent, reusable Asset Library.',
        'Create editable mod projects from verified signed PAKs and use Build Mod to pack and sign.',
        'Improve shared asset diagnostics, model refresh, and GPU preview control icons.',
    )),
    ('1.1.18-beta.3', (
        'Simplify the Modding page with File, Edit, View, Tools and Help menus.',
        'Group related PAKs and extract recognized companion archives together.',
        'Open level and mission XML archives in the Level Editor with map layers and grouped entities in the World Outliner.',
    )),
    ('1.1.18-beta.2', (
        'Add a permanent extracted PAK Asset Library with custom archive imports.',
        'Improve animation asset lookup and simplify the model editor explorer.',
        'Open a Level Editor 1.0 with an Unreal-inspired layout and object transforms.',
    )),
    ('1.1.18-beta.1', (
        'Add a separate experimental Beta release with a Windows installer.',
        'Offer only the newest verified Stable or Beta update in the manager.',
    )),
    ('1.1.17.1', (
        'Add a Windows installer with optional Desktop and Start Menu shortcuts.',
        'Compare Stable and Beta releases and offer only the newest version.',
    )),
    ('1.1.17', (
        'Speed up model preparation and improve material rendering reliability.',
        'Validate Blender exports and restore model, animation and editor settings.',
        'Add appearance presets and a performance diagnostics panel.',
    )),
    ('1.1.16', (
        'Improve GPU previews with normal/specular materials and transparency.',
        'Save animation playlists, refine timelines, and restore editor layouts.',
        'Add project health checks, benchmarks, and customizable light/dark colors.',
    )),
    ('1.1.15.1', (
        'Improve compiled interface visual verification and screenshot coverage.',
    )),
    ('1.1.15', (
        'Add task queues, project dashboards, gameplay presets and conflict inspection.',
        'Speed up model reopening and improve material preview and editing.',
        'Add safer bulk text editing, offline test preparation and crash recovery.',
    )),
    ('1.1.14', (
        'Fix PAK double-click reopening the wrong extracted project.',
        'Add safe old-project cleanup, PAK diffs, asset dependencies and mod validation.',
        'Improve animation timelines, model parts, file search and project history.',
    )),
    ('1.1.13', (
        'Add visual gameplay values and texture adjustment editors.',
        'Search assets across PAKs, batch export files, and share mod profiles.',
        'Expand GPU viewing with camera presets, lighting, and screenshots.',
    )),
    ('1.1.12', (
        'Play supported native Evolve CAF animations, with compatible idle autoplay.',
        'View assembled characters and export animated, textured Blender GLBs.',
        'Load models in the background and refresh GPU textures correctly.',
    )),
    ('1.1.11', (
        'Organized model actions into a compact File, View and Animation menu bar.',
        'Improved cross-PAK diffuse texture lookup and added texture diagnostics.',
    )),
    ('1.1.10', (
        'The full GPU model viewer now opens directly inside Edit Files.',
        'File editor windows now support normal maximize and restore controls.',
    )),
    ('1.1.9', (
        'Editors can maximize; model cameras now support panning and sharper shading.',
        'Added skeletal GLB animation playback and optional CryEngine clip conversion.',
    )),
    ('1.1.8', (
        'Blender exports can now include original CryEngine rigs and skin weights.',
        'Added verified bone hierarchy and bind-pose conversion for supported models.',
    )),
    ('1.1.7', (
        'Blender GLB exports can include diffuse textures from unpacked companion PAKs.',
        'Clarified model, skeleton and animation archive descriptions.',
    )),
    ('1.1.6', (
        'Export supported CryEngine models and folders to Blender-ready GLB files.',
        'Models now use GPU preview by default when supported.',
    )),
    ('1.1.5', (
        'Added mod profiles for organizing and switching signed PAK changes.',
        'Added conflict checks and reversible profile staging.',
    )),
    ('1.1.4', (
        'Info buttons now open help when clicked.',
        "Added a What's New tab with brief version notes.",
    )),
    ('1.1.3', (
        'Added contextual help and an inline launch-status refresh button.',
        'Fixed the Play & Restore menu label.',
    )),
    ('1.1.2', (
        'Organized Settings and added display, activity and snapshot preferences.',
        'Added advance mod preparation and reduced redundant file checks.',
    )),
    ('1.1.1', (
        'Fixed unreadable Windows fonts and strengthened screenshot checks.',
    )),
    ('1.1.0', (
        'Moved the manager and editors to Qt with a smooth overlay menu.',
        'Added a native OpenGL model viewer.',
    )),
    ('1.0.7', (
        'Manual update checks can offer experimental builds; automatic checks stay stable-only.',
    )),
    ('1.0.6', (
        'Added a sliding menu and optional desktop shortcut.',
    )),
    ('1.0.5', (
        'Refined the Menu button and simplified Help labels.',
    )),
    ('1.0.4', (
        'Moved navigation into a collapsible sidebar for more workspace.',
    )),
    ('1.0.3', (
        'Fixed Windows control sizing and card backgrounds.',
    )),
    ('1.0.2', (
        'Updated app icons, colors and control styling.',
    )),
    ('1.0.1', (
        'Added instructions-first setup and simplified update controls.',
    )),
)


def markdown(latest_only=False):
    entries = RELEASE_NOTES[:1] if latest_only else RELEASE_NOTES
    return '\n\n'.join(
        "## What's new in v" + version + '\n\n' +
        '\n'.join('- ' + note for note in notes)
        for version, notes in entries
    ) + '\n'


if __name__ == '__main__':
    import sys
    print(markdown(latest_only='--latest' in sys.argv), end='')
