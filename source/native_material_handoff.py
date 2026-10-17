"""Restore verified source CryEngine material slots after native animation conversion.

The animation converter transits through a GLB for skin/CAF data. Its generic
embedded-texture reader is intentionally not authoritative for the original
CryEngine shader/material identity. Remap only when each GLB material names
an exact, unique source material slot. No ordering assumptions or guessed
name matches are allowed; external imported GLBs are unaffected.
"""
from __future__ import annotations

from dataclasses import fields, replace
import re

_SLOT_NAME = re.compile(r'^CryEngine material slot ([0-9]+)(?: |$)')


def native_animation_materials(document, source):
    """Return source materials in the GLB's material-index order or None.

    GLB builders compact/permute source subset indices. This mapping uses
    the explicit source slot stored in each generated material name, never
    GLB array index == CryEngine MTL leaf index.
    """
    if source is None:
        return None
    materials = document.get('materials', ())
    source_count = len(getattr(source, 'names', ()))
    if not materials or len(materials) > 256 or not source_count:
        return None
    slots = []
    for material in materials:
        match = _SLOT_NAME.match(material.get('name', ''))
        if match is None:
            return None
        slot = int(match.group(1))
        if slot >= source_count or slot in slots:
            return None
        slots.append(slot)

    updates = {}
    for field in fields(source):
        key = field.name
        if key in ('names', 'source', 'issues'):
            continue
        values = getattr(source, key)
        if key in ('map_uv_transforms', 'map_uv_motions'):
            mapped = []
            for role, per_slot in values:
                if len(per_slot) != source_count:
                    return None
                mapped.append((role, tuple(per_slot[slot] for slot in slots)))
            updates[key] = tuple(mapped)
        elif isinstance(values, tuple):
            if not values:
                continue
            if len(values) != source_count:
                # MTL arrays may be sparse in older decoders. A partial
                # attribute list must never be misassigned to another slot.
                return None
            updates[key] = tuple(values[slot] for slot in slots)
    updates['names'] = tuple(source.names[slot] for slot in slots)
    return replace(source, **updates)
