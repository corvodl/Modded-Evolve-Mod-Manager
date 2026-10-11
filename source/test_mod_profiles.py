"""Regression coverage for persistent mod profiles and reversible staging.

No installed Evolve game assets or real signing keys are touched.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mod_profiles import ModProfiles
from universal_stage import digest


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = ModProfiles(self.root / 'Profiles')
        self.stage = self.root / 'stage'
        self.swap = self.root / 'swap'
        self.swap.mkdir()
        self.archive = self.stage / 'paks' / 'Game' / 'libs.pak'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'original stage')
        (self.stage / 'rekey_plan.json').write_text(json.dumps({
            'entries': [{'path': 'Game/libs.pak', 'kind': 'signed-encrypted'}],
            'blocked': [],
        }))
        self.first = self.root / 'first' / 'libs.pak'
        self.second = self.root / 'second' / 'libs.pak'
        self.first.parent.mkdir()
        self.second.parent.mkdir()
        self.first.write_bytes(b'first signed test mod')
        self.second.write_bytes(b'second signed test mod')
        self.restore_calls = 0
        self.install_calls = 0

    def make_mod(self, profile, source=None):
        return self.store.add_mod(profile, source or self.first,
                                  'Game/libs.pak', self.stage)

    def test_create_rename_delete_and_persistent_catalog(self):
        pid = self.store.create('Goliath Balance')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.store.create('goliath balance')
        self.store.rename(pid, 'Different Name')
        self.assertEqual(ModProfiles(self.root / 'Profiles').list_profiles()[0]['name'],
                         'Different Name')
        self.make_mod(pid)
        self.assertTrue(self.store.list_profiles()[0]['mods'][0]['enabled'])
        self.store.set_enabled(pid, self.store.list_profiles()[0]['mods'][0]['id'], False)
        self.assertEqual(self.store.plan(pid, self.stage)[0], [])
        mid = self.store.list_profiles()[0]['mods'][0]['id']
        self.store.remove_mod(pid, mid)
        self.store.delete(pid)
        self.assertEqual(self.store.list_profiles(), [])

    def test_conflicts_and_source_tamper(self):
        pid = self.store.create('Test')
        a = self.make_mod(pid)
        b = self.make_mod(pid, self.second)
        _selected, problems = self.store.plan(pid, self.stage)
        self.assertTrue(any('same PAK' in p for p in problems), problems)
        self.store.set_enabled(pid, b, False)
        _selected, problems = self.store.plan(pid, self.stage)
        self.assertEqual(problems, [])
        self.first.write_bytes(b'tampered')
        _selected, problems = self.store.plan(pid, self.stage)
        self.assertTrue(any('missing or changed' in p for p in problems))
        self.assertEqual(self.archive.read_bytes(), b'original stage')

    def test_filename_and_stage_validation(self):
        pid = self.store.create('Test')
        with self.assertRaises(ValueError):
            self.make_mod(pid, self.root / 'missing.pak')
        wrong = self.root / 'another.pak'
        wrong.write_bytes(b'mod')
        with self.assertRaises(ValueError):
            self.make_mod(pid, wrong)
        with self.assertRaises(ValueError):
            self.store.add_mod(pid, self.first, '../Game/libs.pak', self.stage)
        with self.assertRaises(ValueError):
            self.store.add_mod(pid, self.archive, 'Game/libs.pak', self.stage)

    def fake_install(self, stage, swap, root, relative, source):
        self.install_calls += 1
        backup = Path(root) / 'Game' / 'libs.pak' / 'mock-install'
        backup.mkdir(parents=True)
        previous = self.archive.read_bytes()
        (backup / 'before.pak').write_bytes(previous)
        new = Path(source).read_bytes()
        (backup / 'metadata.json').write_text(json.dumps({
            'status': 'installed',
            'old_sha256': digest(self.archive),
            'new_sha256': digest(source),
        }))
        self.archive.write_bytes(new)
        return backup

    def fake_restore(self, backup):
        self.restore_calls += 1
        backup = Path(backup)
        meta_path = backup / 'metadata.json'
        meta = json.loads(meta_path.read_text())
        self.assertEqual(meta['status'], 'installed')
        self.assertEqual(digest(self.archive), meta['new_sha256'])
        self.archive.write_bytes((backup / 'before.pak').read_bytes())
        meta['status'] = 'restored'
        meta_path.write_text(json.dumps(meta))

    def test_apply_switch_and_restore_without_touching_game(self):
        first = self.store.create('Balance')
        self.make_mod(first)
        second = self.store.create('Textures')
        self.make_mod(second, self.second)
        with patch('mod_profiles.ensure_closed'), \
             patch('mod_profiles.install', side_effect=self.fake_install), \
             patch('mod_profiles.restore', side_effect=self.fake_restore):
            self.assertEqual(self.store.apply(first, self.stage, self.swap), 1)
            self.assertEqual(self.archive.read_bytes(), self.first.read_bytes())
            self.assertEqual(self.store.active_profile_id(), first)
            self.store.apply(first, self.stage, self.swap)
            self.assertEqual(self.install_calls, 1, 'idempotent reapply should be cached')
            self.store.apply(second, self.stage, self.swap)
            self.assertEqual(self.archive.read_bytes(), self.second.read_bytes())
            self.assertEqual(self.install_calls, 2)
            self.assertEqual(self.restore_calls, 1)
            self.store.apply(None, self.stage, self.swap)
            self.assertEqual(self.archive.read_bytes(), b'original stage')
            self.assertEqual(self.store.status()['entry_count'], 0)
            self.assertIsNone(self.store.active_profile_id())

    def test_crash_after_install_is_recovered_before_next_operation(self):
        pid = self.store.create('Test')
        self.make_mod(pid)
        def interrupted(*args):
            self.fake_install(*args)
            raise RuntimeError('simulated sudden process interruption')
        with patch('mod_profiles.ensure_closed'), \
             patch('mod_profiles.install', side_effect=interrupted), \
             patch('mod_profiles.restore', side_effect=self.fake_restore):
            with self.assertRaisesRegex(RuntimeError, 'simulated'):
                self.store.apply(pid, self.stage, self.swap)
            self.assertTrue(self.store.status()['pending'])
            self.store.apply(None, self.stage, self.swap)
            self.assertEqual(self.archive.read_bytes(), b'original stage')
            self.assertFalse(self.store.status()['pending'])
            self.assertEqual(self.store.status()['entry_count'], 0)

    def test_unknown_stage_change_prevents_untracked_overwrite(self):
        pid = self.store.create('Test')
        self.make_mod(pid)
        with patch('mod_profiles.ensure_closed'), \
             patch('mod_profiles.install', side_effect=self.fake_install), \
             patch('mod_profiles.restore', side_effect=self.fake_restore):
            self.store.apply(pid, self.stage, self.swap)
            self.archive.write_bytes(b'untracked edits')
            with self.assertRaisesRegex(RuntimeError, 'changed outside Profiles'):
                self.store.apply(pid, self.stage, self.swap)
            self.assertEqual(self.archive.read_bytes(), b'untracked edits')
            with self.assertRaises(AssertionError):
                self.store.apply(None, self.stage, self.swap)


if __name__ == '__main__':
    unittest.main()
