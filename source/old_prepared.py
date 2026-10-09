"""Opt-in preservation of leftover *.customkey-ready files from older setups.

Only the prepared copies are renamed IN PLACE. Neither installed PAKs nor
customkey-original recovery backups may be altered by this helper. It creates a
journal outside the game so the old names can be located later if required.
"""
from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
import uuid
from universal_stage import ensure_closed, save_json


READY = '.customkey-ready'
BACKUP = '.customkey-original'
PARTIAL = '.customkey-ready.partial'


def scan_prepared(game: Path) -> dict:
    game = Path(game).resolve()
    if not game.is_dir():
        raise ValueError('Choose an existing EvolveGame folder.')
    result = {'ready': [], 'backups': [], 'partial': []}
    for path in game.rglob('*'):
        if not path.name.endswith((READY, BACKUP, PARTIAL)):
            continue
        if path.is_symlink():
            raise ValueError('Unexpected linked file in game folder. Inspect manually: ' + str(path))
        if not path.is_file():
            raise ValueError('Unexpected non-file entry in game folder. Inspect manually: ' + str(path))
        kind = 'partial' if path.name.endswith(PARTIAL) else ('backups' if path.name.endswith(BACKUP) else 'ready')
        result[kind].append(path)
    for found in result.values():
        found.sort()
    return result


def preserve_ready(game: Path, archive_parent: Path) -> dict:
    """Explicitly selected by user. Preserve prepared files; never touch live PAKs.

    Rename files on the SAME filesystem, no disk duplication. A durable journal
    describes the original/new names, and is written BEFORE any file is moved.
    """
    game = Path(game).resolve()
    archive_parent = Path(archive_parent).resolve()
    ensure_closed()
    results = scan_prepared(game)
    if results['backups']:
        raise ValueError('Original backups exist. Restore with the previous launcher helper before continuing: ' + str(results['backups'][0]))
    if results['partial']:
        raise ValueError('An unfinished PAK preparation exists. Inspect it before continuing: ' + str(results['partial'][0]))
    ready = results['ready']
    if not ready:
        return {'status': 'no-prepared-files', 'count': 0, 'manifest': ''}
    if game == archive_parent or game in archive_parent.parents or archive_parent in game.parents:
        raise ValueError('The preservation manifest must be stored outside the game folder.')
    batch = uuid.uuid4().hex
    moves = []
    for path in ready:
        saved = Path(str(path) + '.saved-' + batch)
        if saved.exists():
            raise ValueError('Cannot preserve old file because destination exists: ' + str(saved))
        moves.append({'old': str(path), 'saved': str(saved), 'bytes': path.stat().st_size})
    archive_dir = archive_parent / ('OldPrepared_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + batch[:8])
    archive_dir.mkdir(parents=True, exist_ok=False)
    journal = archive_dir / 'preserved_files.json'
    state = {'version': 1, 'status': 'preserving', 'game': str(game),
             'note': 'Prepared mod copies were renamed beside the originals. Installed .pak and .customkey-original files were never moved.',
             'files': moves}
    save_json(journal, state)
    finished = []
    try:
        for i, item in enumerate(moves, 1):
            old, saved = Path(item['old']), Path(item['saved'])
            if not old.is_file() or old.stat().st_size != item['bytes']:
                raise ValueError('Prepared file changed during preservation: ' + str(old))
            os.replace(old, saved)
            finished.append(item)
            if i % 25 == 0 or i == len(moves):
                print(f'Preserved {i}/{len(moves)} previously prepared files', flush=True)
        state['status'] = 'complete'
        save_json(journal, state)
    except BaseException:
        rollback_errors = []
        for item in reversed(finished):
            old, saved = Path(item['old']), Path(item['saved'])
            try:
                if old.exists():
                    raise ValueError('Old filename unexpectedly occupied: ' + str(old))
                os.replace(saved, old)
            except Exception as err:
                rollback_errors.append(str(err))
        state['status'] = 'manual-recovery-needed' if rollback_errors else 'rolled-back'
        state['rollback_errors'] = rollback_errors
        save_json(journal, state)
        raise
    print('Old prepared files saved alongside the game files with a .saved suffix.', flush=True)
    print('Preservation manifest:', journal, flush=True)
    print('The installed game PAKs and original backups were not changed.', flush=True)
    return {'status': 'complete', 'count': len(moves), 'manifest': str(journal)}
