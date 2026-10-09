"""Non-destructive checks for the unified manager integration. No real game files touched."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launch_integration as integration


class IntegratedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.swap=self.root/'PauseSwapTest';self.swap.mkdir()
        self.stage=self.root/'staged';self.stage.mkdir()
        (self.stage/'stage_status.json').write_text('{}')

    def add_journal(self,phase='prepared',num=234,ready=True,backups=False):
        game=self.root/'game';game.mkdir(exist_ok=True)
        files=[]
        for idx in range(num):
            target=self.root/f'f{idx}.pak';ready_file=self.root/f'f{idx}.pak.customkey-ready';backup=self.root/f'f{idx}.pak.customkey-original'
            if ready:ready_file.write_text('custom')
            if backups:backup.write_text('original')
            files.append(dict(name=f'f{idx}.pak',target=str(target),ready=str(ready_file),backup=str(backup)))
        (self.swap/'controlled_swap_state.json').write_text(json.dumps(dict(version=1,phase=phase,files=files,stage=str(self.stage),game=str(game))))

    def test_helper_install_only_missing(self):
        existing=self.swap/'auto_launch_frida.py'
        existing.write_text('MY WORKING V5 - DO NOT OVERWRITE')
        added=integration.add_missing_scripts(self.swap)
        self.assertEqual(set(added),{'controlled_swap.py','reusable_restore.py'})
        self.assertEqual(existing.read_text(),'MY WORKING V5 - DO NOT OVERWRITE')
        self.assertEqual(integration.add_missing_scripts(self.swap),[])

    def test_prepared_state_and_commands(self):
        integration.add_missing_scripts(self.swap)
        self.add_journal()
        s=integration.state_status(self.swap)
        self.assertEqual((s.phase,s.count,s.ready,s.backups),('prepared',234,234,0))
        cmds=integration.launcher_commands(self.swap,self.stage,s.phase)
        self.assertEqual(len(cmds),1)
        self.assertTrue(cmds[0][1][-1].endswith('auto_launch_frida.py'))

    def test_restored_requires_prepare_then_launch(self):
        integration.add_missing_scripts(self.swap)
        self.add_journal('restored',ready=False)
        s=integration.state_status(self.swap)
        self.assertEqual(s.phase,'restored')
        cmds=integration.launcher_commands(self.swap,self.stage,s.phase)
        self.assertEqual(len(cmds),2)
        self.assertIn('prepare',cmds[0][1]);self.assertIn('--game-root',cmds[0][1])
        self.assertIn('auto_launch_frida.py',cmds[1][1][-1])

    def test_unsafe_prepared_journal_blocks_play(self):
        integration.add_missing_scripts(self.swap)
        self.add_journal('prepared',ready=False)
        s=integration.state_status(self.swap)
        self.assertEqual(s.phase,'unsafe')
        with self.assertRaises(RuntimeError):
            integration.launcher_commands(self.swap,self.stage,s.phase)

    def test_swapped_does_not_prepare(self):
        integration.add_missing_scripts(self.swap)
        self.add_journal('swapped',ready=False,backups=True)
        s=integration.state_status(self.swap)
        self.assertEqual(s.phase,'swapped')
        with self.assertRaises(RuntimeError):integration.launcher_commands(self.swap,self.stage,s.phase)
        self.assertTrue(integration.restore_command(self.swap)[-1].endswith('reusable_restore.py'))

    def test_reject_invalid_json(self):
        (self.swap/'controlled_swap_state.json').write_text('not json')
        self.assertEqual(integration.state_status(self.swap).phase,'unsafe')


if __name__=='__main__':unittest.main(verbosity=2)
