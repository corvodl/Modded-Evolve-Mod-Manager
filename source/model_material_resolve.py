"""Resolve a model's actual CryEngine material across extracted PAKs.

Do not assume every equipment .skinm has a same-named .mtl. Explicit
references in CDF attachments and embedded mesh data outrank guesses.
Never assign an ambiguous or unrelated material.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree as ET

from evolve_pak_workspace import normalized_name
from multi_pak_assets import _resolve

_MAX_CDF = 2 * 1024 * 1024
_MAX_EMBEDDED = 16 * 1024 * 1024
_MTL_TOKEN = re.compile(rb'([a-zA-Z0-9_./\\-]{3,210}\.mtl)', re.I)


def _material_path(value, parent):
    value = str(value or '').strip().replace('\\', '/')
    if not value:
        return None
    if not value.casefold().endswith('.mtl'):
        value += '.mtl'
    if value.startswith('/') or ':' in value or '..' in value.split('/'):
        return None
    try:
        return normalized_name(value)
    except ValueError:
        return None


def _found(index, candidate, preferred_workspace):
    hits, status = _resolve(index, candidate, preferred_workspace=preferred_workspace)
    if status == 'ambiguous':
        raise ValueError('Conflicting copies of model material: ' + candidate)
    if status == 'found':
        return hits[0]
    return None


def _cdf_hints(index, model, preferred_workspace):
    parent = PurePosixPath(model).parent.as_posix().casefold()
    selected_skin = model.casefold().removesuffix('m') if model.casefold().endswith('.skinm') else model.casefold()
    candidates = sorted(
        key for key in index if key.endswith('.cdf')
        and PurePosixPath(key).parent.as_posix().casefold() == parent)
    for path in candidates[:24]:
        file = _found(index, path, preferred_workspace)
        if file is None:
            continue
        try:
            raw = Path(file['file']).read_bytes()
            if (len(raw) > _MAX_CDF or b'<!DOCTYPE' in raw.upper()
                    or b'<!ENTITY' in raw.upper()):
                continue
            root = ET.fromstring(raw)
        except (OSError, ValueError, ET.ParseError):
            continue
        for attachment in root.findall('./AttachmentList/Attachment'):
            binding = attachment.get('Binding', '').strip().replace('\\','/').casefold()
            if binding not in (selected_skin, model.casefold()):
                continue
            hint = (attachment.get('Material') or attachment.get('MaterialName')
                    or attachment.get('MaterialFile'))
            if hint:
                yield hint, 'CDF attachment material'
        skeleton = root.find('./Model')
        if skeleton is not None and (
            skeleton.get('File', '').replace('\\','/').casefold() == model.casefold()):
            hint = skeleton.get('Material') or skeleton.get('MaterialName')
            if hint:
                yield hint, 'CDF skeleton material'


def _embedded_hints(index, model, preferred_workspace):
    record = _found(index, model, preferred_workspace)
    if record is None:
        return
    try:
        file = Path(record['file'])
        if file.stat().st_size > _MAX_EMBEDDED:
            return
        raw = file.read_bytes()
    except OSError:
        return
    hints = set()
    for m in _MTL_TOKEN.finditer(raw):
        value = m.group(1).decode('ascii', 'ignore')
        # Binary buffers can contain a string prefix before the actual path.
        value = value.lstrip('./')
        if value and len(value) >= 5:
            hints.add(value)
    # Evolve also stores some material names as UTF16LE.
    if not hints and b'\x00' in raw:
        try:
            unicode_view = raw.decode('utf-16le', 'ignore')
            hints.update(re.findall(r'[a-zA-Z0-9_./\\-]{3,210}\.mtl', unicode_view, re.I)[:32])
        except UnicodeError:
            pass
    for hint in sorted(hints)[:32]:
        yield hint, 'compiled model material reference'


def find_model_material(index, model_relative, *, preferred_workspace=None):
    """Return (record, explanation, searched_paths) for a verified exact match."""
    model = normalized_name(model_relative)
    parent = PurePosixPath(model).parent.as_posix()
    stem = PurePosixPath(model).stem
    bases = [stem, re.sub(r'_lod\d+$', '', stem, flags=re.I)]
    attempts, seen = [], set()

    def check(name, reason):
        virtual = _material_path(name, parent)
        if virtual is None:
            return None
        choices = [virtual]
        if parent != '.' and not virtual.casefold().startswith(
                ('characters/', 'objects/', 'materials/', 'textures/', 'levels/')):
            choices.insert(0, parent + '/' + virtual)
        for path in choices:
            folded = path.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            attempts.append(path)
            record = _found(index, path, preferred_workspace)
            if record is not None:
                return (record, reason, tuple(attempts))
        return None

    for base in dict.fromkeys(bases):
        result = check((PurePosixPath(parent) / (base+'.mtl')).as_posix(),
                       'same-name material')
        if result:
            return result
    for hint, reason in _cdf_hints(index, model, preferred_workspace):
        result = check(hint, reason)
        if result:
            return result
    for hint, reason in _embedded_hints(index, model, preferred_workspace):
        result = check(hint, reason)
        if result:
            return result
    # The game sometimes shortens equipment render names such as *world
    # while material files keep the base equipment name. Only consider
    # a strong, unique same-directory prefix; never merge two candidates.
    local = []
    for candidate in index:
        if not candidate.endswith('.mtl'):
            continue
        if PurePosixPath(candidate).parent.as_posix().casefold() != parent.casefold():
            continue
        material_stem = PurePosixPath(candidate).stem.casefold()
        simple_stem = re.sub(r'_lod\d+$', '', stem, flags=re.I).casefold()
        if (len(material_stem) >= 9 and len(simple_stem) > len(material_stem)
                and simple_stem.startswith(material_stem)):
            local.append(candidate)
    if len(local) == 1:
        result = check(local[0], 'unique equipment-prefix material')
        if result:
            return result
    return None, 'unresolved', tuple(attempts)


def nearby_model_materials(index, model_relative, limit=12):
    """Diagnostic suggestions only, not implicitly assigned to a model."""
    model = normalized_name(model_relative)
    parent = PurePosixPath(model).parent.as_posix().casefold()
    # Diagnostic hints include nested Materials/ folders under this
    # character. They are NOT automatically assigned to a mesh.
    return tuple(sorted(
        key for key in index if key.endswith('.mtl')
        and (PurePosixPath(key).parent.as_posix().casefold() == parent
             or key.casefold().startswith(parent + '/'))
    )[:limit])
