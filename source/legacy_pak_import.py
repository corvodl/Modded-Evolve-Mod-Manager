"""Route unsigned ZIP-compatible historical PAKs away from Stage 2 signing.

Never assume that an arbitrary .pak is from 2012: format inspection is the
only criterion. Failed signed imports may be browsed/edited as independent
legacy projects, but cannot enter the signed Stage 2 Asset Library.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

from legacy_pak_workspace import create_project, inspect_archive, load_project


def legacy_project_root(path, projects_root):
    source = Path(path).resolve(strict=True)
    slug = ''.join(ch if ch.isalnum() or ch in '-_' else '_'
                   for ch in source.stem)[:48] or 'Archive'
    digest = hashlib.sha256(str(source).casefold().encode("utf-8")).hexdigest()[:9]
    return Path(projects_root).resolve() / "Legacy_PAK_Projects" / (slug + "_" + digest)


def try_import_legacy(path, projects_root):
    """Return an indexed legacy project or None for non-legacy PAKs.

    A ZIP whose entries are invalid is reported as an error rather than
    swallowed as a signed-PAK fallback.
    """
    candidate = Path(path)
    if candidate.suffix.casefold() != ".pak" or not zipfile.is_zipfile(candidate):
        return None
    inspect_archive(candidate)  # methods 0/8, no encryption, safe virtual paths
    folder = legacy_project_root(candidate, projects_root)
    if folder.exists():
        existing = load_project(folder)
        if existing["source_path"] != candidate.resolve():
            raise ValueError("Legacy project collision for a different archive.")
    else:
        folder.parent.mkdir(parents=True, exist_ok=True)
        create_project(candidate, folder)
    return {
        "status": "legacy-project", "name": candidate.name,
        "folder": str(folder), "count": len(load_project(folder)["entries"]),
        "kind": "unsigned-ZIP",
    }
