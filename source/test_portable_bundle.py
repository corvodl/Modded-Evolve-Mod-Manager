"""Portable export/relocation with generated CryPAKs; no real game files."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import test_editor_refresh as fixtures
from refresh_originals import refresh
from evolve_pak_workspace import cmd_extract
from portable_bundle import portable_export,bind_home,configure,STAGE,SWAP,PROJECTS,mapped

class PortableTests(unittest.TestCase):
    def setUp(self):
        fixtures.RefreshTests.setUp(self)
        guard=patch('portable_bundle.ensure_closed');guard.start();self.addCleanup(guard.stop)
        report=refresh(self.game,self.stage,self.swap,self.dest)
        self.new_stage=Path(report['stage']);self.new_swap=Path(report['swap'])
        self.home=self.root/'app';self.home.mkdir()
        for name in ('EvolveModManager.exe','EvolveModWorker.exe'):(self.home/name).write_bytes(b'fixture executable')
        (self.home/'_internal').mkdir();(self.home/'_internal'/'fixture.dll').write_bytes(b'fixture runtime')
        self.projects=self.root/'projects';self.projects.mkdir();self.ws=self.projects/'edit'
        cmd_extract(SimpleNamespace(pak=self.new_stage/'paks'/'Game'/'libs.pak',public_key=self.new_stage/'mykeys'/'public_key.bin',workspace=self.ws,filter=[],skip_unsupported=True))
        self.output=self.root/'complete.zip'

    def export(self):
        portable_export(self.home,self.new_stage,self.new_swap,self.projects,self.output)
        target=self.root/'recipient'
        with zipfile.ZipFile(self.output) as z:
            self.assertIsNone(z.testzip());z.extractall(target)
        return target/'EvolveModManager'

    def test_complete_export_relocation_and_matching_installation(self):
        home=self.export()
        for rel in (STAGE+'/mykeys/private_key.pem',STAGE+'/paks/Game/libs.pak',SWAP+'/auto_launch_frida.py'):
            self.assertTrue((home/rel).is_file())
        self.assertFalse((home/'Evolve.exe').exists())
        self.assertFalse(bind_home(home)['configured'])
        meta=home/PROJECTS/'edit'/'.evolve-pak-workspace.json'
        self.assertEqual(Path(json.loads(meta.read_text())['source_pak']),home/STAGE/'paks'/'Game'/'libs.pak')
        game=self.root/'recipient_game';shutil.copytree(self.game,game)
        client=self.root/'normal-client.exe';client.write_bytes(b'fixture')
        configure(home,game,client)
        state=json.loads((home/SWAP/'controlled_swap_state.json').read_text())
        self.assertEqual(state['game'],str(game));self.assertEqual(state['stage'],str(home/STAGE));self.assertEqual(state['files'],[])
        self.assertEqual(json.loads((home/SWAP/'launcher_config.json').read_text())['launcher'],str(client))
        self.assertEqual(json.loads((home/STAGE/'rekey_plan.json').read_text())['source_root'],str(game))
        moved=home.with_name('MovedManager');home.rename(moved)
        self.assertFalse(bind_home(moved)['configured'])
        self.assertEqual(Path(json.loads((moved/PROJECTS/'edit'/'.evolve-pak-workspace.json').read_text())['source_pak']),moved/STAGE/'paks'/'Game'/'libs.pak')

    def test_different_game_files_block_configuration(self):
        home=self.export();self.pak.write_bytes(b'different version')
        client=self.root/'client.exe';client.write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError,'differs'):configure(home,self.game,client)
        self.assertFalse(json.loads((home/'portable_bundle.json').read_text())['configured'])

    def test_external_project_source_is_collected(self):
        external=self.root/'oldsource'/'libs.pak';external.parent.mkdir();shutil.copy2(self.new_stage/'paks'/'Game'/'libs.pak',external)
        meta=self.ws/'.evolve-pak-workspace.json';data=json.loads(meta.read_text());data['source_pak']=str(external);meta.write_text(json.dumps(data))
        home=self.export();bind_home(home)
        source=Path(json.loads((home/PROJECTS/'edit'/'.evolve-pak-workspace.json').read_text())['source_pak'])
        self.assertTrue(source.is_relative_to(home/'Data'/'ProjectSources'));self.assertEqual(source.read_bytes(),external.read_bytes())

    def test_active_swap_does_not_export(self):
        jp=self.new_swap/'controlled_swap_state.json';state=json.loads(jp.read_text());state['phase']='swapped';jp.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'Restore'):self.export()
        self.assertFalse(self.output.exists())

    def test_windows_path_mapping_respects_boundaries(self):
        self.assertEqual(mapped(r'C:\Old\stage\paks\a.pak',[(r'C:\Old\stage','{APP}/Data/staged')]),'{APP}/Data/staged/paks/a.pak')
        self.assertEqual(mapped(r'C:\Old\stage2\a.pak',[(r'C:\Old\stage','x')]),r'C:\Old\stage2\a.pak')

if __name__=='__main__':unittest.main()
