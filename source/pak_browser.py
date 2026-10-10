"""PAK browser presentation and guarded import-target resolution.

No installed game files are modified here. A selected external PAK must still
pass RSA and entry-layout checks in universal_stage.install before staging.
"""
from pathlib import PurePosixPath
import re


def _title_words(stem: str) -> str:
    """Readable fallback without claiming the exact archive contents."""
    overrides = {
        'goliathmeteor': 'Meteor Goliath',
        'cairalowgrav': 'Caira Lowgrav',
        'cabotbattle': 'Cabot Battle',
        'aberenegade': 'Renegade Abe',
        'griffinelectro': 'Electro Griffin',
        'markovblitz': 'Blitz Markov',
        'parnelltank': 'Tank Parnell',
        'maggiesavage': 'Savage Maggie',
        'hanksgt': 'Hank Sgt',
        'valrogue': 'Rogue Val',
        'rockybobcicle': 'Rocky Bobcicle',
    }
    words = [overrides.get(word.lower(), word.title()) for word in re.split(r'[_ -]+', stem) if word]
    return ' '.join(words)


def archive_description(relative: str) -> str:
    """Best-effort *filename-derived* label, never a content inspection."""
    name = PurePosixPath(relative.replace('\\', '/')).name
    stem = name[:-4] if name.lower().endswith('.pak') else name
    lower = stem.casefold()
    if lower.startswith('characters_'):
        parts = stem.split('_')
        if len(parts) >= 4 and parts[-1].casefold() in ('data', 'ts'):
            subject = '_'.join(parts[2:-1])
            if parts[1].casefold() == 'hunters' and subject.casefold().startswith('merc_'):
                subject = subject[5:]
            if subject:
                kind = 'Models' if parts[-1].casefold() == 'data' else 'Textures'
                return _title_words(subject) + ' ' + kind
        if len(parts) >= 3:
            return _title_words('_'.join(parts[2:])) + ' Characters'
    if lower.startswith('objects_'):
        return _title_words(stem[len('objects_'):]) + ' Objects'
    if lower.startswith('sounds_'):
        return _title_words(stem[len('sounds_'):]) + ' Audio'
    if lower.startswith('materials_'):
        return _title_words(stem[len('materials_'):]) + ' Materials'
    if lower.startswith('levels_'):
        return _title_words(stem[len('levels_'):]) + ' Level'
    if lower.startswith('animations'):
        return _title_words(stem) + ' Assets'
    specials = {
        'libs': 'Game Libraries', 'scripts': 'Game Scripts',
        'textures': 'Game Textures', 'music': 'Game Music',
        'videos': 'Game Videos', 'entities': 'Game Entities',
        'prefabs': 'Game Prefabs', 'fonts': 'Game Fonts',
        'ui_data': 'Interface Data', 'ui_assets': 'Interface Assets',
        'ui_preload': 'Interface Preload',
    }
    return specials.get(lower, _title_words(stem))


def describe_archive(relative: str) -> tuple[str, str]:
    """User-friendly columns without losing the exact signed archive path."""
    rel = PurePosixPath(relative.replace('\\', '/'))
    return rel.name, str(rel.parent) if str(rel.parent) != '.' else '/'


def matching_archives(archives, query='', sort_by='name', descending=False):
    text = query.strip().casefold()
    result = [a for a in archives if text in a.casefold() or text in archive_description(a).casefold()]
    def sort_key(rel):
        name, folder = describe_archive(rel)
        if sort_by == 'location':
            return folder.casefold(), name.casefold(), rel.casefold()
        return name.casefold(), folder.casefold(), rel.casefold()
    return sorted(result, key=sort_key, reverse=descending)


def import_target(file_name: str, archives, selected=None):
    """Choose an unambiguous signed archive in the *current* stage.

    Names are signed into Evolve's encrypted PAK layout, so an external archive
    cannot simply be renamed to a different original name.
    """
    matches = [rel for rel in archives if describe_archive(rel)[0].casefold() == file_name.casefold()]
    if not matches:
        raise ValueError('No staged PAK matches this filename. Imported files must retain '
                         'their original signed .pak filename (for example libs.pak).')
    if selected in matches:
        return selected
    if len(matches) > 1:
        raise ValueError('Multiple staged PAKs share this filename. Select the correct '
                         'archive in the list before importing: ' + ', '.join(matches[:6]))
    return matches[0]


def format_size(byte_count: int) -> str:
    """Compact, consistent right-aligned file sizes."""
    if byte_count < 0: return '—'
    if byte_count < 1024: return f'{byte_count} B'
    for units, size in (('GB', 1024**3), ('MB', 1024**2), ('KB', 1024)):
        if byte_count >= size: return f'{byte_count / size:.1f} {units}'
    return f'{byte_count} B'
