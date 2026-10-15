"""Resolve a model's actual CryEngine material across extracted PAKs.

Do not assume every equipment .skinm has a same-named .mtl. Explicit
references in CDF attachments and embedded mesh data outrank guesses.
Never assign an ambiguous or unrelated material.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import struct
from xml.etree import ElementTree as ET

from evolve_pak_workspace import normalized_name
from multi_pak_assets import _resolve

_MAX_CDF = 2 * 1024 * 1024
_MAX_EMBEDDED = 16 * 1024 * 1024
_MTL_TOKEN = re.compile(rb'([a-zA-Z0-9_./\\-]{3,210}\.mtl)', re.I)


class AmbiguousMaterialError(ValueError):
    def __init__(self, virtual):
        self.virtual = virtual
        super().__init__('Different versions of model material exist in multiple PAKs: ' + virtual)


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
        raise AmbiguousMaterialError(candidate)
    if status == 'found':
        return hits[0]
    return None


def _cdf_hints(index, model, preferred_workspace, cdf):
    selected_skin = model.casefold().removesuffix('m') if model.casefold().endswith('.skinm') else model.casefold()
    # Overrides belong only to the selected character definition.
    candidates = [normalized_name(cdf).casefold()] if cdf else []
    for path in candidates:
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
    # MtlName 0x802 stores a 128-byte name without a .mtl suffix.
    # Monster stages and many hunter weapons share this explicit material.
    from model_asset import inspect_model
    try:
        info = inspect_model(raw)
        if info.format_name == 'CrChF' and info.version == 7:
            for i in range(info.chunks):
                kind, _cid, size, offset = struct.unpack_from('<4I', info.chunk_table, i * 16)
                if kind != 0x08021014 or size < 132:
                    continue
                field = raw[offset:offset + 128]
                if b'\0' not in field:
                    continue
                name = field.split(b'\0', 1)[0].decode('utf-8')
                if name and _material_path(name, ''):
                    hints.add(name if name.casefold().endswith('.mtl') else name + '.mtl')
    except (ValueError, UnicodeError, struct.error):
        pass
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


def find_model_material(index, model_relative, *, preferred_workspace=None, cdf=None):
    """Return (record, explanation, searched_paths) for a verified exact match."""
    model = normalized_name(model_relative)
    parent = PurePosixPath(model).parent.as_posix()
    stem = PurePosixPath(model).stem
    no_lod = re.sub(r'_lod\d+$', '', stem, flags=re.I)
    # CryEngine may name the body mesh *_3p or *_fp while its MTL has
    # the underlying character name. Only check exact same-folder paths.
    perspective_base = re.sub(r'_(?:3p|fp)$', '', no_lod, flags=re.I)
    bases = list(dict.fromkeys((stem, no_lod, perspective_base)))
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
                return record, reason, tuple(attempts)
        return None

    # Explicit CDF material overrides are authoritative. A same-named .mtl
    # may exist for another LOD or skin variant and must not silently replace
    # an explicit attachment material (which would map the wrong DDS slots).
    explicit = list(_cdf_hints(index, model, preferred_workspace, cdf))
    if len({hint.casefold() for hint, _ in explicit}) > 1:
        raise AmbiguousMaterialError('Conflicting attachment materials in ' + str(cdf))
    for hint, reason in explicit:
        result = check(hint, reason)
        if result:
            return result
    if explicit:
        return None, 'Selected CDF material is missing', tuple(attempts)
    for hint, reason in _embedded_hints(index, model, preferred_workspace):
        result = check(hint, reason)
        if result:
            return result
    for base in bases:
        result = check((PurePosixPath(parent) / (base + '.mtl')).as_posix(),
                       'same-name material')
        if result:
            return result

    # A mesh with no embedded/default material may still have one unambiguous
    # equipment association. Never allow that fallback to replace a default.
    if cdf is None:
        hints = {}
        for definition in sorted(key for key in index if key.endswith('.cdf')
                and PurePosixPath(key).parent.as_posix().casefold() == parent.casefold()):
            for hint, reason in _cdf_hints(index, model, preferred_workspace, definition):
                hints[hint.casefold()] = (hint, reason)
        if len(hints) == 1:
            hint, reason = next(iter(hints.values()))
            result = check(hint, reason)
            if result:
                return result

    # Equipment *world meshes may use a shortened material filename.
    # Only accept a unique, strong prefix within the same model folder.
    local = []
    for candidate in index:
        if not candidate.endswith('.mtl'):
            continue
        if PurePosixPath(candidate).parent.as_posix().casefold() != parent.casefold():
            continue
        material_stem = PurePosixPath(candidate).stem.casefold()
        simple_stem = no_lod.casefold()
        if (len(material_stem) >= 9 and len(simple_stem) > len(material_stem)
                and simple_stem.startswith(material_stem)):
            local.append(candidate)
    if len(local) == 1:
        result = check(local[0], 'unique equipment-prefix material')
        if result:
            return result
    return None, 'unresolved', tuple(attempts)


def nearby_model_materials(index, model_relative, limit=12):
    """Diagnostic candidates only; these are never assigned automatically."""
    model = normalized_name(model_relative)
    parent = PurePosixPath(model).parent.as_posix().casefold()
    return tuple(sorted(
        key for key in index if key.endswith('.mtl')
        and (PurePosixPath(key).parent.as_posix().casefold() == parent
             or key.casefold().startswith(parent + '/'))
    )[:limit])
