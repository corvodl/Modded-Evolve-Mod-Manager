"""Dependency checkpoints for safe long-lived prepared-model cache entries.

Source assets are accessed by the existing verified resolver. This module
only observes safe candidate metadata (never trusts a manifest path for writes).
A changed/removed/added neighboring .mtl, .dds[.N], CDF, skeleton or provider
manifest forces recomputation; unknown dependencies fall back to cache TTL.
"""
from __future__ import annotations
from pathlib import Path

TRACKED = ('.mtl', '.dds', '.cdf', '.skinm', '.skin', '.chr', '.cgf', '.chrparams')


def _canonical_virtual(value):
    if not isinstance(value, str) or not value:
        return None
    value = value.replace('\\', '/')
    if value.startswith('/') or ':' in value or '..' in value.split('/'):
        return None
    return value.casefold()


def dependency_watch_paths(workspace, relative, result, index):
    root = Path(workspace).resolve()
    folder = (root / 'files').resolve()
    refs = set()
    value = _canonical_virtual(relative)
    if value:
        refs.add(value)
    mats = result.get('materials')
    if mats is not None:
        source = _canonical_virtual(getattr(mats, 'source', ''))
        if source:
            refs.add(source)
        for entry in getattr(mats, 'diffuse_sources', ()):
            rel = (entry[0] if isinstance(entry, (tuple, list)) and entry else
                   entry if isinstance(entry, str) else None)
            resolved = _canonical_virtual(rel)
            if resolved:
                refs.add(resolved)
    directories = {ref.rpartition('/')[0] for ref in refs
                   if '/' in ref}
    paths = set()
    for ref in refs:
        candidate = folder.joinpath(*ref.split('/'))
        paths.add(candidate)
        if ref.endswith('.dds'):
            for number in range(10):
                paths.add(folder.joinpath(*(ref + '.' + str(number)).split('/')))
    if isinstance(index, dict) and directories:
        for name, hits in index.items():
            key = _canonical_virtual(name)
            if key is None or key.rpartition('/')[0] not in directories:
                continue
            if not (key.endswith(TRACKED) or
                    any(key.endswith('.dds.' + str(i)) for i in range(10))):
                continue
            if not isinstance(hits, (list, tuple)):
                continue
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                candidate = hit.get('file')
                if candidate is not None:
                    paths.add(Path(candidate))
                owner = hit.get('workspace')
                if owner is not None:
                    paths.add(Path(owner) / '.evolve-pak-workspace.json')
                    paths.add(Path(owner) / 'asset-library.json')
    # Directory metadata discovers additions/removals not yet in an index.
    # Do not recurse arbitrary game folders; watch only referenced directories.
    parents = set()
    for candidate in paths:
        try:
            parents.add(candidate.parent.resolve())
        except OSError:
            parents.add(candidate.parent)
    paths.update(parents)
    return tuple(sorted((str(p) for p in paths), key=str.casefold))


def snapshot(paths):
    """Cheap stat tuples; explicit missing entries also detect new assets."""
    result = []
    for name in paths:
        try:
            info = Path(name).stat()
            result.append((name, info.st_mtime_ns, info.st_size,
                           getattr(info, 'st_ino', 0)))
        except OSError:
            result.append((name, None, None, None))
    return tuple(result)


def unchanged(original):
    return original == snapshot(tuple(row[0] for row in original))
