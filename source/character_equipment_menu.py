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


def _menu_choices(equipment_options):
    """Hide an arm companion when its owning equipment is a separate option.

    The attachment still follows its CDF/SkinSets for the parent selection.
    It must not be shown as an independent gun because that loses its owner.
    """
    entries = tuple(equipment_options)
    labels = {re.sub(r'[^a-z0-9]', '', label.casefold()) for label, _ in entries}
    for label, virtual in entries:
        normalized = re.sub(r'[^a-z0-9]', '', label.casefold())
        if (normalized.endswith('arm') and normalized[:-3] in labels
                and normalized[:-3]):
            continue
        yield label, virtual


def equipment_menu_groups(equipment_options, faceplate_options=()):
    """Return one submenu per nonempty CDF-derived group.

    Entries are (kind, label, original virtual path). Classifications are
    strictly presentation hints, not guesses about equipment compatibility.
    """
    grouped = {}
    for label, virtual in _menu_choices(equipment_options):
        grouped.setdefault(_display_group(label, virtual), []).append(
            ('equipment', label, virtual))
    for label, virtual in faceplate_options:
        grouped.setdefault('Face / Head Variants', []).append(
            ('faceplate', label, virtual))
    order = ('Weapons', 'Equipment / Abilities', 'Face / Head Variants',
             'Accessories', 'Other Attachments')
    return tuple((title, tuple(grouped[title]))
                 for title in order if grouped.get(title))
