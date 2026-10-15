"""Conservative bounded dependency inventory for build-scoped transfer planning.

Only explicit references in bounded text XML are traversed. This is *not* a
complete engine dependency graph, and no file is modified or re-encoded.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from xml.etree import ElementTree as ET

from evolve_pak_workspace import normalized_name

MAX_WALK = 4096
MAX_DEPTH = 12
MAX_EDGES = 16000
MAX_XML_BYTES = 2 * 1024 * 1024
XML_SUFFIXES = frozenset((".mtl", ".xml", ".cdf", ".chrparams"))
ROOT_PREFIXES = ("characters/", "objects/", "levels/", "libs/", "materials/",
                 "textures/", "animations/", "engine/")
REFERENCE_ATTRS = frozenset((
    "file", "filename", "path", "model", "material", "skeleton",
    "geometry", "character", "cdf", "animation"))
ASSET_EXTENSIONS = (
    ".cgf", ".skinm", ".chrm", ".cgfm", ".cgam", ".mtl", ".dds",
    ".dds.0", ".tif", ".cdf", ".chrparams", ".caf", ".dba", ".xml")


def _safe_ref(raw, parent):
    text = str(raw or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text or "\x00" in text:
        return None, "invalid"
    try:
        virtual = normalized_name(text)
    except (ValueError, TypeError):
        return None, "invalid"
    if not virtual.casefold().endswith(ASSET_EXTENSIONS):
        return None, "not-asset"
    if virtual.casefold().startswith(ROOT_PREFIXES):
        return virtual, "exact"
    # A concrete relative dependency is anchored to the owning XML folder.
    # It is NEVER matched by basename across unrelated archives or builds.
    scope = "/".join(parent.split("/")[:-1])
    if scope:
        try:
            return normalized_name(scope + "/" + virtual), "relative"
        except ValueError:
            return None, "invalid"
    return None, "unrooted"


def _text_links(path, owner):
    if Path(owner).suffix.casefold() not in XML_SUFFIXES:
        return (), "unsupported-binary"
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return (), "unsafe-or-missing"
    size = path.stat().st_size
    if not 0 < size <= MAX_XML_BYTES:
        return (), "oversized-xml"
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return (), "forbidden-xml"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return (), "unreadable-xml"
    links = []
    for node in root.iter():
        for attribute, value in node.attrib.items():
            if attribute.split("}")[-1].casefold() not in REFERENCE_ATTRS:
                continue
            ref, state = _safe_ref(value, owner)
            if ref:
                links.append((ref, state))
    return tuple(links), "scanned-xml"


def discover_dependencies(index, initial_paths, *, build_id, cancelled=None):
    """Return conservative closure plus truthful non-scannable diagnostics.

    Index must be build-filtered. A build context is mandatory and checked by
    the resolver before workspace precedence or content-fingerprint dedup.
    """
    from multi_pak_assets import _resolve
    if build_id in (None, "all"):
        raise ValueError("Dependency discovery requires an explicit build.")
    if len(initial_paths) > MAX_WALK:
        raise ValueError("Too many source paths.")
    queue = deque()
    for name in initial_paths:
        queue.append((normalized_name(name), 0))
    visited, found, missing, conflicting, incomplete = set(), [], [], [], []
    edges = []
    while queue:
        if cancelled and cancelled():
            return {"cancelled": True, "found": tuple(found), "missing": tuple(missing),
                    "conflicts": tuple(conflicting), "incomplete": tuple(incomplete),
                    "edges": tuple(edges), "closure_complete": False}
        if len(visited) >= MAX_WALK:
            incomplete.append({"reason": "walk-limit"})
            break
        name, depth = queue.popleft()
        key = name.casefold()
        if key in visited:
            continue
        visited.add(key)
        hits, state = _resolve(index, name, build_id=build_id)
        if state == "missing":
            missing.append(name)
            continue
        if state != "found" or len(hits) != 1:
            conflicting.append({"path": name, "status": state})
            continue
        record = hits[0]
        found.append(name)
        try:
            refs, parser_state = _text_links(record["file"], name)
        except (OSError, ValueError, TypeError):
            refs, parser_state = (), "unreadable-xml"
        if parser_state != "scanned-xml":
            incomplete.append({"path": name, "reason": parser_state})
            continue
        if depth >= MAX_DEPTH:
            if refs:
                incomplete.append({"path": name, "reason": "depth-limit"})
            continue
        for ref, mode in refs:
            if len(edges) >= MAX_EDGES:
                incomplete.append({"path": name, "reason": "edge-limit"})
                queue.clear()
                break
            edges.append({"from": name, "to": ref, "reference": mode})
            if ref.casefold() not in visited:
                queue.append((ref, depth + 1))
    return {"cancelled": False, "found": tuple(found),
            "missing": tuple(missing), "conflicts": tuple(conflicting),
            "incomplete": tuple(incomplete), "edges": tuple(edges),
            "closure_complete": not (missing or conflicting or incomplete)}
