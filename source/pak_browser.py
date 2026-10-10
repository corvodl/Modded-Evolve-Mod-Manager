"""PAK browser presentation and guarded import-target resolution.

No installed game files are modified here. A selected external PAK must still
pass RSA and entry-layout checks in universal_stage.install before staging.
"""
from pathlib import PurePosixPath


def describe_archive(relative: str) -> tuple[str, str]:
    """User-friendly columns without losing the exact signed archive path."""
    rel = PurePosixPath(relative.replace('\\', '/'))
    return rel.name, str(rel.parent) if str(rel.parent) != '.' else '/'


def matching_archives(archives, query='', sort_by='name', descending=False):
    text = query.strip().casefold()
    result = [a for a in archives if text in a.casefold()]
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
