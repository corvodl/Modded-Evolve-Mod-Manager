"""Initial setup from game/client alone and clean app-only publishing."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import test_editor_refresh as fixtures
from first_run_setup import setup_from_game, public_key_from_shim
from evolve_pak_rekey import load_with_signed_basename
from publish_release import publish
from old_prepared import scan_prepared, preserve_ready

class FirstSetupTests(unittest.TestCase):
    def setUp(self):
        fixtures.RefreshTests.setUp(self)
        guard=patch('first_run_setup.ensure_closed');guard.start();self.addCleanup(guard.stop)
        # Remove ALL prior manager resources. Only installed game/client remain.
        shutil.rmtree(self.stage.parent);shutil.rmtree(self.swap)
        self.client=self.root/'ModdedEvolveLauncher.exe';self.client.write_bytes(b'fixture client')
        self.seed=self.root/'app'/'Data'/'SetupSeeds'
        self.destination=self.root/'app'/'Data'/'GameSets'/'new'

    def test_game_and_client_alone_generate_working_signing_stage(self):
        shim=self.game/'bin64_SteamRetail'/'inject.dll';before=shim.read_bytes()
        report=setup_from_game(self.game,self.client,self.destination,self.seed)
        stage=Path(report['stage']);swap=Path(report['swap'])
        self.assertTrue(report['initial_setup']);self.assertEqual(report['status'],'complete')
        self.assertEqual(self.pak.read_bytes(),self.before);self.assertEqual(shim.read_bytes(),before)
        self.assertEqual((self.destination/'RSAKeyData.bin').read_bytes(),self.pub)
        self.assertNotEqual((stage/'mykeys'/'public_key.bin').read_bytes(),self.pub)
        parsed,_=load_with_signed_basename(stage/'paks'/'Game'/'libs.pak',stage/'mykeys'/'public_key.bin')
        self.assertEqual(parsed['count'],1)
        self.assertEqual(json.loads((swap/'launcher_config.json').read_text())['launcher'],str(self.client))
        self.assertTrue((swap/'auto_launch_frida.py').is_file())
        self.assertTrue((stage/'inject-custom.dll').is_file())
        self.assertEqual((stage/'inject-custom.dll').read_bytes()[0x870:0x8fc], (stage/'mykeys'/'public_key.bin').read_bytes())
        self.assertTrue((stage/'mykeys'/'private_key.pem').is_file())
        self.assertEqual(list(self.seed.iterdir()),[])
        import importlib.util
        spec=importlib.util.spec_from_file_location('initial_swap',swap/'controlled_swap.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        with patch.object(worker,'all_closed'):worker.prepare(SimpleNamespace(game_root=self.game,stage_dir=stage))
        self.assertEqual(worker.require_state()['phase'],'prepared')
        self.assertEqual(worker.require_state()['count'],2)

    def test_old_ready_files_are_preserved_only_with_user_opt_in(self):
        extra = Path(str(self.pak) + '.customkey-ready')
        extra.write_bytes(b'previous mod from an older setup')
        with self.assertRaisesRegex(ValueError, 'Untracked prepared files'):
            setup_from_game(self.game, self.client, self.destination, self.seed)
        self.assertTrue(extra.is_file())
        self.assertFalse(self.destination.exists())
        report = setup_from_game(self.game, self.client, self.destination, self.seed, archive_ready=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(self.pak.read_bytes(), self.before)
        self.assertFalse(extra.exists())
        saved = list(self.game.rglob('*.customkey-ready.saved-*'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b'previous mod from an older setup')
        old = report['preserved_old_prepared']
        self.assertEqual(old['count'], 1)
        manifest = json.loads(Path(old['manifest']).read_text())
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(manifest['files'][0]['old'], str(extra))

    def test_previous_original_backup_not_overridden_even_if_opted_in(self):
        extra = Path(str(self.pak) + '.customkey-ready')
        backup = Path(str(self.pak) + '.customkey-original')
        extra.write_bytes(b'important prepared data')
        backup.write_bytes(b'original backup')
        with self.assertRaisesRegex(ValueError, 'Original backups'):
            setup_from_game(self.game, self.client, self.destination, self.seed, archive_ready=True)
        self.assertEqual(extra.read_bytes(), b'important prepared data')
        self.assertEqual(backup.read_bytes(), b'original backup')
        self.assertFalse(self.destination.exists())

    def test_incomplete_preparation_not_archived(self):
        extra = Path(str(self.pak) + '.customkey-ready.partial')
        extra.write_bytes(b'partial write')
        with self.assertRaisesRegex(ValueError, 'unfinished PAK preparation'):
            setup_from_game(self.game, self.client, self.destination, self.seed, archive_ready=True)
        self.assertEqual(extra.read_bytes(), b'partial write')
        self.assertFalse(self.destination.exists())

    def test_preserve_is_reversible_if_a_rename_fails(self):
        ready1 = Path(str(self.pak) + '.customkey-ready')
        ready1.write_bytes(b'first')
        ready2 = self.game/'Game'/'zzz.pak.customkey-ready'
        ready2.write_bytes(b'second')
        from old_prepared import os as preserver_os
        original_replace = preserver_os.replace
        def simulate_error(old, new):
            if str(old) == str(ready2):
                raise OSError('simulated rename failure')
            return original_replace(old, new)
        with patch('old_prepared.os.replace', side_effect=simulate_error):
            with self.assertRaisesRegex(OSError, 'simulated rename failure'):
                preserve_ready(self.game, self.seed.parent/'ArchivedPrepared')
        self.assertEqual(ready1.read_bytes(), b'first')
        self.assertEqual(ready2.read_bytes(), b'second')
        self.assertEqual(list(self.game.rglob('*.customkey-ready.saved-*')), [])

    def test_bundled_reference_matches_original_shim(self):
        from first_run_setup import public_key_from_shim
        ref=Path(__file__).parent/'reference'
        self.assertEqual(public_key_from_shim(ref/'inject.dll'), (ref/'RSAKeyData.bin').read_bytes())

    def test_unsupported_shim_stops_before_writing_setup(self):
        (self.game/'bin64_SteamRetail'/'inject.dll').write_bytes(b'unknown shim')
        with self.assertRaisesRegex(ValueError,'layout'):setup_from_game(self.game,self.client,self.destination,self.seed)
        self.assertFalse(self.destination.exists());self.assertFalse(self.seed.exists())
        self.assertEqual(self.pak.read_bytes(),self.before)

    def test_invalid_key_stops_before_writing_setup(self):
        (self.game/'bin64_SteamRetail'/'inject.dll').write_bytes(bytes(4096))
        with self.assertRaisesRegex(ValueError,'public key'):setup_from_game(self.game,self.client,self.destination,self.seed)
        self.assertFalse(self.seed.exists())

    def test_existing_swap_backup_blocks_first_setup(self):
        Path(str(self.pak)+'.customkey-original').write_bytes(b'important original')
        with self.assertRaisesRegex(ValueError,'Restore'):setup_from_game(self.game,self.client,self.destination,self.seed)
        self.assertFalse(self.seed.exists())

    def test_existing_setup_directory_not_overwritten(self):
        self.destination.mkdir(parents=True);(self.destination/'keep.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError,'new folder'):setup_from_game(self.game,self.client,self.destination,self.seed)
        self.assertEqual((self.destination/'keep.txt').read_text(),'keep')

class PublishTests(unittest.TestCase):
    def test_clean_zip_excludes_existing_user_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);built=root/'built';built.mkdir();(built/'_internal').mkdir()
            for name in ('EvolveModManager.exe','EvolveModWorker.exe'):(built/name).write_bytes(b'fixture')
            (built/'BUILD_COMMIT.txt').write_text('a'*40)
            (built/'BUILD_CHANNEL.txt').write_text('main')
            dist=root/'dist';previous=dist/'ExistingApp'/'Data';previous.mkdir(parents=True)
            (previous/'private_key.pem').write_text('DO NOT TOUCH')
            release,archive=publish(built,dist)
            self.assertEqual((previous/'private_key.pem').read_text(),'DO NOT TOUCH')
            with zipfile.ZipFile(archive) as z:
                self.assertFalse(any('/Data/' in n for n in z.namelist()));self.assertIsNone(z.testzip())
            self.assertNotEqual(release,previous.parent)
            (built/'Data').mkdir()
            with self.assertRaisesRegex(ValueError,'personal'):publish(built,dist)
            shutil.rmtree(built/'Data')
            (built/'game.pak').write_bytes(b'do not publish')
            with self.assertRaisesRegex(ValueError,'PAKs'):publish(built,dist)
            (built/'game.pak').unlink()
            (built/'private_key.pem').write_bytes(b'do not publish')
            with self.assertRaisesRegex(ValueError,'personal'):publish(built,dist)

if __name__=='__main__':unittest.main()
