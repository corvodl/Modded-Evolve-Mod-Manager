"""Small, presentation-only groups for verified CDF attachments.

Compatibility is decided by CryEngine CDF and SkinSets in character_assembly,
not by these display categories. Equipment remains single-selection; verified
faceplate alternatives are an independent choice.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath


def _display_group(label, virtual):
    # Label and source stem are UI hints only, never sufficient to attach
    # geometry, assume a dependency, or override a CDF's SkinSets.
    name = re.sub(r'[^a-z0-9]', '', str(label).casefold())
    stem = re.sub(r'[^a-z0-9]', '', PurePosixPath(virtual).stem.casefold())
    joined = name + ' ' + stem
    if any(word in joined for word in (
            'gun', 'rifle', 'pistol', 'cannon', 'launcher', 'shotgun', 'blaster',
            'flamethrower', 'minigun', 'harpoon', 'lasergun', 'armorreducer')):
        return 'Weapons'
    if any(word in joined for word in (
            'head', 'face', 'mask', 'helmet', 'visage')):
        return 'Face / Head Variants'
    if any(word in joined for word in (
            'scarf', 'backpack', 'flashlight', 'gear', 'cosmetic', 'accessory')):
        return 'Accessories'
    if any(word in joined for word in (
            'device', 'totem', 'beacon', 'shield', 'trap', 'tool',
            'injector', 'scanner', 'healing', 'medic', 'ability')):
        return 'Equipment / Abilities'
    return 'Other Attachments'


def equipment_menu_groups(equipment_options, faceplate_options=()):
    """Return nonempty (title, kind, options) groups in stable display order."""
    grouped = {}
    for label, virtual in equipment_options:
        category = _display_group(label, virtual)
        grouped.setdefault((category, 'equipment'), []).append((label, virtual))
    if faceplate_options:
        grouped[('Face / Head Variants', 'faceplate')] = list(faceplate_options)
    order = ('Weapons', 'Equipment / Abilities', 'Face / Head Variants',
             'Accessories', 'Other Attachments')
    return tuple(
        (title, kind, tuple(grouped[(title, kind)]))
        for title in order for kind in ('equipment', 'faceplate')
        if grouped.get((title, kind)))
