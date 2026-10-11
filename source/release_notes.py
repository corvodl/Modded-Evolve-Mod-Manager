"""Curated public notes shared by the app, README and release description.

Keep each version to at most three short bullets. Internal gated tools must
never appear here or in update names. Do not generate notes from commit logs.
"""
RELEASE_NOTES = (
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
