"""Evolve Stage 2 display aliases for internal CryEngine asset folders.

These are presentation labels only; never rewrite game virtual paths, material
references, PAK contents or project/workspace keys.
"""
from __future__ import annotations
from pathlib import PurePosixPath

CHARACTER_ALIASES = {
    'lurker': 'Gorgon',
    'tech': 'Kala',
    'scrap': 'Jack',
    'grounder': 'Kraken',
    'rocky': 'Behemoth',
    'rockybobcicle': 'Glacial Behemoth',
    'teleporter': 'Wraith',
}


def character_alias(virtual_path):
    """Return friendly name for a complete hunter/monster folder segment."""
    parts = PurePosixPath(str(virtual_path).replace('\\', '/')).parts
    for i, part in enumerate(parts):
        if part.lower() not in ('hunters', 'monsters') or i + 1 >= len(parts):
            continue
        folder = parts[i + 1].lower()
        # Internal directories commonly use merc_tech / mon_lurker, etc.
        if folder.startswith(('merc_', 'mon_')):
            folder = folder.split('_', 1)[1]
        if folder in CHARACTER_ALIASES:
            return CHARACTER_ALIASES[folder]
        if folder == 'rockybobcicle':
            return 'Glacial Behemoth'
    return None


def pretty_folder(segment, parent_path):
    """Show friendly name alongside the precise internal asset directory."""
    key = segment.casefold()
    if key.startswith(('merc_', 'mon_')):
        key = key.split('_', 1)[1]
    path = str(parent_path).replace('\\', '/').casefold()
    if (path.endswith('/hunters') or path.endswith('/monsters')
            or path in ('hunters', 'monsters')) and key in CHARACTER_ALIASES:
        return CHARACTER_ALIASES[key] + ' (' + segment + ')'
    return segment


def character_name_for_internal(internal):
    """In-game name from a folder or an archive character token."""
    key = str(internal).casefold()
    if key.startswith(('merc_', 'mon_')):
        key = key.split('_', 1)[1]
    return CHARACTER_ALIASES.get(key)


def character_asset_description(virtual_path):
    """Stable, tooltip-friendly character identification for all file views."""
    path = str(virtual_path).replace('\\', '/')
    parts = PurePosixPath(path).parts
    for idx, part in enumerate(parts):
        if part.casefold() not in ('hunters', 'monsters') or idx + 1 >= len(parts):
            continue
        folder = parts[idx + 1]
        friendly = character_name_for_internal(folder)
        if friendly:
            return 'In-game: ' + friendly + ' | Internal: ' + folder
    return ''
