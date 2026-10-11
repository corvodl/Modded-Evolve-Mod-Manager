"""Audited, explicit deletion of old Evolve extracted project workspaces.

Never deletes installed game data, staged signed archives, mod profiles or
active editors. Only direct children of the configured Projects root that
contain a valid extraction manifest can be offered for cleanup.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import time

from evolve_pak_workspace import load_workspace, get_changes

@dataclass(frozen=True)
class ProjectRecord:
    path: Path
    archive: str
    bytes: int
    last_modified: float
    has_changes: bool
    age_days: float
    eligible: bool
    reason: str


def _audit_tree(folder, *, max_items=200000):
    total, recent, count = 0, 0., 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            count += 1
            if count > max_items:
                raise ValueError('Project contains too many files to audit safely.')
            path = Path(base)/name
            if path.is_symlink():
                raise ValueError('Project includes a symlink; cleanup disabled.')
            st = path.stat(follow_symlinks=False)
            total += st.st_size if path.is_file() else 0
            recent = max(recent, st.st_mtime)
    return total, recent


def inspect_project(project_root, candidate, *, active=(), min_age_days=30, now=None):
    root = Path(project_root).resolve(strict=True)
    path = Path(candidate).resolve(strict=True)
    if path.parent != root or not path.is_dir() or path.is_symlink():
        raise ValueError('Cleanup is restricted to direct managed project folders.')
    if (path/'.evolve-pak-workspace.json').is_symlink():
        raise ValueError('Symlinked project manifest is not allowed.')
    from multi_pak_assets import BATCH_FILE, load_collection
    is_batch = (path/BATCH_FILE).is_file()
    if is_batch:
        data = load_collection(path)
        children = [(path/item['workspace']).resolve() for item in data['archives']]
        reports = [(child,load_workspace(child)) for child in children]
        archive = '%d-PAK batch' % len(reports)
    else:
        report = load_workspace(path)
        reports = [(path,report)]
        archive = Path(report.get('source_pak','Unknown archive')).name
    active_paths = {Path(x).resolve() for x in active if x}
    total, recent = _audit_tree(path)
    last = max(recent,(path/'.evolve-pak-workspace.json').stat().st_mtime)
    age = max(0.,((time.time() if now is None else now)-last)/86400.)
    reason = ''
    try:
        has_changes = False
        for child,manifest in reports:
            if get_changes(child,manifest):
                has_changes = True
            # The manifest does not list new files or backups from editing.
            # Treat either as valuable user work even if all tracked files
            # were later reverted to their original checksums.
            if (child/'EditorBackups').exists() or (
                    child/'.EXTRACTION-INCOMPLETE.txt').exists():
                has_changes = True
            expected = {entry['path'].casefold() for entry in manifest['entries']}
            files_root = child/'files'
            for base,dirs,files in os.walk(files_root,followlinks=False):
                for name in files:
                    rel=(Path(base)/name).relative_to(files_root).as_posix()
                    if rel.casefold() not in expected:
                        has_changes = True
                        break
                if has_changes:
                    break
    except (ValueError,OSError) as exc:
        has_changes = True
        reason = 'Files missing or verification failed: '+str(exc)[:160]
    if any(current==path or current.is_relative_to(path)
           for current in active_paths):
        reason = 'Current or open project'
    elif has_changes:
        reason = reason or 'Contains edits or incomplete project files'
    elif age < min_age_days:
        reason = 'Recently accessed or modified'
    return ProjectRecord(path,archive,total,last,has_changes,age,not reason,reason)


def find_old_projects(project_root, *, active=(), min_age_days=30, now=None):
    root = Path(project_root)
    if not root.is_dir():
        return ()
    root = root.resolve(strict=True)
    found = []
    for child in sorted(root.iterdir(),key=lambda p:p.name.casefold()):
        if (child.is_symlink() or not child.is_dir() or
                not (child/'.evolve-pak-workspace.json').is_file() and
                not (child/'.evolve-pak-batch.json').is_file()):
            continue
        try:
            found.append(inspect_project(root,child,active=active,
                                         min_age_days=min_age_days,now=now))
        except (ValueError,OSError):
            continue
    return tuple(found)


def delete_abandoned_project(project_root, project, *, active=(), min_age_days=30,
                             now=None):
    """Re-audit immediately before explicit deletion. No automatic removal."""
    record = inspect_project(project_root,project,active=active,
                             min_age_days=min_age_days,now=now)
    if not record.eligible:
        raise ValueError('Project must be preserved: '+record.reason)
    # No symlinks or boundary crossing; each deletion is scoped to a verified
    # direct child and the operation is never applied to the root.
    shutil.rmtree(record.path)
    return record.bytes
