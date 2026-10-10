"""Regression: first Add Mod works with empty restored swap journal.

Uses a generated signed PAK and local temporary game, not real game files.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evolve_gameplay_editor import decrypt_entry, decode_cryxml
from evolve_pak_workspace import cmd_extract, cmd_build
from evolve_pak_rekey import load_with_signed_basename
from universal_stage import digest, install, restore
from workspace_editor import save_text, digest as digest_bytes
from test_editor_refresh import RefreshTests
from refresh_originals import refresh


class AddModRestoredTests(unittest.TestCase):
    def setUp(self):
        RefreshTests.setUp(self)
        self.report = refresh(self.game, self.stage, self.swap, self.dest)
        self.new_stage = Path(self.report['stage'])
        self.new_swap = Path(self.report['swap'])
        self.staged = self.new_stage / 'paks' / 'Game' / 'libs.pak'
        self.stage_original_hash = digest(self.staged)
        self.backup_root = self.root / 'Backups'
        self.mod = self.root / 'output' / 'libs.pak'
        self.mod.parent.mkdir()
        project = self.root / 'mod-project'
        key = self.new_stage / 'mykeys' / 'public_key.bin'
        cmd_extract(SimpleNamespace(pak=self.staged, public_key=key,
                                    workspace=project, filter=[], skip_unsupported=True))
        rel = 'libs/Abilities/test.xml'
        raw = (project / 'files' / rel).read_bytes()
        save_text(project, rel, raw.decode().replace('damage="100"', 'damage="900"'),
                  digest_bytes(raw), raw, True)
        cmd_build(SimpleNamespace(workspace=project, pak=None, public_key=key,
                                  private_key=self.new_stage / 'mykeys' / 'private_key.pem',
                                  out=self.mod))
        self.journal = self.new_swap / 'controlled_swap_state.json'

    def _install(self):
        return install(self.new_stage, self.new_swap, self.backup_root,
                       'Game/libs.pak', self.mod)

    def test_fresh_restored_empty_journal_add_then_undo(self):
        state = json.loads(self.journal.read_text())
        self.assertEqual((state['phase'], state['files']), ('restored', []))
        original_journal = self.journal.read_bytes()
        original_game = self.pak.read_bytes()
        backup = self._install()
        self.assertTrue((backup / 'before.pak').is_file())
        self.assertEqual(json.loads((backup / 'metadata.json').read_text())['status'], 'installed')
        self.assertNotEqual(digest(self.staged), self.stage_original_hash)
        self.assertEqual(self.journal.read_bytes(), original_journal)
        self.assertEqual(self.pak.read_bytes(), original_game)
        restore(backup)
        self.assertEqual(digest(self.staged), self.stage_original_hash)
        self.assertEqual(self.pak.read_bytes(), original_game)

    def test_prepared_without_archive_record_is_rejected(self):
        state = json.loads(self.journal.read_text())
        state['phase'] = 'prepared'
        self.journal.write_text(json.dumps(state))
        with self.assertRaisesRegex(RuntimeError, 'Prepared swap journal does not contain'):
            self._install()
        self.assertEqual(digest(self.staged), self.stage_original_hash)
        self.assertFalse(self.backup_root.exists())

    def test_restored_foreign_stage_is_rejected(self):
        state = json.loads(self.journal.read_text())
        state['stage'] = str(self.root / 'another-stage')
        self.journal.write_text(json.dumps(state))
        with self.assertRaisesRegex(RuntimeError, 'another game or signing stage'):
            self._install()
        self.assertEqual(digest(self.staged), self.stage_original_hash)

    def test_restored_with_leftover_recovery_file_is_rejected(self):
        state = json.loads(self.journal.read_text())
        backup_file = Path(str(self.pak) + '.customkey-original')
        backup_file.write_bytes(b'do not touch')
        state['files'] = [{'name': 'Game/libs.pak', 'source': str(self.staged),
                           'ready': str(self.pak) + '.customkey-ready',
                           'backup': str(backup_file)}]
        self.journal.write_text(json.dumps(state))
        with self.assertRaisesRegex(RuntimeError, 'recovery files'):
            self._install()
        self.assertEqual(backup_file.read_bytes(), b'do not touch')
        self.assertEqual(digest(self.staged), self.stage_original_hash)

    def test_untracked_live_swap_backup_stops_add_even_with_empty_journal(self):
        safety = Path(str(self.pak) + '.customkey-original')
        safety.write_bytes(b'important unsaved original')
        with self.assertRaisesRegex(RuntimeError, 'Untracked live-swap recovery'):
            self._install()
        self.assertEqual(safety.read_bytes(), b'important unsaved original')
        self.assertEqual(digest(self.staged), self.stage_original_hash)

    def test_prepared_record_is_updated_and_undo_restores_it(self):
        spec = importlib.util.spec_from_file_location('swap_test', self.new_swap / 'controlled_swap.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with patch.object(helper, 'all_closed'):
            helper.prepare(SimpleNamespace(game_root=self.game, stage_dir=self.new_stage))
        before = helper.require_state()
        record = next(f for f in before['files'] if f['name'].replace('\\','/') == 'Game/libs.pak')
        ready = Path(record['ready'])
        self.assertEqual(digest(ready), self.stage_original_hash)
        backup = self._install()
        updated = helper.require_state()
        new_rec = next(f for f in updated['files'] if f['name'].replace('\\','/') == 'Game/libs.pak')
        self.assertEqual(new_rec['sha256'], digest(self.mod))
        self.assertEqual(digest(ready), digest(self.mod))
        restore(backup)
        self.assertEqual(digest(self.staged), self.stage_original_hash)
        self.assertEqual(digest(ready), self.stage_original_hash)
        self.assertEqual(helper.require_state()['phase'], 'prepared')


if __name__ == '__main__':
    unittest.main()
