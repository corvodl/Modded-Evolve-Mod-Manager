"""Discover explicit, separate full-character models without inventing meshes.

A Full Character item is a virtual UI selection backed by the character's own
CDF and stage-specific or third-person skin mesh. Original CryEngine paths stay
unchanged; equipment and skeleton binding are resolved by CDF assembly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FullCharacter:
    primary: str
    definition: str
    label: str


def discover_full_characters(paths):
    """Map real primary .skinm paths to CDF-backed full character options.

    Ignore FP hands, LODs and equipment-only meshes; no guessed combinations
    of unrelated skins or every weapon are allowed.
    """
    by_lower = {str(path).replace('\\', '/').casefold(): str(path).replace('\\', '/')
                for path in paths}
    result = {}
    for folded, original in by_lower.items():
        if not folded.endswith('.cdf'):
            continue
        path = PurePosixPath(original)
        parent = path.parent
        segments = parent.parts
        if (len(segments) < 3 or segments[0].casefold() != 'characters'
                or segments[1].casefold() not in ('hunters', 'monsters', 'creatures')):
            continue
        stem = path.stem
        if ('_fp' in stem.casefold() or '_lod' in stem.casefold()
                or 'weapon' in stem.casefold()):
            continue
        mesh = by_lower.get((parent / (stem + '.skinm')).as_posix().casefold())
        if not mesh:
            continue
        # Preserve the game's presentation names while keeping original paths.
        from character_aliases import character_alias
        name = (stem[:1].upper() + stem[1:] if segments[1].casefold() == 'monsters'
                else character_alias(mesh) or stem.replace('_', ' ').title())
        title = name + ' • Full Character'
        result[mesh] = FullCharacter(mesh, original, title)
    return result
