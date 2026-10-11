"""Profile 2.0 portable manifests: no game bytes, absolute paths or unverified PAKs."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from mod_profiles import ModProfiles
import hashlib


class ProfileV2Tests(TestCase):
    def test_shared_manifest_has_no_absolute_paths_and_checks_hashes(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            stage=root/'staged'
            (stage/'paks').mkdir(parents=True)
            mods=root/'received'
            mods.mkdir()
            content=b'example signed archive for offline fixture'
            (mods/'test.pak').write_bytes(content)
            digest=hashlib.sha256(content).hexdigest()
            allowed={'test.pak':'test.pak'}
            with patch('mod_profiles.allowed_paks',return_value=allowed):
                store=ModProfiles(root/'profiles')
                pid=store.create('Development')
                store.add_mod(pid,mods/'test.pak','test.pak',stage)
                duplicate=store.duplicate_profile(pid,'Duplicate')
                self.assertNotEqual(pid,duplicate)
                destination=root/'share.json'
                store.export_manifest(pid,destination)
                exported=destination.read_text('utf-8')
                self.assertNotIn(str(mods),exported)
                self.assertIn(digest,exported)
                shared=json.loads(exported)
                self.assertEqual('evolve-mod-profile-v2',shared['format'])
                imported=store.import_manifest(destination,mods,stage,name='Imported')
                self.assertEqual(len(store.list_profiles()),3)
                self.assertEqual(store.list_profiles()[-1]['id'],imported)
                self.assertEqual(store.list_profiles()[-1]['mods'][0]['sha256'],digest)
                (mods/'test.pak').write_bytes(b'different bytes')
                with self.assertRaises(ValueError):
                    store.import_manifest(destination,mods,stage,name='Tampered')

    def test_reorder_entries_preserves_verified_target_order(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            stage=root/'staged'
            (stage/'paks').mkdir(parents=True)
            with patch('mod_profiles.allowed_paks',
                       return_value={'a.pak':'a.pak','b.pak':'b.pak'}):
                store=ModProfiles(root/'profiles')
                pid=store.create('Work')
                for filename in ('a.pak','b.pak'):
                    archive=root/filename
                    archive.write_bytes(filename.encode())
                    store.add_mod(pid,archive,filename,stage)
                before=store.list_profiles()[0]['mods']
                store.move_mod(pid,before[1]['id'],-1)
                after=store.list_profiles()[0]['mods']
                self.assertEqual(['b.pak','a.pak'],
                                 [m['relative'] for m in after])
                _choices, errors=store.plan(pid,stage,verify_files=True)
                self.assertFalse(errors)
