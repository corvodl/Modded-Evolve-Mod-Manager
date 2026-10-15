"""CryEngine level scene hierarchy and archive-scoped model resolution.

This module is intentionally independent of Qt and of runtime PAK packaging.
It never alters input XML, and provides stable GUIDs, parent transforms, and
per-object diagnostics to the eventual GPU renderer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path, PurePosixPath

RENDERABLE = {".cgf", ".cga", ".cdf", ".chr", ".skin", ".skinm", ".chrm", ".cgam", ".cgfm"}
MODEL_FIELDS = ("Prefab", "Geometry", "Model", "FileName")


def _vec(raw, n, default):
    if not raw:
        return default
    try:
        values = tuple(float(v.strip()) for v in raw.split(","))
    except (ValueError, AttributeError):
        return default
    if len(values) != n or not all(math.isfinite(v) and abs(v) < 1e8 for v in values):
        return default
    return values


def _quat(q):
    length = math.sqrt(sum(v * v for v in q))
    if length <= 1e-8:
        return (1., 0., 0., 0.)
    return tuple(v / length for v in q)


def quat_mul(a, b):
    """CryEngine attribute rotation is w,x,y,z; multiply parent by local."""
    w, x, y, z = a
    W, X, Y, Z = b
    return (w*W-x*X-y*Y-z*Z, w*X+x*W+y*Z-z*Y,
            w*Y-x*Z+y*W+z*X, w*Z+x*Y-y*X+z*W)


def rotate_vector(q, point):
    w, x, y, z = q
    a = quat_mul(quat_mul(q, (0., *point)), (w, -x, -y, -z))
    return a[1:]


@dataclass(frozen=True)
class WorldTransform:
    pos: tuple = (0., 0., 0.)
    rotate: tuple = (1., 0., 0., 0.)
    scale: tuple = (1., 1., 1.)

    def combine(self, child):
        scaled = tuple(child.pos[i] * self.scale[i] for i in range(3))
        offset = rotate_vector(self.rotate, scaled)
        return WorldTransform(
            tuple(self.pos[i] + offset[i] for i in range(3)),
            _quat(quat_mul(self.rotate, child.rotate)),
            tuple(self.scale[i] * child.scale[i] for i in range(3)),
        )


@dataclass(frozen=True)
class SceneObject:
    source: str
    id: str
    parent_id: str
    name: str
    kind: str
    model: str
    material: str
    local: WorldTransform
    world: WorldTransform
    status: str


def element_model_reference(element):
    direct = model_reference(element.attrib)
    if direct:
        return direct
    # Only explicit entity property containers. Do not steal child placements.
    for child in element:
        if child.tag.split('}')[-1].casefold() in ('properties', 'properties2'):
            for prop in child.iter():
                found = model_reference(prop.attrib)
                if found:
                    return found
    return ''


def model_reference(attrs):
    """Brush Prefab is a mesh path, not an entity archetype name."""
    for key in MODEL_FIELDS:
        candidate = str(attrs.get(key) or "").strip().replace("\\", "/")
        if candidate and PurePosixPath(candidate).suffix.lower() in RENDERABLE:
            return candidate
    return ""


def normalize_reference(value):
    value = str(value).replace("\\", "/").strip().lstrip("/")
    if not value or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("Unsafe or empty virtual model path")
    return value.casefold()


def resolve_model(index, reference, *, archive=None, project=None):
    """Use full virtual path; prefer copied project then map-bound archives.

    The index maps normalized virtual paths to library records with absolute
    file, archive and workspace fields. Matching the basename is forbidden.
    """
    key = normalize_reference(reference)
    matches = index.get(key, ())
    valid = []
    for entry in matches:
        try:
            file = Path(entry["file"])
            if not file.is_file() or file.is_symlink():
                continue
            valid.append(entry)
        except (KeyError, OSError, TypeError):
            continue
    if project:
        candidates = [e for e in valid if e.get("workspace") == project]
        if candidates:
            return candidates[0], "project"
    if archive:
        candidates = [e for e in valid if e.get("archive") == archive]
        if candidates:
            return candidates[0], "map archive"
    if len(valid) == 1:
        return valid[0], "shared library"
    if len(valid) > 1:
        return None, "ambiguous: multiple archives contain this full virtual path"
    return None, "missing model asset"


def _scene_node(record, layer, idx):
    attrs = record.attrib
    guid = (attrs.get("Id") or attrs.get("EntityId") or f"{layer}#{idx}").casefold()
    parent = (attrs.get("Parent") or attrs.get("Group") or "").casefold()
    local = WorldTransform(
        _vec(attrs.get("Pos"), 3, (0., 0., 0.)),
        _quat(_vec(attrs.get("Rotate"), 4, (1., 0., 0., 0.))),
        _vec(attrs.get("Scale"), 3, (1., 1., 1.)),
    )
    return {
        "source": layer, "id": guid, "parent_id": parent,
        "name": attrs.get("Name", guid),
        "kind": attrs.get("Type", record.tag.split("}")[-1]),
        "model": element_model_reference(record),
        "material": attrs.get("Material", ""),
        "local": local,
        "hidden": attrs.get("Hidden") == "1",
    }


def build_scene(layers):
    """Build hierarchy from parsed (source-name, nodes) without XML mutation.

    Nodes may be nested in XML or use explicit Parent/Group GUIDs. Detect
    repeated IDs/cycles and report them instead of silently misplacing objects.
    """
    pending = {}
    diagnostics = []
    for source, nodes in layers:
        nodes = tuple(nodes)
        ids = {id(element): (element.get('Id') or element.get('EntityId') or f'{source}#{i}').casefold()
               for i,element in enumerate(nodes)}
        parents = {}
        def children(element, parent):
            for child in element:
                if id(child) in ids:
                    parents[id(child)] = parent
                else:
                    children(child,parent)
        for element in nodes:
            children(element,ids[id(element)])
        for i, element in enumerate(nodes):
            value = _scene_node(element, source, i)
            if not value["parent_id"]:
                value["parent_id"] = parents.get(id(element), "")
            guid = value["id"]
            if guid in pending:
                diagnostics.append(f"duplicate object ID: {guid}; kept {pending[guid]['source']}; ignored {source}")
                continue
            pending[guid] = value
    done = {}
    visiting = set()

    def walk(guid):
        if guid in done:
            return done[guid]
        node = pending[guid]
        if guid in visiting:
            raise ValueError("cyclic parent reference: " + guid)
        visiting.add(guid)
        parent = node["parent_id"]
        status = ""
        if parent and parent in pending:
            try:
                base = walk(parent).world
            except ValueError as exc:
                diagnostics.append(str(exc))
                base = WorldTransform()
        else:
            base = WorldTransform()
            if parent:
                status = "unresolved parent GUID"
        world = base.combine(node["local"])
        if not node["model"]:
            status = status or "no explicit renderable model reference"
        if node["hidden"] or (parent in done and done[parent].status == "hidden in editor"):
            status = "hidden in editor"
        obj = SceneObject(node["source"], guid, parent, node["name"],
                          node["kind"], node["model"], node["material"],
                          node["local"], world, status or "model reference available")
        visiting.remove(guid)
        done[guid] = obj
        return obj

    for guid in pending:
        walk(guid)
    return tuple(done.values()), tuple(diagnostics)
