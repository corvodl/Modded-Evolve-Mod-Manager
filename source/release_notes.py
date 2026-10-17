"""Curated public notes shared by the app, README and release description.

Earlier versions use short bullets; stable 1.2 is a curated feature summary. Internal gated tools must
never appear here or in update names. Do not generate notes from commit logs.
"""
RELEASE_NOTES = (
    ('1.3.0', (
        "Render verified CryEngine Glass visors as translucent in the GPU viewer, preserving source opacity, tint, fog, Fresnel and refraction settings.",
        "Export verified Glass materials to Blender GLB with optical transmission, original IOR and source material metadata instead of treating them as opaque.",
        "Reduce occasional model-selection stalls by sampling material diagnostics efficiently, resolving only selected assets and decoding off the Qt UI thread.",
        "Prioritize the latest selected model, cancel obsolete queued decodes and prevent repeated loads when browsing characters quickly.",
        "Add cancellable model-loading progress with stage labels, elapsed time and genuine GPU texture upload counts, without inventing completion percentages.",
        "Release stale GPU textures and pending uploads on model changes and viewer close while retaining bounded decoded-model caching.",
        "Display CryEngine Glass shader properties and approximate preview transparency separately in the Material diagnostics inspector.",
        "Extend Performance Monitor with loading-stage timings, texture allocation estimates, process memory peaks and cancellation counters.",
        "Speed up Asset Library file search with a 525 ms typing debounce, Enter-to-search and reused case-insensitive path indexing.",
    )),
    ('1.2.8', (
        "Redesign the GPU character viewer with more viewport space, pictogram toolbars, scrollable Materials and Animation popovers, and a draggable XYZ orbit gizmo.",
        "Display precise percentage values for lighting, glow, gloss and studio fill; preserve Smooth as the default shading mode.",
        "Show cancellable, stage-labelled animation loading; export separately validated DBA Actions alongside compatible CAF clips instead of skipping databases when CAFs are present.",
        "Apply the user-tested reference emission strength of 100 to verified glowing surfaces in GPU previews and Blender GLB exports while preserving authored CryEngine GlowAmount and masks.",
        "Preserve Blender animated GLB emissive strength above the old 16-unit limit when exporting and importing compatible animations.",
        "Reclaim wasted space in the Asset Library Full Character view; preserve original game assets as read-only.",
    )),
    ('1.2.7', (
        "Respect Kala's CDF SkinSets: Banshee Gun, Armor Reducer and Rift Device equipment no longer overlap; the Rift Device arm stays with its owning equipment.",
        "Preserve Kala's authored blue Banshee Gun and purple Rift Device glow separately, without hardcoded color substitutions.",
        "Decode high-resolution CryEngine DDNA gloss sidecars and keep gloss mapped to the correct material when assembling character attachments.",
        "Default the GPU model viewer to Smooth shading while keeping Faceted available.",
        "Start verified converted native CAF/DBA clips even without an idle name; never auto-play standalone additive or layered poses.",
        "Retain CryEngine glow, bloom, emissive Blender export and rig-safe geometry handling without altering source assets.",
    )),
    ('1.2.6', (
        "Patch 5: Preserve verified CryEngine Decal glow, emissive strength and original material UV mapping when native CAF/DBA playback switches to a skinned GLB preview.",
        "Patch 5: Use each legacy Illum Decal material's authored diffuse tint when no explicit emissive color is provided, preserving colored light instead of untinted white.",
        "Patch 5: Restore emissive textures, UV transforms and intensity from imported animated GLBs; regression-test native assembly handoff and non-emissive materials.",
        "Patch 4: Preserve each source MTL submaterial identity, original slot, and resolved emissive reference when combining hunter, monster and wildlife character parts.",
        "Patch 4: Allow verified Decal/Emittance glow to load even when a submaterial has no diffuse DDS; retain safe missing-material diagnostics.",
        "Patch 4: Refresh scoped character, shared and global Asset Library textures before final assembly, and show precise glow decode status in the GPU viewer.",
        "Patch 3: Correct CryEngine ALPHAGLOW mask selection across all characters and models: independent Decal glow no longer inherits unrelated diffuse alpha.",
        "Patch 3: Preserve each emissive texture's own UV tiling and offsets in the GPU viewer, bloom pass, assembled character parts and Blender GLB export.",
        "Patch 3: Diagnose actual mesh-subset emission UV coverage, distinguish black sampled regions from nonblack atlases, and flag unsupported animated UV oscillators.",
        "Patch 2: Fix missing Kala emissive glow by correcting Renderer 3 sampler registration and legacy CryEngine GlowAmount masking.",
        "Patch 2: Show model-loading progress from geometry preparation until the existing bottom texture-loading bar takes over.",
        "Patch 2: Report unavailable GPU emissive shading and black baked glow maps without changing original textures.",
    )),
    ('1.2.5', (
        "Fix Asset Library's separate character export path so Emet and other characters use the single-GLB layout instead of loose meshes, Assemblies, and old CDF assembly reports.",
        "Use the same complete character-folder exporter from File Editor and Modding Tools, instead of scattering standalone model GLBs across the export root.",
        "Create a character-named main GLB and categorized animations, textures, and models directories containing additional verified assemblies and equipment.",
        "Preserve Emet's canonical complete 3P body when selecting the primary GLB, with explicit rig and animation compatibility reports instead of fabricated skeletons.",
        "Handle empty-name, zero-byte CryPAK placeholders in read-only Asset Library and workspace indexes while continuing to reject unsafe names and entries with data.",
    )),
    ('1.2.4', (
        "Import individually verified assembled CDF characters into Blender as one GLB, with compatible named CAF/DBA Actions bound to the correct original skeleton.",
        "Expand native CAF conversion to as many as 256 verified clips per assembly within size and resource safety limits; report unsupported clips explicitly.",
        "Preserve compatible 2012 CryTek master-skeleton DBA conversion and reject unsupported or mismatched rigs rather than inventing motion.",
        "Show per-assembly Blender animation counts, clip names and skip reasons after export, while keeping the separate animation manifest for diagnostics.",
        "Add Settings integration for installing the optional standalone Blender Animation Browser through Blender's own extension system, with safe version and package checks.",
        "Review and install signed PAK or multi-PAK ZIP mods with validation, deduplication and conflict checks.",
        "Manage installed mod profiles, installation history, undo, and Verify & Repair for matching source files.",
        "Preview required disk space, choose mod storage locations, reuse verified setups, and safely clean up old setups.",
        "Open Quick Commands with Ctrl+K and use streamlined Modding and Settings workflows.",
        "View System Health diagnostics and launch/recovery safety checks.",
        "Improve project filtering, editor lifecycle, and main-menu promotional visibility persistence.",
    )),
    ('1.2.3', (
        "Patch 2: Add a collapsible, on-demand System Health dashboard for firewall rules, running processes, crash/support tools, storage and environment checks.",
        "Patch 2: Strengthen offline launch safety with upfront Windows elevation, pre-launch verification, recovery guidance and privacy-reviewed support reporting.",
        "Patch 2: Keep GFX Hide/Remove featured-promotion edits effective across menu video changes, and improve project browsing and workspace reuse.",
        "Patch 1: Decode higher-resolution DDS, explicit gloss/AO channels, original normals/tangents, detail maps, independent UVs, and environment reflections.",
        "Patch 1: Add optional GPU SSAO quality presets and smaller, expandable View, Materials, and Animation controls; show individual material and animation loading stages.",
        "Patch 1: Export GLBs with editable PNGs in textures/ and verified rigged CAF/DBA clips in animations/, with source files and compatibility reports.",
        "Pin Play with Mods, Play without Mods, and Settings at the bottom of the sidebar; add quick launch buttons to Home.",
        "Restore original PAK files automatically after the game and client close, with journal-backed recovery in Settings.",
        "Show installed client version separately without an unverified update checker.",
    )),
    ('1.2.2', (
        "Speed up Modding and Asset Library navigation with background inventory, virtualized large asset trees, and demand-driven file metadata.",
        "Show verified model and map geometry earlier, retain unchanged map textures, and invalidate prepared caches when source assets change.",
        "Stream RSA-verified signed PAK entries in bounded chunks with native Twofish acceleration, cancellation, and preserved CRC checks.",
    )),
    ('1.2.1', (
        "Patch 1: introduce Home as the launch dashboard, with workspace shortcuts, quick setup status and a persistent outlined-icon navigation rail.",
        "Patch 1: let the application logo return to Home while preserving restricted-page access controls.",
        "Patch 1: rebuild modified PAK workspaces using an exact SHA-verified retained original, when available, without overwriting edited files or swap journals.",
        "Refresh every Qt workspace with a cohesive charcoal interface, muted panels, restrained red accents, and reusable controls.",
        "Improve Modding and Asset Library navigation with resizable project lists, contextual actions, and collapsible advanced tools.",
        "Modernize File, GFX, GPU Model, and Level editors with clearer inspectors, shared toolbars, and more room for previews.",
        "Simplify Settings and dialogs with consistent light/dark appearances, custom theme options, and compact-density support.",
        "Improve main-menu GFX authoring, static UI symbol visibility, and separate featured title, TRY, and BUY edits.",
        "Introduce an experimental read-only UI inspection interface for future verified offline game-side integration.",
        "Keep the release milestone visible in editor footers without patch-revision clutter and preserve safer update handling.",
        "Retain console-free Windows startup and defer expensive inventory scans until their workspace opens.",
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
