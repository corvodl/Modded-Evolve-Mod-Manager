"""Optional long-term snapshots never disable required live-swap backups."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from evolve_pak_rekey import load_with_signed_basename
from refresh_originals import refresh
from first_run_setup import setup_from_game
import test_editor_refresh as fixtures
import test_first_run_setup as setup_tests


class SnapshotFreeRefreshTests(unittest.TestCase):
    def setUp(self):
        fixtures.RefreshTests.setUp(self)

    def test_opt_out_removes_only_permanent_original_snapshots(self):
        before_shim=(self.game/'bin64_SteamRetail'/'inject.dll').read_bytes()
        report=refresh(self.game,self.stage,self.swap,self.dest,keep_original_snapshots=False)
        self.assertFalse(report['keep_original_snapshots'])
        self.assertFalse((self.dest/'originals').exists())
        self.assertEqual(self.pak.read_bytes(),self.before)
        self.assertEqual((self.game/'bin64_SteamRetail'/'inject.dll').read_bytes(),before_shim)
        new_stage=Path(report['stage'])
        parsed,_=load_with_signed_basename(new_stage/'paks'/'Game'/'libs.pak',new_stage/'mykeys'/'public_key.bin')
        self.assertEqual(parsed['count'],1)
        self.assertTrue((new_stage/'inject-custom.dll').is_file())
        self.assertEqual(report['signed_count'],1)

    def test_skip_snapshots_signs_directly_from_installed_paks(self):
        import refresh_originals
        real=refresh_originals.cmd_resign
        seen=[]
        def traced(args):
            seen.append(args.original)
            return real(args)
        with patch('refresh_originals.cmd_resign',side_effect=traced):
            refresh(self.game,self.stage,self.swap,self.dest,keep_original_snapshots=False)
        self.assertEqual(seen,[self.pak])

    def test_bad_original_signature_is_rejected_without_snapshots(self):
        fixtures.fixture(self.pak,self.custom_key)
        with self.assertRaises(ValueError):
            refresh(self.game,self.stage,self.swap,self.dest,keep_original_snapshots=False)
        self.assertFalse((self.dest/'staged'/'stage_status.json').exists())
        self.assertFalse((self.stage/'REFRESHED-TO.json').exists())

    def test_space_estimate_reduces_with_opt_out(self):
        import refresh_originals
        import shutil
        old_free=int(1024**3 + self.pak.stat().st_size*1.5)
        with patch('refresh_originals.shutil.disk_usage',return_value=SimpleNamespace(free=old_free)):
            with self.assertRaisesRegex(ValueError,'free'):
                refresh(self.game,self.stage,self.swap,self.dest)
            self.assertFalse(self.dest.exists())
            report=refresh(self.game,self.stage,self.swap,self.dest,keep_original_snapshots=False)
        self.assertEqual(report['status'],'complete')


class SnapshotFreeSetupTests(unittest.TestCase):
    def setUp(self):
        setup_tests.FirstSetupTests.setUp(self)

    def test_first_time_opt_out_can_swap_restore_every_signed_pak(self):
        shim=self.game/'bin64_SteamRetail'/'inject.dll'
        before_shim=shim.read_bytes()
        report=setup_from_game(self.game,self.client,self.destination,self.seed,
                               keep_original_snapshots=False)
        self.assertFalse(report['keep_original_snapshots'])
        self.assertFalse((self.destination/'originals').exists())
        self.assertEqual(self.pak.read_bytes(),self.before)
        self.assertEqual(shim.read_bytes(),before_shim)
        module_path=Path(report['swap'])/'controlled_swap.py'
        spec=importlib.util.spec_from_file_location('snapshot_free_swap',module_path)
        worker=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with patch.object(worker,'all_closed'),patch.object(worker,'no_game'):
            worker.prepare(SimpleNamespace(game_root=self.game,stage_dir=Path(report['stage'])))
            state=worker.require_state()
            self.assertEqual(state['count'],2)
            self.assertEqual(len(state['files']),2)  # encrypted archive and injector still need matching keys
            worker.swap(SimpleNamespace(confirm_launcher_paused=True))
            self.assertNotEqual(self.pak.read_bytes(),self.before)
            self.assertEqual(Path(str(self.pak)+'.customkey-original').read_bytes(),self.before)
            self.assertEqual(Path(str(shim)+'.customkey-original').read_bytes(),before_shim)
            worker.restore(SimpleNamespace())
            self.assertEqual(worker.require_state()['phase'],'restored')
            self.assertEqual(self.pak.read_bytes(),self.before)
            self.assertEqual(shim.read_bytes(),before_shim)
            self.assertFalse(Path(str(self.pak)+'.customkey-original').exists())

    def test_first_time_default_retains_snapshots(self):
        report=setup_from_game(self.game,self.client,self.destination,self.seed)
        self.assertTrue(report['keep_original_snapshots'])
        self.assertEqual((self.destination/'originals'/'Game'/'libs.pak').read_bytes(),self.before)


if __name__=='__main__': unittest.main()