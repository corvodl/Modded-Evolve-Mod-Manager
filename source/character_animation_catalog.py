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
import zlib
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
    in_archive: bool = False  # signed directory names known; bytes still unextracted


def _xml(data):
    if len(data) > 2 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Invalid or unsafe animation configuration XML.')
    return ET.fromstring(data)


def parse_animation_references(data, exists, can_extract=None):
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
        archived = bool(can_extract(virtual)) if can_extract else False
        out.append(AnimationReference(name, virtual, ext[1:], resolved is not None,
                                      resolved, archived))
    return tuple(out)


def native_clip_playability(reference):
    """Classify playback without attempting CAF decoding or I/O.

    Missing standalone CAFs can trigger exact-path extraction on Play.
    Aimposes require a separate CryEngine base animation and are never
    incorrectly claimed as standalone playable after extraction.
    """
    if reference.kind != 'caf':
        return 'metadata', 'Blendspaces / animation databases require the CryEngine animation graph.'
    name = reference.name.casefold()
    if ('aimpose' in name or 'additive' in name or
            re.search(r'(^|[_\\-])aim[_\\-]', name)):
        return 'layered', (
            'Layered/additive aim pose. Needs a compatible base animation and '
            'CryEngine blend weights; standalone playback is not supported.')
    if not reference.available:
        if reference.in_archive:
            return 'extractable', ('Found in signed PAK metadata. Click Extract & Play '
                                   'to decode only this CAF into the preview cache.')
        return 'missing', ('Not extracted. Click Search PAK & Play to locate this '
                           'exact animation in verified installed/staged PAKs.')
    return 'candidate', ('Standalone playback candidate. The native CAF decoder '
                          'will verify bone controllers and flags before playback.')


def prefer_idle(references):
    """Pick a verified idle, favoring neutral standing over contextual idles."""
    available = [ref for ref in references if native_clip_playability(ref)[0] == 'candidate'
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
    # The model viewer may have opened before the background Asset Library
    # finished importing its CAF PAKs. Merge its CURRENT extracted files, not
    # the one-time geometry index captured when the model first loaded.
    from asset_library import extracted_index
    library = extracted_index()
    if library:
        merged = dict(index)
        for name, records in library.items():
            if not name.endswith(('.caf', '.chrparams', '.dba', '.bspace', '.comb')):
                continue
            present = {str(row.get('file', '')) for row in merged.get(name, ())}
            extra = [row for row in records
                     if str(row.get('file', '')) not in present]
            if extra:
                merged[name] = list(merged.get(name, ())) + extra
        index = merged
    config_name, config_file = selected_character_params(
        selected, index, preferred_workspace)
    if config_file is None:
        return (), None, 'No matching skeleton .chrparams was found.'
    def resolve(virtual):
        # A catalog listing never decrypts a CAF; only a Play request may
        # materialize one from an encrypted game PAK.
        from animation_on_demand import _exact_loose
        known = _exact_loose(index, virtual, preferred_workspace)
        if known is not None:
            return known
        if loose_index:
            return loose_index.get(virtual.casefold())
        return None

    def can_extract(virtual):
        return any('_shared_archive' in row for row in
                   index.get(virtual.casefold(), ()))
    try:
        refs = parse_animation_references(Path(config_file).read_bytes(), resolve,
                                         can_extract=can_extract)
    except (OSError, ValueError, ET.ParseError) as exc:
        return (), None, 'Cannot read skeleton animation map: ' + str(exc)
    confirmed = []
    for item in refs:
        if item.available and item.kind == 'caf' and not caf_header(item.file):
            confirmed.append(AnimationReference(item.name, item.virtual_path,
                                                item.kind, False))
        else:
            confirmed.append(item)
    # Characters commonly have standalone CAF files omitted from their
    # .chrparams (the latter points at packed DBA tracks and blendspaces).
    # Permit such clips ONLY if their embedded controller CRC32 IDs match
    # the selected compiled skeleton. Never match on filename alone.
    candidates, status = _resolve(index, selected, preferred_workspace=preferred_workspace)
    if status == 'found' and selected.casefold().endswith(('.chr','.skin')):
        from model_rigging import parse_compiled_bones
        try:
            names, _, _, _ = parse_compiled_bones(candidates[0]['file'].read_bytes())
        except (ValueError, OSError):
            names = ()
        if names:
            valid_ids = {zlib.crc32(n.casefold().encode('utf-8')) for n in names}
            parent = Path(selected).parent.as_posix().casefold()
            base = Path(selected).stem.casefold()
            direction = 'fp' if ('_fp' in base or base.endswith('fp')) else '3p'
            prefix = parent + '/animations/' + direction + '/'
            seen = {r.virtual_path.casefold() for r in confirmed}
            paths = {}
            for key, entries in index.items():
                if key.startswith(prefix) and key.endswith('.caf'):
                    # Skip encrypted-on-demand entries here; the name alone
                    # does not establish a compatible skeleton/controller.
                    file = resolve(key)
                    if file:
                        paths[key] = file
            if loose_index:
                for key, file in loose_index.items():
                    if key.startswith(prefix) and key.endswith('.caf') and file and key not in paths:
                        paths[key] = file
            for key, file in sorted(paths.items()):
                if key in seen:
                    continue
                try:
                    raw = Path(file).read_bytes()
                    if len(raw) > 64*1024*1024 or not caf_header(file):
                        continue
                    count, offset = struct.unpack_from('<II',raw,8)
                    ids = set()
                    for i in range(count):
                        kind, _cid, size, at = struct.unpack_from('<4I',raw,offset+16*i)
                        if kind & 0xffff == 0x100d and size >= 16 and at+size<=len(raw):
                            ids.add(struct.unpack_from('<I',raw,at)[0])
                    if len(ids) < 3 or len(ids & valid_ids) < max(3,int(len(ids)*.8)):
                        continue
                except (ValueError, OSError, struct.error):
                    continue
                label = Path(key).stem
                confirmed.append(AnimationReference(label,key,'caf',True,Path(file)))
                seen.add(key)
    return tuple(confirmed), prefer_idle(confirmed), config_name
