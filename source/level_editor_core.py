"""Evolve Stage 2 Level Editor 1.0: editable CryEngine placed-object layers.

Only extracted .lyr objects and their transforms are editable.  This is NOT a
CryEngine level compiler, terrain sculptor, or collision/navmesh rebuilder.
Saves are confined to the extracted project and retain original backups.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

MAX_LAYERS = 1024
MAX_LAYER_BYTES = 48 * 1024 * 1024
MAX_OBJECTS = 200000


def parse_vec(raw, dimension=3):
    if not isinstance(raw, str):
        return None
    parts = re.split(r"[\s,]+", raw.strip())
    if len(parts) != dimension:
        return None
    try:
        nums = tuple(float(x) for x in parts)
    except ValueError:
        return None
    return nums if all(math.isfinite(v) and abs(v) < 1e9 for v in nums) else None


def _serialize_vec(values, dimension):
    if len(values) != dimension:
        raise ValueError("Incorrect transform component count.")
    vals = tuple(float(v) for v in values)
    if not all(math.isfinite(v) and abs(v) < 1e9 for v in vals):
        raise ValueError("Transform values must be finite and bounded.")
    return ",".join(format(v, ".9g") for v in vals)


@dataclass(frozen=True)
class PlacedObject:
    layer: str
    index: int
    name: str
    kind: str
    geometry: str
    pos: tuple
    rotate: tuple | None
    scale: tuple | None


class LevelProject:
    """Reversible transform edits scoped to an extracted level workspace."""

    def __init__(self, workspace):
        from evolve_pak_workspace import load_workspace, normalized_name
        self.workspace = Path(workspace).resolve(strict=True)
        self.root = (self.workspace / "files").resolve(strict=True)
        manifest = load_workspace(self.workspace)
        names = [normalized_name(entry["path"]) for entry in manifest["entries"]
                 if entry["path"].casefold().endswith(".lyr")]
        if not names:
            raise ValueError("No editable .lyr level layers found in this project.")
        if len(names) > MAX_LAYERS:
            raise ValueError("Too many level layers.")
        self.layers = {}
        self.objects = []
        self._originals = {}
        self._original_attrs = {}
        self.skipped = []
        self.undo_stack = []
        self.redo_stack = []
        if names[0].casefold().startswith("levels/"):
            self.level_name = names[0].split("/")[1]
        else:
            self.level_name = self.workspace.name
        for name in sorted(names, key=str.casefold):
            path = self.root.joinpath(*name.split("/"))
            if (path.is_symlink() or not path.is_file() or
                    not path.resolve().is_relative_to(self.root) or
                    path.stat().st_size > MAX_LAYER_BYTES):
                self.skipped.append(name + ": unsafe, missing or too large")
                continue
            data = path.read_bytes()
            try:
                if data.startswith(b"CryXmlB\x00"):
                    self.skipped.append(name + ": encrypted CryXMLB must be exported to XML first")
                    continue
                # Preserve CryXMLB workspace namespace prefixes and attributes.
                from evolve_gameplay_editor import parse_cryxml_text
                root = parse_cryxml_text(data)
            except ET.ParseError:
                self.skipped.append(name + ": invalid XML layer")
                continue
            nodes = [node for node in root.iter()
                     if node.tag.split("}")[-1].casefold() == "object"
                     and ("Pos" in node.attrib or "Name" in node.attrib)]
            if len(self.objects) + len(nodes) > MAX_OBJECTS:
                raise ValueError("Too many placed level objects.")
            self.layers[name] = (path, root, nodes)
            self._originals[name] = data
            for i, node in enumerate(nodes):
                self._original_attrs[(name, i)] = {
                    k: node.get(k) for k in ("Pos", "Rotate", "Scale")}
                if parse_vec(node.get("Pos")) is None:
                    continue
                a = node.attrib
                self.objects.append(PlacedObject(
                    name, i, a.get("Name", a.get("Id", "Unnamed")),
                    a.get("Type", "Object"),
                    a.get("Geometry", a.get("EntityClass", a.get("Material", ""))),
                    parse_vec(a["Pos"]), parse_vec(a.get("Rotate"), 4),
                    parse_vec(a.get("Scale"))))
        if not self.layers:
            raise ValueError("No readable object layers in this level.")
        self._scene_indices = {(o.layer,o.index): n
                               for n,o in enumerate(self.objects)}

    @staticmethod
    def contains_levels(workspace):
        from evolve_pak_workspace import load_workspace
        try:
            return any(str(x["path"]).casefold().endswith(".lyr")
                       for x in load_workspace(Path(workspace))["entries"])
        except (ValueError, OSError, KeyError):
            return False

    def _node(self, layer, index):
        if layer not in self.layers:
            raise ValueError("Unknown layer")
        nodes = self.layers[layer][2]
        if not 0 <= index < len(nodes):
            raise ValueError("Unknown object")
        return nodes[index]

    def get(self, layer, index):
        node = self._node(layer, index)
        a = node.attrib
        return PlacedObject(
            layer, index, a.get("Name", a.get("Id", "Unnamed")),
            a.get("Type", "Object"),
            a.get("Geometry", a.get("EntityClass", a.get("Material", ""))),
            parse_vec(a.get("Pos")) or (0, 0, 0),
            parse_vec(a.get("Rotate"), 4), parse_vec(a.get("Scale")))

    def apply(self, layer, index, key, values):
        if key not in ("Pos", "Rotate", "Scale"):
            raise ValueError("Only object transforms are editable in Level Editor 1.0.")
        values = tuple(float(v) for v in values)
        if key == "Scale" and any(abs(v) < 0.000001 for v in values):
            raise ValueError("Zero object scale is not supported.")
        updated = _serialize_vec(values, 4 if key == "Rotate" else 3)
        node = self._node(layer, index)
        before = node.get(key)
        if before == updated:
            return False
        self.undo_stack.append((layer, index, key, before, updated))
        self.redo_stack.clear()
        node.set(key, updated)
        self._refresh_object(layer,index)
        return True

    def undo(self):
        if not self.undo_stack:
            return False
        change = self.undo_stack.pop()
        layer, index, key, before, after = change
        node = self._node(layer, index)
        if before is None:
            node.attrib.pop(key, None)
        else:
            node.set(key, before)
        self.redo_stack.append(change)
        self._refresh_object(layer,index)
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        change = self.redo_stack.pop()
        layer, index, key, before, after = change
        self._node(layer, index).set(key, after)
        self.undo_stack.append(change)
        self._refresh_object(layer,index)
        return True

    def _refresh_object(self, layer, index):
        item=self._scene_indices.get((layer,index))
        if item is not None:
            self.objects[item]=self.get(layer,index)

    def dirty_layers(self):
        dirty = set()
        for (name, i), old in self._original_attrs.items():
            node = self._node(name, i)
            if any(node.get(k) != v for k, v in old.items()):
                dirty.add(name)
        return dirty

    def save(self):
        """Atomic replacement of changed .lyr files. Never touches live PAKs."""
        changed = self.dirty_layers()
        if not changed:
            return ()
        results = []
        for name in sorted(changed):
            path, root, nodes = self.layers[name]
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("Level file moved outside the editable project.")
            if path.read_bytes() != self._originals[name]:
                raise ValueError("Layer changed on disk. Reopen to avoid overwriting edits.")
            backup = self.workspace / ".level-editor-backups"
            backup_file = backup.joinpath(*name.split("/"))
            if backup_file.is_symlink():
                raise ValueError("Linked backup path is not supported.")
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            if not backup_file.is_file():
                shutil.copy2(path, backup_file)
            payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            tmp = path.with_name(path.name + ".level-editor-saving")
            if tmp.exists():
                raise ValueError("Interrupted level save requires manual review: " + str(tmp))
            try:
                with tmp.open("xb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            self._originals[name] = payload
            results.append(name)
        for (name, i) in self._original_attrs:
            node = self._node(name, i)
            self._original_attrs[(name, i)] = {
                key: node.get(key) for key in ("Pos", "Rotate", "Scale")}
        self.undo_stack.clear()
        self.redo_stack.clear()
        return tuple(results)
