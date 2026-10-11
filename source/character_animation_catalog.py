"""Read-only animation references and asset availability for CryEngine characters.

This catalog does not treat a CAF filename or a blendspace as decoded motion.
It resolves only exact virtual paths from verified unpacked workspaces and an
optionally selected loose asset directory. Actual CAF playback needs a decoder.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import struct
from xml.etree import ElementTree as ET

from multi_pak_assets import normalized_name, _resolve
from model_asset import MAX_MODEL_BYTES


@dataclass(frozen=True)
class AnimationReference:
    name: str
    virtual_path: str
    kind: str
    available: bool
    file: Path | None = None


def _xml(data):
    if len(data) > 2 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Invalid or unsafe animation configuration XML.')
    return ET.fromstring(data)


def parse_animation_references(data, exists):
    """Read CryEngine #filepath mappings without guessing cross-skeleton matches.

    exists(virtual_path) must return a safely resolved file path or None.
    Wildcard blendspace directives and DBA declarations are metadata, not CAFs.
    """
    root = _xml(data)
    entries = root.findall('./AnimationList/Animation')
    base = ''
    out = []
    for row in entries:
        name = (row.get('name') or '').strip()
        ref = (row.get('path') or '').strip().replace('\\', '/')
        if name.casefold() == '#filepath':
            try:
                base = normalized_name(ref).rstrip('/') + '/'
            except ValueError:
                base = ''
            continue
        if not name or not ref or name.startswith(('#', '$')) or '*' in name or '*' in ref:
            continue
        ext = Path(ref).suffix.casefold()
        if ext not in ('.caf', '.bspace', '.comb', '.dba'):
            continue
        try:
            virtual = normalized_name(ref if '/' in ref and ref.casefold().startswith(('characters/', 'animations/'))
                                      else base + ref)
        except ValueError:
            continue
        resolved = exists(virtual)
        if resolved is not None:
            resolved = Path(resolved)
        out.append(AnimationReference(name, virtual, ext[1:], resolved is not None, resolved))
    return tuple(out)


def prefer_idle(references):
    """Pick a verified idle, favoring neutral standing over contextual idles."""
    available = [ref for ref in references if ref.available and ref.kind == 'caf'
                 and re.search(r'(^|_)idle($|_)', ref.name.casefold())]
    if not available:
        return None

    def rank(ref):
        n = ref.name.casefold()
        bad = any(x in n for x in ('additive', 'underwater', 'swim', 'incap', 'jetpack', 'fp_', 'aim'))
        return (bad, not any(x in n for x in ('stand', 'neutral', 'relaxed')),
                'loop' not in n, n)
    return min(available, key=rank)


def caf_header(path):
    """Only confirm a safe chunk header. Never mistake headers for keyframes."""
    path = Path(path)
    if path.stat().st_size > MAX_MODEL_BYTES or path.stat().st_size < 32:
        return False
    with path.open('rb') as f:
        header = f.read(16)
    if len(header) < 16 or header[:8] != b'CrChF\x07\x00\x00':
        return False
    count, offset = struct.unpack_from('<II', header, 8)
    return 1 <= count <= 10000 and 16 <= offset and offset + 16 * count <= path.stat().st_size


def selected_character_params(selected, index, preferred_workspace=None):
    """Select an exact same-character skeleton config, never a different hunter."""
    rel = normalized_name(selected)
    p = Path(rel)
    stem = re.sub(r'_lod\d+$', '', p.stem, flags=re.I)
    stems = [stem, re.sub(r'_skeleton$', '', stem, flags=re.I)]
    if stem.endswith('_3p') or stem.endswith('_fp'):
        stems.insert(0, stem + '_skeleton')
    if stem.endswith('_3p_skeleton') or stem.endswith('_fp_skeleton'):
        stems.insert(0, stem)
    tried = set()
    for item in stems:
        key = (p.parent / (item + '.chrparams')).as_posix()
        if key in tried:
            continue
        tried.add(key)
        hits, status = _resolve(index, key, preferred_workspace=preferred_workspace)
        if status == 'found':
            return key, hits[0]['file']
    return None, None


def asset_directory_index(folder, *, max_files=60000):
    """Bounded, filename-only index for user-selected extracted animation trees."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('Select a directory containing extracted character animations.')
    result = {}
    count = 0
    # Walk without following symlinked dirs, reject symlinked files.
    for base, directories, names in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if not (Path(base) / d).is_symlink()]
        for name in names:
            count += 1
            if count > max_files:
                raise ValueError('Animation directory contains too many files.')
            path = Path(base) / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.casefold().startswith('files/'):
                relative = relative[6:]
            if Path(relative).suffix.casefold() not in ('.caf', '.dba', '.chrparams', '.bspace', '.comb'):
                continue
            try:
                key = normalized_name(relative).casefold()
            except ValueError:
                continue
            # Do not infer that two nonidentical files are interchangeable.
            if key in result and result[key] != path:
                result[key] = None
            else:
                result[key] = path
    return result


def discover_animations(selected, index, *, preferred_workspace=None, loose_index=None):
    """Resolve a model's named animations from its own skeleton's .chrparams."""
    config_name, config_file = selected_character_params(
        selected, index, preferred_workspace)
    if config_file is None:
        return (), None, 'No matching skeleton .chrparams was found.'
    def resolve(virtual):
        hits, status = _resolve(index, virtual, preferred_workspace=preferred_workspace)
        if status == 'found':
            return hits[0]['file']
        if status == 'missing' and loose_index:
            return loose_index.get(virtual.casefold())
        return None
    try:
        refs = parse_animation_references(Path(config_file).read_bytes(), resolve)
    except (OSError, ValueError, ET.ParseError) as exc:
        return (), None, 'Cannot read skeleton animation map: ' + str(exc)
    confirmed = []
    for item in refs:
        if item.available and item.kind == 'caf' and not caf_header(item.file):
            confirmed.append(AnimationReference(item.name, item.virtual_path,
                                                item.kind, False))
        else:
            confirmed.append(item)
    return tuple(confirmed), prefer_idle(confirmed), config_name
