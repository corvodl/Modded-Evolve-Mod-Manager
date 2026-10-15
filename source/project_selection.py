"""Match selected staged PAKs to their exact extraction workspace.

Prevents a stale 'current_workspace' setting from opening another archive when
users double-click a different PAK. Also rejects stale/moved signed sources.
"""
from pathlib import Path

from evolve_pak_workspace import load_workspace


def workspace_matches(workspace, pak):
    if not workspace or not pak:
        return False
    try:
        project = Path(workspace).resolve(strict=True)
        source = Path(pak).resolve(strict=True)
        if not project.is_dir() or not source.is_file():
            return False
        manifest = load_workspace(project)
        return Path(manifest['source_pak']).resolve() == source
    except (OSError, ValueError, KeyError, TypeError):
        return False


def workspace_for_archive(stage, relative, current_workspace='', batch_workspaces=None):
    """Return the matching project or None: never reuse an unrelated project."""
    from evolve_pak_workspace import normalized_name
    relative = normalized_name(relative)
    pak = Path(stage).resolve()/'paks'/Path(*relative.split('/'))
    collection = batch_workspaces or {}
    for candidate in (collection.get(relative),current_workspace):
        if workspace_matches(candidate,pak):
            return Path(candidate).resolve()
    return None


def editor_collection_for_workspace(workspace, collection):
    """Only expose batch PAK selectors belonging to the active editor.

    The manager retains the last extracted batch when users create a new
    standalone project. Showing that old batch in the new editor incorrectly
    presents its first PAK as if it were the current project.
    """
    if not workspace or not collection:
        return {}
    try:
        active = Path(workspace).resolve(strict=True)
    except (OSError, ValueError):
        return {}
    result = {}
    for archive, path in collection.items():
        try:
            resolved = Path(path).resolve(strict=True)
        except (OSError, ValueError):
            continue
        if resolved.is_dir():
            result[archive] = resolved
    if active not in result.values():
        return {}
    return result
