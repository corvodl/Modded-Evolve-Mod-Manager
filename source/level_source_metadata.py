"""Bounded read-only inspection of CryEngine map editor/terrain metadata.

The binary height and vegetation INSTANCE payloads are not decoded here.
Displaying metadata is not a claim of rendered terrain or editable vegetation.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

MAX_SOURCE_BYTES = 96 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_EDITOR_XML = 16 * 1024 * 1024


def _xml(root_bytes):
    if b"<!DOCTYPE" in root_bytes.upper() or b"<!ENTITY" in root_bytes.upper():
        raise ValueError("Untrusted XML entity declarations.")
    return ET.fromstring(root_bytes)


def embedded_metadata(raw, tag):
    """Parse only an explicitly identified XML metadata section in a binary file."""
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Binary source exceeds inspection limit.")
    start = raw.find(("<" + tag).encode("ascii"))
    if start < 0 or start > MAX_METADATA_BYTES:
        raise ValueError("Expected " + tag + " XML metadata header was not found.")
    closing = ("</" + tag + ">").encode("ascii")
    end = raw.find(closing, start, min(len(raw), start+MAX_METADATA_BYTES))
    if end < 0:
        raise ValueError("Incomplete " + tag + " metadata header.")
    root = _xml(raw[start:end+len(closing)])
    if root.tag != tag:
        raise ValueError("Invalid embedded XML metadata root.")
    return root


def inspect_heightmap(raw):
    root = embedded_metadata(raw,"Heightmap")
    result = {}
    for key in ("Width","Height","TextureSize"):
        value = int(root.get(key,0))
        if not 1 <= value <= 8192:
            raise ValueError("Heightmap " + key + " outside permitted metadata bounds.")
        result[key] = value
    for key in ("UnitSize","MaxHeight","WaterLevel"):
        result[key] = float(root.get(key,0))
    result["SurfaceTypes"] = len(root.findall("./SurfaceTypes/SurfaceType"))
    result["Layers"] = len(root.findall("./Layers/Layer"))
    result["PayloadDecoded"] = False
    return result


def inspect_vegetation(raw):
    root = embedded_metadata(raw,"VegetationMap")
    objects = root.findall("./Objects/Object")
    if len(objects) > 30000:
        raise ValueError("Vegetation definitions exceed supported bounds.")
    kinds = Counter(row.get("Category") or "Other" for row in objects)
    references = sorted({row.get("FileName") or row.get("Object")
                         for row in objects if row.get("FileName") or row.get("Object")})
    return {
        "Version": root.get("Version",""),
        "Definitions": len(objects),
        "Categories": dict(kinds),
        "ExplicitMeshes": len(references),
        "InstancePayloadDecoded": False,
    }


def inspect_editor_cry(raw):
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Editor archive too large.")
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as archive:
        members = [x for x in archive.infolist()
                   if x.filename.replace("\\","/").casefold()=="level.editor_xml"]
        if len(members)!=1 or members[0].file_size > MAX_EDITOR_XML:
            raise ValueError("Expected exactly one bounded level.editor_xml.")
        data = archive.read(members[0])
    root = _xml(data)
    return {"Archive":"CryEngine editor ZIP",
            "ObjectDeclarations": sum(1 for child in root.iter()
                                      if child.tag.split("}")[-1] == "Object"),
            "LayerDeclarations": sum(1 for child in root.iter()
                                     if child.tag.split("}")[-1] in ("RootLayer","Layer")),
            "SourceCompiled": False}


def inspect_workspace_sources(workspace):
    from evolve_pak_workspace import load_workspace, normalized_name
    root=(Path(workspace).resolve(strict=True)/"files").resolve(strict=True)
    records={}
    for entry in load_workspace(workspace)["entries"]:
        rel=normalized_name(entry["path"])
        name=Path(rel).name.casefold()
        if name not in ("heightmap.dat","vegetationmap.dat") and not name.endswith(".cry"):
            continue
        path=root.joinpath(*rel.split("/"))
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        if path.stat().st_size > MAX_SOURCE_BYTES:
            records[rel]={"Unsupported":"Source file too large"}
            continue
        try:
            if name=="heightmap.dat":
                data=inspect_heightmap(path.read_bytes())
            elif name=="vegetationmap.dat":
                data=inspect_vegetation(path.read_bytes())
            else:
                data=inspect_editor_cry(path.read_bytes())
            records[rel]=data
        except (ValueError,OSError,zipfile.BadZipFile,ET.ParseError) as exc:
            records[rel]={"Unsupported":str(exc)[:200]}
    return records
