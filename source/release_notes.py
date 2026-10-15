"""Curated public notes shared by the app, README and release description.

Earlier versions use short bullets; stable 1.2 is a curated feature summary. Internal gated tools must
never appear here or in update names. Do not generate notes from commit logs.
"""
RELEASE_NOTES = (
    ('1.2.1', (
        "Refresh every Qt workspace with a cohesive charcoal interface, muted panels, restrained red accents, and reusable controls.",
        "Improve Modding and Asset Library navigation with resizable project lists, contextual actions, and collapsible advanced tools.",
        "Modernize File, GFX, GPU Model, and Level editors with clearer inspectors, shared toolbars, and more room for previews.",
        "Simplify Settings and dialogs with consistent light/dark appearances, custom theme options, and compact-density support.",
        "Improve main-menu GFX authoring, static UI symbol visibility, and separate featured title, TRY, and BUY edits.",
        "Introduce an experimental read-only UI inspection interface for future verified offline game-side integration.",
        "Keep the release milestone visible in editor footers without patch-revision clutter and preserve safer update handling.",
    )),
    ('1.2.0', (
        "Modernize the Qt interface with contextual tools, cleaner Settings, Flaticon navigation icons, and improved workspace layouts.",
        "Manage Stage 2 PAKs through build-scoped Asset Library browsing, dependencies, bulk imports, isolated edits, and safer mod staging.",
        "Improve complete character assemblies, GPU materials, rig compatibility, CAF/DBA animation browsing, and supported idle playback.",
        "Use higher-resolution streamed CryEngine DDS textures and normal maps in previews and Blender exports, with explicit diagnostics.",
        "Export supported rigged character assemblies, textured GLBs, and terrain/level scenes for Blender with source-safe validation.",
        "Expand the Level Editor with grouped map archives, combined scenes, previews, and missing-resource reports.",
        "Preview and edit Scaleform GFX with external texture placement, transparent frames, and verified static layout controls.",
        "Offer experimental main-menu promotional title and TRY/BUY overrides plus preview-only popup-layer isolation; verify results offline.",
        "Inspect historical CryTek PAKs and compatible assets separately without promising playable cross-version conversions.",
        "Improve offline project workflows, asset provenance, recovery, and update safety while preserving personal game and mod data.",
        "Support versioned patch revisions and consolidate installation, update work, and logs under one manager folder.",
        "Patch 1: embed GFX editing in File Editor, diagnose same-build UI dependencies, hide static video placeholders, and improve startup without a console window.",
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
