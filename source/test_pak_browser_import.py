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
from pak_browser import describe_archive, archive_description, matching_archives, import_target
from pak_manager_gui import Manager
from ui_copy import load_text


class BrowserLogicTests(unittest.TestCase):
    def test_filename_and_path_columns(self):
        self.assertEqual(describe_archive('Game/characters_monsters_goliath_data.pak'),
                         ('characters_monsters_goliath_data.pak', 'Game'))
        self.assertEqual(describe_archive('libs.pak'), ('libs.pak', '/'))

    def test_filename_inferred_descriptions(self):
        self.assertEqual(archive_description('Game/characters_monsters_goliath_data.pak'),
                         'Goliath Models')
        self.assertEqual(archive_description('Game/characters_monsters_goliath_ts.pak'),
                         'Goliath Textures')
        self.assertEqual(archive_description('Game/characters_hunters_merc_caira_ts.pak'),
                         'Caira Textures')
        self.assertEqual(archive_description('Game/objects_basic_props.pak'),
                         'Basic Props Objects')
        self.assertEqual(archive_description('Game/UI_Data.pak'), 'Interface Data')
        self.assertEqual(archive_description('Game/sounds_hunters.pak'), 'Hunters Audio')
        self.assertEqual(archive_description('Game/unknown_archive.pak'), 'Unknown Archive')
        self.assertEqual(matching_archives(
            ['Game/characters_monsters_goliath_data.pak',
             'Game/characters_monsters_goliath_ts.pak'], 'Goliath Models'),
            ['Game/characters_monsters_goliath_data.pak'])

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
        self.manager.archive_entries = ['Game/goliath.pak', 'Game/libs.pak', 'DLC/props.pak',
                                        'Game/characters_monsters_goliath_data.pak']
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
        self.assertEqual(tuple(tree['columns']), ('folder','description','name','project','size'))
        self.assertEqual(tree.item('Game/goliath.pak')['values'][:3],
                         ['Game', 'Goliath', 'goliath.pak'])
        self.assertEqual(tree.item('Game/characters_monsters_goliath_data.pak')['values'][1],
                         'Goliath Models')
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
        self.manager.sort_archives('description')
        self.assertEqual(set(self.manager.selected_archive_relatives()),
                         {'Game/libs.pak','DLC/props.pak'})

    def test_context_menu_right_click_selects_clicked_pak_not_stale_selection(self):
        from types import SimpleNamespace
        manager = self.manager
        tree = manager.archives
        manager.game_root = self.project / 'game'
        (manager.game_root / 'Game').mkdir(parents=True)
        original = manager.game_root / 'Game' / 'goliath.pak'
        original.write_bytes(b'original game archive')
        tree.selection_set('DLC/props.pak')
        target = 'Game/goliath.pak'
        self.root.update()
        coords = tree.bbox(target)
        self.assertTrue(coords)
        event = SimpleNamespace(x=coords[0]+10, y=coords[1]+5,
                                x_root=100, y_root=150, keysym='')
        seen = {}
        def fake_popup(menu, x, y):
            seen['labels'] = [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1)
                              if menu.type(i) != 'separator']
            seen['commands'] = menu
        with patch('tkinter.Menu.tk_popup', autospec=True, side_effect=fake_popup):
            manager.show_archive_context_menu(event)
        self.assertEqual(manager.archive_label.get(), target)
        self.assertEqual(tree.focus(), target)
        self.assertIn('Unpack PAK', seen['labels'])
        self.assertIn('Inspect PAK Details', seen['labels'])
        self.assertIn('Show Original Game File in Explorer', seen['labels'])

    def test_context_paths_keep_stage_and_original_separate(self):
        manager = self.manager
        manager.game_root = self.project / 'installed' / 'EvolveGame'
        stage, original = manager._pak_locations('Game/goliath.pak')
        self.assertEqual(stage, self.sample)
        self.assertEqual(original, manager.game_root / 'Game' / 'goliath.pak')
        with patch('pak_manager_gui.messagebox.showinfo') as info:
            manager.inspect_browser_pak('Game/goliath.pak')
            self.assertIn('Goliath', info.call_args.args[0])
            self.assertIn(str(self.sample), info.call_args.args[1])
        with patch.object(manager, '_show_file_in_explorer') as reveal:
            manager.reveal_browser_pak('Game/goliath.pak', original=False)
            reveal.assert_called_once_with(self.sample)
            manager.reveal_browser_pak('Game/goliath.pak', original=True)
            self.assertEqual(reveal.call_args.args[0],
                             manager.game_root / 'Game' / 'goliath.pak')

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
