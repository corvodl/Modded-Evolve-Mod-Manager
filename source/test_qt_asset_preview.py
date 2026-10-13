"""Offscreen end-to-end smoke test for the read-only imported-PAK viewer.

The fixture creates a real temporary Asset Library manifest and extracted
file. Both the Qt explorer and its read_asset() backend must resolve the
SAME library root. Mocking only the Qt explorer's archive folder would conceal
routing bugs and make the backend query the actual user's library.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QTreeWidgetItemIterator

from qt_asset_preview import QtAssetPreviewWindow


def same_directory(left: Path, right: Path) -> bool:
    """Compare filesystem identity across Windows long and 8.3 short paths."""
    return os.path.samefile(left, right)


class ReadOnlyAssetPreviewUITests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library_root = root / 'AssetLibrary'
        self.identifier = 'a' * 20
        self.folder = self.library_root / self.identifier
        self.relative = 'characters/hunter.xml'
        self.source = self.folder / 'files' / self.relative
        self.source.parent.mkdir(parents=True)
        self.source.write_text('<Character name="Maggie"/>', encoding='utf-8')
        # The viewer must group real virtual paths without parsing/changing
        # these synthetic binary mesh fixtures.
        self.models = [
            'characters/scrap/scrap_3p.skinm',
            'characters/scrap/scrap_3p_lod1.skinm',
            'characters/scrap/scrap_3p_lod2.skinm',
        ]
        for model in self.models:
            target = self.folder / 'files' / model
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'fixture')
        (self.folder / 'asset-library.json').write_text(json.dumps({
            'version': 1,
            'name': 'characters_hunters_maggie_data.pak',
            'entries': ([{
                'path': self.relative,
                'bytes': self.source.stat().st_size,
            }] + [{
                'path': relative,
                'bytes': (self.folder / 'files' / relative).stat().st_size,
            } for relative in self.models]),
        }), encoding='utf-8')

        # Patch the one backend source of truth, not archive lookups in just
        # one UI module; this exercises real manifest validation + read_asset.
        library_home = patch('asset_library_preview.LIBRARY_HOME',
                             self.library_root)
        library_home.start()
        self.addCleanup(library_home.stop)
        self.parent = QMainWindow()
        self.parent.settings = {}
        self.parent.persist = lambda: None
        self.parent.busy = False
        self.parent.stage_path = str(root / 'stage')
        self.parent.projects_path = str(root / 'projects')
        self.parent.game_root = None
        self.parent._find_game_root = lambda: None
        self.parent.open_pak_diff = lambda: None
        self.parent.open_level_editor = lambda *_: None
        self.parent.export_library_archive_by_id = lambda identifier: None
        self.addCleanup(self.parent.close)
        self.window = QtAssetPreviewWindow(self.parent, {
            'id': self.identifier, 'name': 'characters_hunters_maggie_data.pak',
        })
        self.addCleanup(self.window.close)

    def test_legacy_archive_preview_uses_unassigned_context(self):
        # A readable legacy manifest without build membership must still
        # support read-only preview, while never pulling foreign dependencies.
        from asset_library_preview import library_model_index
        from build_collections import resolve_archive_build
        self.assertEqual(resolve_archive_build(
            self.identifier, root=self.library_root), "unassigned")
        index = library_model_index(self.identifier, root=self.library_root)
        self.assertIn(self.models[0], index)
        self.assertEqual(index[self.models[0]][0]["_asset_library"], self.identifier)

    def test_text_preview_and_noneditable_controls(self):
        self.window.show()
        self.assertTrue(same_directory(self.window.folder, self.folder))
        iterator = QTreeWidgetItemIterator(self.window.tree)
        choice = None
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == self.relative:
                choice = item
                break
            iterator += 1
        self.assertIsNotNone(choice)
        self.window.tree.setCurrentItem(choice)
        self.assertIn('Character name', self.window.text.toPlainText())
        self.assertTrue(self.window.text.isReadOnly())
        self.assertEqual(self.source.read_text(encoding='utf-8'),
                         '<Character name="Maggie"/>')
        labels = ' '.join(x.text() for x in self.window.findChildren(QLabel))
        self.assertIn('previewing and exporting only', labels)
        self.assertNotIn('Save Changes', [
            x.text() for x in self.window.findChildren(QLabel)])

    def test_model_lods_share_a_grouped_explorer_family(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator = QTreeWidgetItemIterator(self.window.tree)
        found = {}
        while iterator.value():
            node = iterator.value()
            rel = node.data(0, Qt.UserRole)
            if rel in self.models:
                found[rel] = node
            iterator += 1
        self.assertEqual(set(found), set(self.models))
        primary = found[self.models[0]]
        for lod in self.models[1:]:
            self.assertIs(found[lod].parent(), primary)
        self.assertTrue(primary.text(0).casefold().find('model') >= 0)

    def test_export_folder_uses_current_selected_directory(self):
        folder_item = None
        iterator = QTreeWidgetItemIterator(self.window.file_tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole + 1) == 'characters/scrap':
                folder_item = item
                break
            iterator += 1
        self.assertIsNotNone(folder_item)
        self.window.file_tree.setCurrentItem(folder_item)
        self.assertEqual(
            self.window._current_model_folder_relative(), 'characters/scrap')
        self.assertTrue(same_directory(
            self.window._current_model_source_folder(),
            self.folder / 'files' / 'characters' / 'scrap'))
        output = Path(self.temp.name) / 'BlenderExports'
        output.mkdir()
        # Run the queued worker body in-process for this source-selection
        # test; thread lifecycle is exercised in test_beta15_export_progress.
        def run_inline(_parent, _title, _label, work, **callbacks):
            result = work(lambda *_: None, lambda: False)
            callbacks['on_success'](result)
        with patch('qt_asset_preview.QFileDialog.getExistingDirectory',
                   return_value=str(output)) as choose_destination, \
             patch('qt_background_exports.begin_file_operation',
                   side_effect=run_inline), \
             patch('qt_asset_preview.export_glb',
                   side_effect=lambda archive, rel, target: (target, {})) as exported:
            self.window.export_blender_folder()
        choose_destination.assert_called_once()
        self.assertEqual(exported.call_count, 3)
        self.assertEqual(
            {call.args[1] for call in exported.call_args_list}, set(self.models))

    def test_export_folder_uses_selected_model_parent_without_source_dialog(self):
        iterator = QTreeWidgetItemIterator(self.window.file_tree)
        model_item = None
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == self.models[0]:
                model_item = item
                break
            iterator += 1
        self.assertIsNotNone(model_item)
        self.window.file_tree.setCurrentItem(model_item)
        self.assertEqual(
            self.window._current_model_folder_relative(), 'characters/scrap')
        self.assertTrue(same_directory(
            self.window._current_model_source_folder(),
            self.folder / 'files' / 'characters' / 'scrap'))

    def test_same_modding_editor_model_tools_but_no_import_or_save(self):
        from qt_workspace_editor import QtWorkspaceEditor
        self.assertIsInstance(self.window, QtWorkspaceEditor)
        self.assertEqual(self.window.editor_split.objectName(),
                         'unifiedEditorSplitter')
        self.assertIs(self.window.file_tree, self.window.tree)
        self.assertTrue(self.window.read_only_library)
        self.assertTrue(self.window.actions['files']['Save'].isHidden())
        self.assertTrue(self.window.actions['files']['Import / Replace File'].isHidden())
        self.assertTrue(self.window.actions['images']['Import DDS'].isHidden())
        self.assertTrue(self.window.actions['images']['Import PNG'].isHidden())
        self.assertFalse(self.window.save_shortcut.isEnabled())
        model_names = [action.text() for action in
                       self.window.editor_menus['Model'].actions()
                       if action.isVisible()]
        self.assertIn('Open GPU Viewer in New Window', model_names)
        self.assertIn('Compare Models Side by Side…', model_names)
        self.assertNotIn('Import Same-Layout Model…', model_names)
        with self.assertRaises(PermissionError):
            self.window.import_dds()
        with self.assertRaises(PermissionError):
            self.window.import_png()
        with self.assertRaises(PermissionError):
            self.window.import_model()
        with self.assertRaises(PermissionError):
            self.window._import_and_reload(lambda _: None)
        self.window.save()
        self.assertEqual(self.source.read_text(encoding='utf-8'),
                         '<Character name="Maggie"/>')


if __name__ == '__main__':
    import unittest
    unittest.main()
