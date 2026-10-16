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



EMET_MAIN_SKIN = 'emetremotecannon_3p.skinm'


def is_emet_primary_model(virtual):
    """The known Emet 3P model includes his arm; healing/beacon models do not.

    This is *not* a made-up CDF or cross-character alias. Match the exact
    filename within Emet's own hunters folder. Original assets remain intact.
    """
    path = PurePosixPath(str(virtual).replace('\\', '/'))
    parts = tuple(part.casefold() for part in path.parts)
    return (len(parts) >= 4 and parts[0] == 'characters'
            and parts[1] == 'hunters'
            and parts[2] in ('emet', 'merc_emet')
            and parts[-1] == EMET_MAIN_SKIN)


def preferred_emet_source(paths):
    """Select original 3P skinm only with its exact matching native .skin."""
    original = {str(path).replace('\\', '/').casefold(): str(path).replace('\\', '/')
                for path in paths}
    for name in original.values():
        if is_emet_primary_model(name):
            companion = name[:-1]
            if companion.casefold() in original:
                return name
    return None


def discover_full_characters(paths):
    """Map real primary .skinm paths to CDF-backed full character options.

    Ignore FP hands, LODs and equipment-only meshes; no guessed combinations
    of unrelated skins or every weapon are allowed.
    """
    by_lower = {str(path).replace('\\', '/').casefold(): str(path).replace('\\', '/')
                for path in paths}
    result = {}
    # Emet's native remote-cannon 3P skinm is the verified complete mesh with
    # his arm. The nearby healing-totem and dropship CDFs represent deployable
    # equipment, not his default full character. Preserve them as variants.
    emet_main = preferred_emet_source(by_lower.values())
    if emet_main is not None:
        result[emet_main] = FullCharacter(
            emet_main, emet_main[:-1], 'Emet • Full Character')
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
        if is_emet_primary_model(mesh):
            continue  # The directly rigged Emet 3P model takes precedence.
        result[mesh] = FullCharacter(mesh, original, title)
    return result
