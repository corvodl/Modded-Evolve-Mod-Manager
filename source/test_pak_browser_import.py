"""Readable main PAK list and safely routed signed-PAK imports."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from dark_theme import apply_theme
from pak_browser import describe_archive, matching_archives, import_target
from pak_manager_gui import Manager
from ui_copy import load_text


class BrowserLogicTests(unittest.TestCase):
    def test_filename_and_path_columns(self):
        self.assertEqual(describe_archive('Game/characters_monsters_goliath_data.pak'),
                         ('characters_monsters_goliath_data.pak', 'Game'))
        self.assertEqual(describe_archive('libs.pak'), ('libs.pak', '/'))

    def test_search_sort_and_case_insensitivity(self):
        rows = ['Game/zeta.pak', 'Mods/alpha.pak', 'Game/alpha.pak', 'Game/props.pak']
        self.assertEqual(matching_archives(rows, 'ALPHA'), ['Game/alpha.pak', 'Mods/alpha.pak'])
        self.assertEqual(matching_archives(rows, sort_by='location'),
                         ['Game/alpha.pak', 'Game/props.pak', 'Game/zeta.pak', 'Mods/alpha.pak'])

    def test_import_target_basename_and_ambiguous_stage(self):
        self.assertEqual(import_target('LIBS.pak', ['Game/libs.pak']), 'Game/libs.pak')
        with self.assertRaisesRegex(ValueError, 'retain'):
            import_target('other.pak', ['Game/libs.pak'])
        rows = ['Game/props.pak', 'DLC/props.pak']
        with self.assertRaisesRegex(ValueError, 'Select the correct'):
            import_target('props.pak', rows)
        self.assertEqual(import_target('PROPS.pak', rows, 'DLC/props.pak'), 'DLC/props.pak')


class BrowserInterfaceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('No Tk desktop: ' + str(exc))
        self.addCleanup(self.root.destroy)
        self.root.geometry('1080x760+0+0')
        apply_theme(self.root)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.stage = self.project / 'stage'
        (self.stage/'paks'/'Game').mkdir(parents=True)
        self.sample = self.stage/'paks'/'Game'/'goliath.pak'
        self.sample.write_bytes(b'original signed fixture')
        self.manager = Manager.__new__(Manager)
        self.manager.window = self.root
        self.manager.copy, _ = load_text()
        self.manager.archive_entries = ['Game/goliath.pak', 'Game/libs.pak', 'DLC/props.pak']
        self.manager.batch_workspaces = {}
        self.manager.visible = []
        for name, value in {'search':'', 'archive_count':'', 'archive_label':'',
                            'stage':str(self.stage), 'swap':str(self.project/'swap'),
                            'projects':str(self.project/'projects'), 'current_workspace':'',
                            'external_pak':'', 'output_pak':''}.items():
            setattr(self.manager, name, tk.StringVar(master=self.root, value=value))
        panel = ttk.Frame(self.root)
        panel.pack(fill='both',expand=True)
        self.manager.draw_edit(panel)
        self.manager.refresh_list()
        self.root.update()

    def test_header_content_search_and_selection(self):
        tree = self.manager.archives
        self.assertIsInstance(tree, ttk.Treeview)
        self.assertEqual(str(tree['selectmode']), 'extended')
        self.assertEqual(tuple(tree['columns']), ('name','location','project'))
        self.assertEqual(tree.item('Game/goliath.pak')['values'][:2],
                         ['goliath.pak', 'Game'])
        self.manager.search.set('goliath')
        self.root.update()
        self.assertEqual(self.manager.visible, ['Game/goliath.pak'])
        tree.selection_set('Game/goliath.pak')
        tree.focus('Game/goliath.pak')
        self.manager.select_archive()
        self.assertEqual(self.manager.archive_label.get(), 'Game/goliath.pak')
        self.assertEqual(self.manager.browser_selection.get(), 'goliath.pak')

    def test_multi_select_and_sort_without_losing_selection(self):
        tree=self.manager.archives
        tree.selection_set(['Game/libs.pak','DLC/props.pak'])
        tree.focus('Game/libs.pak')
        self.manager.select_archive()
        self.assertEqual(set(self.manager.selected_archive_relatives()),
                         {'Game/libs.pak','DLC/props.pak'})
        self.assertEqual(self.manager.browser_selection.get(), '2 selected')
        self.manager.sort_archives('location')
        self.assertEqual(set(self.manager.selected_archive_relatives()),
                         {'Game/libs.pak','DLC/props.pak'})

    def test_external_import_uses_guarded_staging_worker_not_workspace(self):
        mod = self.project/'modified'/'goliath.pak'
        mod.parent.mkdir(parents=True)
        mod.write_bytes(b'externally signed fixture')
        manager=self.manager
        manager.busy=False
        manager.run_steps=lambda *args, **kwargs: setattr(manager, 'recorded', (args,kwargs))
        with patch('pak_manager_gui.allowed_paks',return_value={'game/goliath.pak':'Game/goliath.pak'}), \
             patch('pak_manager_gui.filedialog.askopenfilename',return_value=str(mod)), \
             patch('pak_manager_gui.messagebox.askyesno',return_value=True):
            manager.import_modified_pak()
        args, kwargs = manager.recorded
        step = args[0][0]
        command = step[1]
        self.assertIn('universal_stage.py',command[2])
        self.assertEqual(command[3],'install')
        self.assertEqual(command[command.index('--relative') + 1], 'Game/goliath.pak')
        self.assertEqual(Path(command[command.index('--mod') + 1]), mod)
        self.assertEqual(args[1], 'Import Modified PAK')
        # No stage archive changes until the guarded helper runs to completion.
        self.assertEqual(self.sample.read_bytes(), b'original signed fixture')

    def test_cancel_import_never_starts_operation(self):
        mod = self.project/'modified'/'goliath.pak'
        mod.parent.mkdir(parents=True)
        mod.write_bytes(b'fixture')
        manager = self.manager
        manager.busy = False
        with patch('pak_manager_gui.allowed_paks',return_value={'game/goliath.pak':'Game/goliath.pak'}), \
             patch('pak_manager_gui.filedialog.askopenfilename',return_value=str(mod)), \
             patch('pak_manager_gui.messagebox.askyesno',return_value=False), \
             patch.object(manager, 'run_steps') as runs:
            manager.import_modified_pak()
            runs.assert_not_called()


if __name__ == '__main__':
    unittest.main()
