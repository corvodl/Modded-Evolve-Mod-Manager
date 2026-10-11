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
        (self.folder / 'asset-library.json').write_text(json.dumps({
            'version': 1,
            'name': 'characters_hunters_maggie_data.pak',
            'entries': [{
                'path': self.relative,
                'bytes': self.source.stat().st_size,
            }],
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

    def test_text_preview_and_noneditable_controls(self):
        self.window.show()
        self.assertEqual(self.window.folder, self.folder)
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
