"""Group safe CryEngine model variants in the file explorer.

This is a read-only presentation layer: all original virtual file paths stay
selectable and editing/export continues to use the real files in the PAK.
LOD meshes are never naively combined; the CDF loader handles assembly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from collections import defaultdict
import re

_MESH = {'.skinm', '.cgfm', '.chr'}
_DETAILS = {'.skinm', '.skin', '.chr', '.cdf', '.chrparams', '.mtl'}
_PERSPECTIVE = re.compile(r'^(.+?_(?:3p|fp))(?=_|$)', re.IGNORECASE)
_LOD = re.compile(r'_lod\d+$', re.IGNORECASE)


@dataclass(frozen=True)
class ModelFamily:
    primary: str
    members: tuple[str, ...]
    title: str
    lod_count: int
    has_definition: bool


def _family_key(relative):
    path = PurePosixPath(relative.replace('\\', '/'))
    if path.suffix.casefold() not in _DETAILS:
        return None
    stem = path.stem
    match = _PERSPECTIVE.match(stem)
    if match:
        root = match.group(1)
    elif _LOD.search(stem):
        root = _LOD.sub('', stem)
    else:
        root = stem
    return (path.parent.as_posix().casefold(), root.casefold())


def group_model_families(paths):
    """Return {primary_virtual_path: ModelFamily}, with no virtual-path changes.

    A body plus LODs or attachment meshes qualifies. A lone model is left
    ungrouped, and FP/3P assets are never accidentally merged.
    """
    families = defaultdict(list)
    for rel in paths:
        key = _family_key(rel)
        if key:
            families[key].append(rel)
    results = {}
    for (_, root), members in families.items():
        mesh_paths = [rel for rel in members
                      if PurePosixPath(rel).suffix.casefold() in _MESH]
        if not mesh_paths:
            continue
        has_cdf = any(rel.casefold().endswith('.cdf') for rel in members)
        if len(mesh_paths) < 2 and not (has_cdf and len(members) > 1):
            continue
        # Prefer the unnumbered body; never open a random accessory as the
        # grouped character. LOD fallback is used only when no body exists.
        base_mesh = [rel for rel in mesh_paths
                     if PurePosixPath(rel).stem.casefold() == root
                     and PurePosixPath(rel).suffix.casefold() in {'.skinm', '.chr'}]
        if not base_mesh:
            continue
        primary = min(base_mesh, key=lambda rel: (
            PurePosixPath(rel).suffix.casefold() != '.skinm', rel.casefold()))
        ordered = tuple(sorted(members, key=lambda rel: (
            rel != primary, PurePosixPath(rel).name.casefold())))
        lods = sum(bool(_LOD.search(PurePosixPath(rel).stem))
                   for rel in mesh_paths)
        nice_name = root.replace('_', ' ').title().replace('3P', '3P').replace('Fp', 'FP')
        results[primary] = ModelFamily(primary, ordered,
            nice_name + (' · Character' if has_cdf else ' · Model variants'),
            lods, has_cdf)
    return results
