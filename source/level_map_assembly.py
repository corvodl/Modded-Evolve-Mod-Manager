"""Read-only placement assembly shared by viewport and export.

Editable source layers win GUID conflicts. Runtime mission0 contributes only
complementary IDs; other missions require an explicit selection. Mounted XML
is never added to the project's editable layers or save manifest.
"""
from pathlib import Path
from copy import deepcopy
import re
from xml.etree import ElementTree as ET
from level_scene_core import build_scene
from level_asset_mount import resolve_scoped

MAX_PLACEMENT_BYTES = 48 * 1024 * 1024
MAX_PLACEMENT_FILES = 1024
MAX_PLACEMENTS = 200000


def placement_nodes(root):
    return [node for node in root.iter()
            if node.tag.split('}')[-1].casefold() in ('object', 'entity', 'brush', 'prefab')
            and (node.get('Id') or node.get('EntityId') or node.get('Name'))]


def assemble_map_scene(project, index, *, cancel=None, progress=None, mission='mission_mission0.xml'):
    layers = []
    for name,payload in project.layers.items():
        nodes = deepcopy(payload[2])
        root = payload[1]
        if root is not None and (root.get('Hidden') == '1' or root.get('Visible') == '0'):
            for node in nodes:
                node.set('Hidden','1')
        layers.append((name,nodes))
    diagnostics = []
    owned = {name.casefold() for name in project.layers}
    candidates = [key for key in sorted(index)
                  if key.endswith('.lyr') or re.fullmatch(r'(?:.*/)?mission_[^/]+\.xml', key)]
    if len(candidates) > MAX_PLACEMENT_FILES:
        raise ValueError('Placement file limit exceeded')
    count = sum(len(nodes) for _, nodes in layers)
    for position, key in enumerate(candidates):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Placement assembly cancelled')
        if key in owned:
            continue
        if progress:
            progress('Parsing placements ' + key, position, len(candidates))
        # Only selected-map records may contribute placements, even if a
        # shared archive happens to contain a similarly named XML file.
        scoped = [r for r in index[key] if r.get('map_owner') == str(project.level_name).casefold()]
        if not scoped:
            continue
        if not key.endswith('.lyr') and Path(key).name != mission.casefold():
            diagnostics.append('Inactive mission omitted: ' + key)
            continue
        record, state = resolve_scoped({key: scoped}, key, project.workspace)
        if record is None:
            diagnostics.append('Placement ' + state + ': ' + key)
            continue
        path = Path(record['file'])
        try:
            if not 0 < path.stat().st_size <= MAX_PLACEMENT_BYTES:
                raise ValueError('placement XML exceeds bounds')
            raw = path.read_bytes()
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('placement XML contains forbidden entities')
            root = ET.fromstring(raw)
            nodes = placement_nodes(root)
            if root.get('Hidden') == '1' or root.get('Visible') == '0':
                for node in nodes:
                    node.set('Hidden','1')
        except (OSError, ValueError, ET.ParseError) as exc:
            diagnostics.append(key + ': ' + str(exc)[:160])
            continue
        count += len(nodes)
        if count > MAX_PLACEMENTS:
            raise ValueError('Placement count limit exceeded')
        layers.append((str(record.get('archive', 'mounted')) + ':' + key, nodes))
    # Explicit precedence: copied source, other selected-map source, runtime.
    layers.sort(key=lambda pair: (pair[0] not in project.layers, not pair[0].endswith('.lyr')))
    scene, conflicts = build_scene(layers)
    diagnostics.extend(conflicts)
    diagnostics.extend(obj.id + ': ' + obj.status for obj in scene
                       if obj.status == 'unresolved parent GUID')
    return scene, tuple(diagnostics)
