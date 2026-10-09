"""DDS .0 standalone detection and nonmodal file-selection regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from dds_streaming import inspect_whole_part0
from dds_texture import parse_dds, replace_dds
from dds_png_import import encode_png_as_dds
from test_editor_tabs_png import fixture_dds
from test_asset_streaming_models import split_fixture
from workspace_editor import WorkspaceEditor


class WholeDDS0Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / 'work'
        self.folder = self.work / 'files'
        self.folder.mkdir(parents=True)
        self.raw = fixture_dds('DXT5', (16, 16), 1)
        self.source = self.folder / 'effect.dds.0'
        self.source.write_bytes(self.raw)

    def test_standalone_is_complete_without_part_1(self):
        self.assertEqual(inspect_whole_part0(self.work, 'effect.dds.0'), self.raw)
        self.assertEqual(parse_dds(self.raw).mipmaps, 1)

    def test_png_edit_roundtrip_with_preserved_filename_and_backup(self):
        png = Path(self.temp.name) / 'edited.png'
        Image.new('RGBA', (16, 16), (18, 52, 95, 255)).save(png)
        rep = encode_png_as_dds(self.raw, png)
        candidate = Path(self.temp.name) / 'edited.dds'
        candidate.write_bytes(rep)
        replace_dds(self.work, 'effect.dds.0', candidate, hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.source.read_bytes(), rep)
        backups = list((self.work / 'EditorBackups').rglob('effect.dds.0'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.raw)

    def test_part_0_with_sibling_is_not_treated_as_standalone(self):
        (self.folder / 'effect.dds.1').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'other streaming fragments'):
            inspect_whole_part0(self.work, 'effect.dds.0')
        candidate = Path(self.temp.name) / 'edited.dds'
        candidate.write_bytes(self.raw)
        with self.assertRaises(ValueError):
            replace_dds(self.work, 'effect.dds.0', candidate, hashlib.sha256(self.raw).hexdigest())

    def test_truncated_or_mip_only_part_not_misidentified(self):
        self.source.write_bytes(self.raw[:-16])
        with self.assertRaises(ValueError):
            inspect_whole_part0(self.work, 'effect.dds.0')
        self.source.unlink()
        split_fixture(self.folder, 'effect.dds')
        for path in self.folder.glob('effect.dds.[1-9]*'):
            path.unlink()
        with self.assertRaises(ValueError):
            inspect_whole_part0(self.work, 'effect.dds.0')

    def test_orphan_fragment_is_visible_in_images_list(self):
        self.assertEqual(WorkspaceEditor.visible_entries(['orphan.dds.4', 'orphan.dds.7'], 'images'), ['orphan.dds.4'])
        self.assertEqual(WorkspaceEditor.visible_entries(['effect.dds.0', 'effect.dds.2'], 'images'), ['effect.dds.0'])
        self.assertEqual(WorkspaceEditor.visible_entries(['huge.dds.9999999999'], 'images'), ['huge.dds.9999999999'])

    def test_tk_selection_does_not_raise_modal_popup(self):
        import tkinter as tk
        from dark_theme import apply_theme
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest('Graphical Tk display unavailable: ' + str(error))
        root.withdraw()
        editor = None
        try:
            self.folder.joinpath('broken.dds.0').write_bytes(fixture_dds('DXT5', (16, 16), 3)[:128+16])
            manifest = {'entries':[{'path': name,'format':'raw'} for name in ('effect.dds.0','broken.dds.0')]}
            (self.work / '.evolve-pak-workspace.json').write_text(json.dumps(manifest))
            apply_theme(root)
            manager = SimpleNamespace(window=root,busy=False,t=lambda name:name)
            with patch('workspace_editor.messagebox.showerror') as show_error:
                editor = WorkspaceEditor(manager, self.work)
                editor.tabs.select(editor.tab_frames['images'])
                root.update()
                tree = editor.trees['images']
                tree.selection_set(editor.path_items['images']['effect.dds.0'])
                root.update()
                self.assertEqual(editor.view_mode, 'dds0_whole')
                self.assertFalse(editor.export_button.instate(['disabled']))
                self.assertFalse(editor.import_button.instate(['disabled']))
                self.assertFalse(editor.import_png_button.instate(['disabled']))
                tree.selection_set(editor.path_items['images']['broken.dds.0'])
                root.update()
                self.assertEqual(editor.view_mode, 'unavailable')
                self.assertIn('full split DDS set', editor.image_label.cget('text'))
                self.assertTrue(editor.import_button.instate(['disabled']))
                show_error.assert_not_called()
        finally:
            if editor:
                editor.window.destroy()
            root.destroy()


if __name__ == '__main__':
    unittest.main()
