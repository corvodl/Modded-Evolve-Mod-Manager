"""Read-only animation references and asset availability for CryEngine characters.

This catalog does not treat a CAF filename or a blendspace as decoded motion.
It resolves only exact virtual paths from verified unpacked workspaces and an
optionally selected loose asset directory. Actual CAF playback needs a decoder.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
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
    dba_file: Path | None = None  # verified declared database, not a guessed CAF
    dba_virtual_path: str | None = None
    annotation: str = ''


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
        # $TracksDatabase declarations are the exact containers used by
        # CryEngine for packed motion. Expose the DBA itself as a selectable
        # conversion source, not as a fictitious standalone CAF.
        database = name.casefold() == '$tracksdatabase'
        if (not name or not ref or '*' in name or '*' in ref
                or name.startswith('#')
                or (name.startswith(chr(36)) and not database)):
            continue
        annotation = ''
        annotated = re.fullmatch(r'(.+\.caf)\s+\(([^()]*)\)', ref, re.I)
        if annotated:
            ref, annotation = annotated.groups()
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
        label = ('DBA: ' + Path(virtual).stem) if database else name
        out.append(AnimationReference(label, virtual, ext[1:], resolved is not None,
                                      resolved, archived, annotation=annotation))
    return tuple(out)



def _is_additive_caf(path):
    """Read motion flags so unnamed additive clips are not offered standalone."""
    try:
        path = Path(path)
        stat = path.stat()
        return _cached_additive_flag(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, struct.error):
        return False  # The playback decoder will explain invalid/missing CAFs.


@lru_cache(maxsize=512)
def _cached_additive_flag(path, modified, size):
    if not 32 <= size <= 64 * 1024 * 1024:
        return False
    with open(path, 'rb') as source:
        header = source.read(16)
        if header[:8] != b'CrChF\x07\0\0':
            return False
        count, table = struct.unpack_from('<II', header, 8)
        if not 1 <= count <= 1520 or table < 16 or table + count * 16 > size:
            return False
        source.seek(table)
        entries = source.read(count * 16)
        for kind, _ident, length, offset in struct.iter_unpack('<4I', entries):
            if kind != 0x09253002:
                continue
            if length < 40 or offset < table + count * 16 or offset + length > size:
                return False
            source.seek(offset)
            return bool(struct.unpack('<I', source.read(4))[0] & 1)
    return False


def native_clip_playability(reference):
    """Classify playback without decoding CAF controllers.

    Missing standalone CAFs can trigger exact-path extraction on Play.
    Aimposes require a separate CryEngine base animation and are never
    incorrectly claimed as standalone playable after extraction.
    """
    if reference.kind == 'dba':
        if reference.available and reference.file is not None:
            return 'converter', (
                'DBA database is available for bundled converter-backed '
                'animation preview. Track compatibility is checked after conversion.')
        return 'metadata', (
            'DBA database has not been extracted. Import its source animation PAK '
            'into the Asset Library before converting it.')
    if reference.kind != 'caf':
        return 'metadata', 'Blendspaces / combination graphs are not standalone motions.'
    name = reference.name.casefold()
    flagged_additive = (reference.available and reference.file is not None
                        and _is_additive_caf(reference.file))
    if (flagged_additive or 'aimpose' in name or 'additive' in name or
            re.search(r'(^|[_\\-])aim[_\\-]', name)):
        return 'layered', (
            'Layered/additive aim pose. Needs a compatible base animation and '
            'CryEngine blend weights; standalone playback is not supported.')
    if not reference.available:
        if reference.in_archive:
            return 'extractable', ('Found in signed PAK metadata. Click Extract & Play '
                                   'to decode only this CAF into the preview cache.')
        if reference.dba_file is not None and reference.dba_virtual_path:
            return 'converter', (
                'No standalone CAF is expected for this mapping: the character '
                'declares a DBA animation database. Convert its packed tracks '
                'and play only if the requested clip is found by exact name.')
        return 'missing', (
            'No standalone CAF at the exact path in indexed or verified PAKs. '
            'This motion may be stored in a DBA, but DBA playback is not yet supported '
            'for this clip because no verified database could be uniquely linked '
            'to the selected character.')
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



def preferred_third_person_dba(references, character=None):
    """Prefer one *verified* <character>_3p DBA from this skeleton's catalog.

    A DBA's filename is not proof that its tracks can animate the model.
    The bundled converter and bind-pose checks remain mandatory on Play.
    In particular, never choose an unrelated wildcard/other character DBA.
    """
    from pathlib import PurePosixPath
    ready = [ref for ref in references
             if ref.kind == 'dba' and ref.available and ref.file is not None
             and native_clip_playability(ref)[0] == 'converter'
             and PurePosixPath(ref.virtual_path).stem.casefold().endswith('_3p')]
    if not ready:
        return None
    if character:
        selected = PurePosixPath(str(character).replace('\\', '/').casefold())
        stem = selected.stem
        for suffix in ('_skeleton', '_3p', '_fp'):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
        parent = selected.parent.name.casefold()
        names = {stem, parent, parent.removeprefix('merc_')}
        direct = [ref for ref in ready
                  if PurePosixPath(ref.virtual_path).stem.casefold()[:-3] in names
                  and (ref.virtual_path.casefold().startswith(
                       selected.parent.as_posix().casefold().rstrip('/') + '/'))]
        if len(direct) == 1:
            return direct[0]
    # The catalog is already parsed from this exact skeleton chrparams.
    # A sole 3P database is unambiguous; multiple different choices aren't.
    return ready[0] if len(ready) == 1 else None


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


def discover_animations(selected, index, *, preferred_workspace=None,
                        loose_index=None, cancel=None):
    """Resolve a model's named animations from its own skeleton's .chrparams."""
    def checkpoint():
        if cancel is not None and cancel():
            raise InterruptedError('Animation discovery cancelled')
    checkpoint()
    # The model viewer may have opened before the background Asset Library
    # finished importing its CAF PAKs. Merge its CURRENT extracted files, not
    # the one-time geometry index captured when the model first loaded.
    from asset_library import extracted_index
    library = extracted_index()
    if library:
        merged = dict(index)
        for name, records in library.items():
            checkpoint()
            if not name.endswith(('.caf', '.chrparams', '.dba', '.bspace', '.comb')):
                continue
            present = {str(row.get('file', '')) for row in merged.get(name, ())}
            extra = [row for row in records
                     if str(row.get('file', '')) not in present]
            if extra:
                merged[name] = list(merged.get(name, ())) + extra
        index = merged
    checkpoint()
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
        checkpoint()
        if item.available and item.kind == 'caf' and not caf_header(item.file):
            confirmed.append(AnimationReference(item.name, item.virtual_path,
                                                item.kind, False))
        else:
            confirmed.append(item)
    # CryEngine animation maps often declare blend spaces through glob entries
    # (for example */bs_*.bspace). Expose exact indexed definitions as graph
    # metadata without mislabelling them as directly playable CAF clips.
    from fnmatch import fnmatchcase
    import re as _re
    try:
        config_xml = _xml(Path(config_file).read_bytes())
        raw_mappings = config_xml.findall('./AnimationList/Animation')
    except (OSError, ValueError, ET.ParseError):
        raw_mappings = ()
    base = ''
    extra_graph = []
    known = {ref.virtual_path.casefold() for ref in confirmed}
    for row in raw_mappings:
        checkpoint()
        label = (row.get('name') or '').strip()
        path = (row.get('path') or '').replace('\\', '/').strip()
        if label.casefold() == '#filepath':
            try:
                base = normalized_name(path).rstrip('/') + '/'
            except ValueError:
                base = ''
            continue
        if not ('*' in label or '*' in path):
            continue
        if not path.casefold().endswith(('.bspace', '.comb')):
            continue
        pattern = path if path.startswith(('characters/', 'animations/')) else base + path
        if not pattern or '..' in pattern.split('/'):
            continue
        from animation_on_demand import _exact_loose
        for key in sorted(index):
            if len(extra_graph) >= 500:
                break
            if key in known or not key.endswith(('.bspace', '.comb')):
                continue
            if not fnmatchcase(key, pattern.casefold()):
                continue
            concrete = _exact_loose(index, key, preferred_workspace)
            if concrete is None:
                continue
            extra_graph.append(AnimationReference(
                Path(key).stem, key, Path(key).suffix[1:], True, Path(concrete)))
            known.add(key)
    confirmed.extend(extra_graph)
    # CryEngine streams animations from declared DBA containers. Absence of a
    # loose CAF is normal; associate its reference ONLY if this skeleton's
    # animation map resolves one unambiguous concrete database. An exact
    # clip-name match is still required after conversion.
    from dataclasses import replace
    databases = {
        ref.virtual_path.casefold(): ref
        for ref in confirmed if ref.kind == 'dba'
        and ref.available and ref.file is not None
    }
    # Also expand explicit $TracksDatabase wildcard declarations against
    # verified extracted files. Never select an arbitrary other character DBA.
    for row in raw_mappings:
        checkpoint()
        if (row.get('name') or '').strip().casefold() != '$tracksdatabase':
            continue
        pattern = (row.get('path') or '').replace('\\', '/').strip()
        if not pattern or '..' in pattern.split('/'):
            continue
        if not pattern.startswith(('characters/', 'animations/')):
            pattern = base + pattern
        for key in sorted(index):
            if len(databases) >= 16:
                break
            if not key.endswith('.dba') or not fnmatchcase(key, pattern.casefold()):
                continue
            path = resolve(key)
            if path is not None:
                databases[key.casefold()] = AnimationReference(
                    'DBA: ' + Path(key).stem, key, 'dba', True, Path(path))
    if len(databases) == 1:
        database = next(iter(databases.values()))
        confirmed = [
            replace(ref, dba_file=database.file,
                    dba_virtual_path=database.virtual_path)
            if ref.kind == 'caf' and not ref.available and not ref.in_archive
            else ref for ref in confirmed
        ]
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
                checkpoint()
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
                checkpoint()
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
