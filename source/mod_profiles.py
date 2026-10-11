"""Non-destructive Evolve mod catalog and journaled profile staging.

The catalog points to user-owned signed PAKs (never copies game data).
Applying a profile uses universal_stage.install/restore, which validate
signatures, stage compatibility, and the one-click swap journal. Changes
affect the *staged* copies, not the installed game directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import uuid

from universal_stage import allowed_paks, digest, ensure_closed, install, restore

SCHEMA = 1
MAX_PROFILES = 100
MAX_MODS = 500


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.profiles-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, indent=2, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _uuid():
    return uuid.uuid4().hex


def _clean_name(name):
    if not isinstance(name, str):
        raise ValueError('Enter a profile name.')
    name = name.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Profile names must be 1–80 printable characters.')
    return name


def _clean_relative(relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError('Select a PAK from the verified stage.')
    path = Path(relative.replace('\\', '/'))
    if (path.is_absolute() or '..' in path.parts or ':' in relative
            or path.suffix.casefold() != '.pak'):
        raise ValueError('Invalid PAK target: ' + str(relative))
    return path.as_posix()


def _load(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


class ModProfiles:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.catalog_path = self.root / 'profiles.json'
        self.applied_path = self.root / 'applied.json'

    def _catalog(self):
        data = _load(self.catalog_path, {'schema': SCHEMA, 'profiles': []})
        if data.get('schema') != SCHEMA or not isinstance(data.get('profiles'), list):
            raise ValueError('Unsupported or damaged mod profile catalog. No files modified.')
        for profile in data['profiles']:
            if not isinstance(profile, dict) or not isinstance(profile.get('mods'), list):
                raise ValueError('Malformed profile records. No files modified.')
        return data

    def list_profiles(self):
        return self._catalog()['profiles']

    def _get(self, catalog, profile_id):
        found = next((p for p in catalog['profiles'] if p.get('id') == profile_id), None)
        if found is None:
            raise ValueError('The selected mod profile no longer exists.')
        return found

    def create(self, name):
        data = self._catalog()
        name = _clean_name(name)
        if len(data['profiles']) >= MAX_PROFILES:
            raise ValueError('Profile limit reached.')
        if any(p['name'].casefold() == name.casefold() for p in data['profiles']):
            raise ValueError('A profile with that name already exists.')
        profile = {'id': _uuid(), 'name': name, 'mods': []}
        data['profiles'].append(profile)
        _atomic_json(self.catalog_path, data)
        return profile['id']

    def rename(self, profile_id, name):
        data = self._catalog()
        profile = self._get(data, profile_id)
        name = _clean_name(name)
        if any(p['id'] != profile_id and p['name'].casefold() == name.casefold()
               for p in data['profiles']):
            raise ValueError('A profile with that name already exists.')
        profile['name'] = name
        _atomic_json(self.catalog_path, data)

    def delete(self, profile_id):
        data = self._catalog()
        self._get(data, profile_id)
        status = self.status()
        if status['in_progress'] or status['pending']:
            raise RuntimeError('Resolve the current profile staging operation first.')
        if self.active_profile_id() == profile_id:
            raise RuntimeError('Restore the active profile before deleting it.')
        data['profiles'] = [p for p in data['profiles'] if p['id'] != profile_id]
        _atomic_json(self.catalog_path, data)

    def add_mod(self, profile_id, source, relative, stage):
        data = self._catalog()
        profile = self._get(data, profile_id)
        if len(profile['mods']) >= MAX_MODS:
            raise ValueError('Mod library limit reached.')
        stage = Path(stage).resolve()
        relative = _clean_relative(relative)
        valid = allowed_paks(stage)
        if relative.casefold() not in valid:
            raise ValueError('Target is not a verified signed PAK in this stage.')
        relative = valid[relative.casefold()]
        source = Path(source).resolve()
        if not source.is_file() or source.suffix.casefold() != '.pak':
            raise FileNotFoundError('Choose an existing built or imported PAK file.')
        if source.name.casefold() != Path(relative).name.casefold():
            raise ValueError('Mod filename must match its intended staged PAK.')
        staged = stage / 'paks' / Path(*relative.split('/'))
        if source == staged.resolve():
            raise ValueError('Do not add the active staged PAK itself as a mod.')
        if any(Path(m['source']).resolve() == source for m in profile['mods']):
            raise ValueError('This file is already included in the profile.')
        mod = {'id': _uuid(), 'relative': relative, 'source': str(source),
               'sha256': digest(source), 'enabled': True}
        profile['mods'].append(mod)
        _atomic_json(self.catalog_path, data)
        return mod['id']

    def set_enabled(self, profile_id, mod_id, enabled):
        data = self._catalog()
        mod = self._mod(data, profile_id, mod_id)
        mod['enabled'] = bool(enabled)
        _atomic_json(self.catalog_path, data)

    def remove_mod(self, profile_id, mod_id):
        data = self._catalog()
        profile = self._get(data, profile_id)
        self._mod(data, profile_id, mod_id)
        profile['mods'] = [m for m in profile['mods'] if m['id'] != mod_id]
        _atomic_json(self.catalog_path, data)

    def _mod(self, catalog, profile_id, mod_id):
        profile = self._get(catalog, profile_id)
        mod = next((m for m in profile['mods'] if m.get('id') == mod_id), None)
        if mod is None:
            raise ValueError('The selected mod no longer exists.')
        return mod

    def plan(self, profile_id, stage, *, verify_files=True):
        """Check conflicts and stale/missing mod files before changing staging."""
        profile = self._get(self._catalog(), profile_id)
        choices = [dict(m) for m in profile['mods'] if m.get('enabled', False)]
        selected = {}
        errors = []
        valid = allowed_paks(Path(stage).resolve())
        for mod in choices:
            try:
                rel = _clean_relative(mod['relative'])
                if rel.casefold() not in valid:
                    raise ValueError('not in the current signing stage')
                if rel.casefold() in selected:
                    raise ValueError('multiple enabled mods target the same PAK')
                selected[rel.casefold()] = mod['id']
                source = Path(mod['source']).resolve()
                if source.suffix.casefold() != '.pak' or source.name.casefold() != Path(rel).name.casefold():
                    raise ValueError('file does not match its target archive')
                if verify_files and (not source.is_file() or digest(source) != mod['sha256']):
                    raise ValueError('source file is missing or changed; re-import it')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(str(mod.get('relative', 'Unknown PAK')) + ': ' + str(exc))
        return choices, errors

    def _applied(self, stage, swap):
        state = _load(self.applied_path, {
            'schema': SCHEMA, 'stage': str(stage), 'swap': str(swap),
            'profile_id': None, 'entries': [], 'pending': None,
            'in_progress': False
        })
        if (state.get('schema') != SCHEMA
                or Path(state.get('stage', '')).resolve() != stage
                or Path(state.get('swap', '')).resolve() != swap
                or not isinstance(state.get('entries'), list)):
            raise RuntimeError('Existing applied profile belongs to another signing stage. '
                               'Restore it before changing game installations.')
        return state

    def active_profile_id(self):
        state = _load(self.applied_path, {'profile_id': None})
        return state.get('profile_id')

    def status(self):
        state = _load(self.applied_path, {})
        return {'profile_id': state.get('profile_id'),
                'entry_count': len(state.get('entries', [])),
                'in_progress': state.get('in_progress', False),
                'pending': state.get('pending') is not None}

    def _reconcile(self, state):
        """Crash recovery for an interrupted individual install or restore."""
        pending = state.get('pending')
        if not pending:
            return
        backup = Path(pending['backup'])
        if pending['action'] == 'install':
            matches = list(backup.rglob('metadata.json')) if backup.is_dir() else []
            if len(matches) > 1:
                raise RuntimeError('Multiple pending backup records; manual recovery required.')
            if matches:
                meta = _load(matches[0], {})
                status = meta.get('status')
                if status == 'installed':
                    state['entries'].append({
                        'mod_id': pending['mod_id'],
                        'relative': pending['relative'],
                        'backup': str(matches[0].parent)})
                elif status not in ('rolled-back-after-error',):
                    raise RuntimeError('Interrupted PAK install; inspect preserved backup '
                                       + str(matches[0].parent))
            # With no backup the call had not yet made persistent changes.
        elif pending['action'] == 'restore':
            meta = _load(backup / 'metadata.json', {})
            if meta.get('status') == 'installed':
                restore(backup)  # already protected by the archive hash/journal
            elif meta.get('status') != 'restored':
                raise RuntimeError('Uncertain rollback state; inspect ' + str(backup))
            if not state['entries'] or state['entries'][-1]['backup'] != str(backup):
                raise RuntimeError('Profile rollback journal order mismatch.')
            state['entries'].pop()
        else:
            raise RuntimeError('Unknown incomplete profile operation.')
        state['pending'] = None
        _atomic_json(self.applied_path, state)

    def apply(self, profile_id, stage, swap):
        """Stage a profile through existing per-PAK reversible transactions.

        The live EvolveGame installation is never written here. No profile
        transition is allowed while the game/launcher or a swap is active.
        """
        stage = Path(stage).resolve()
        swap = Path(swap).resolve()
        ensure_closed()
        if not stage.is_dir() or not swap.is_dir():
            raise FileNotFoundError('Run Manager Setup before applying a profile.')
        from launch_integration import state_status
        phase = state_status(swap).phase
        if phase not in ('missing', 'restored', 'prepared'):
            raise RuntimeError('Restore live game files and resolve the swap journal first.')
        state = self._applied(stage, swap)
        self._reconcile(state)
        choices = []
        if profile_id is not None:
            choices, errors = self.plan(profile_id, stage)
            if errors:
                raise ValueError('Cannot apply profile:\n' + '\n'.join(errors))
        # Re-selecting the unchanged profile does not copy large archives again.
        active_ids = [item['mod_id'] for item in state['entries']]
        desired_ids = [mod['id'] for mod in choices]
        if (not state['in_progress'] and not state.get('pending')
                and state['profile_id'] == profile_id and active_ids == desired_ids):
            for mod in choices:
                staged = stage / 'paks' / Path(*mod['relative'].split('/'))
                if digest(staged) != mod['sha256']:
                    raise RuntimeError('Staged archive changed outside Profiles. '
                                       'Inspect and restore the existing backup first: '
                                       + mod['relative'])
            return len(choices)
        # Each PAK target may be used only once, so restoration is independent.
        # Restore the previous profile using exactly the backups we created.
        state['in_progress'] = True
        _atomic_json(self.applied_path, state)
        while state['entries']:
            record = state['entries'][-1]
            backup = Path(record['backup'])
            state['pending'] = {'action': 'restore', 'backup': str(backup)}
            _atomic_json(self.applied_path, state)
            restore(backup)
            state['entries'].pop()
            state['pending'] = None
            _atomic_json(self.applied_path, state)
        state['profile_id'] = None
        _atomic_json(self.applied_path, state)
        for mod in choices:
            # The pending marker includes its unique destination. If the app
            # is interrupted after install but before save, recovery can find
            # the operation's validated backup metadata.
            op_root = self.root / 'Backups' / _uuid()
            state['pending'] = {
                'action': 'install', 'backup': str(op_root),
                'mod_id': mod['id'], 'relative': mod['relative']
            }
            _atomic_json(self.applied_path, state)
            if digest(Path(mod['source'])) != mod['sha256']:
                raise RuntimeError('Mod file changed since it was added: '
                                   + mod['relative'] + '. Re-import it first.')
            backup = install(stage, swap, op_root, mod['relative'], Path(mod['source']))
            state['entries'].append({'mod_id': mod['id'],
                                     'relative': mod['relative'],
                                     'backup': str(backup)})
            state['pending'] = None
            _atomic_json(self.applied_path, state)
        state['profile_id'] = profile_id
        state['in_progress'] = False
        _atomic_json(self.applied_path, state)
        return len(choices)


def main(argv=None):
    """Run a journalled profile application as an existing manager worker."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply', 'status'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--swap', type=Path)
    parser.add_argument('--profile', default='baseline')
    args = parser.parse_args(argv)
    store = ModProfiles(args.root)
    if args.action == 'status':
        print(json.dumps(store.status(), indent=2))
        return 0
    if args.stage is None or args.swap is None:
        parser.error('--stage and --swap are required to apply a profile.')
    selected = None if args.profile == 'baseline' else args.profile
    count = store.apply(selected, args.stage, args.swap)
    print('PROFILE STAGING COMPLETE: %d enabled signed PAKs staged.' % count)
    print('Installed EvolveGame files remain untouched.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
