"""Offscreen smoke test for the new read-only extracted-PAK file browser."""
from __future__ import annotations

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
        self.folder = root / ('a' * 20)
        source = self.folder / 'files' / 'characters' / 'hunter.xml'
        source.parent.mkdir(parents=True)
        source.write_text('<Character name="Maggie"/>', encoding='utf-8')
        self.source = source
        self.parent = QMainWindow()
        self.parent.export_library_archive_by_id = lambda identifier: None
        self.addCleanup(self.parent.close)
        archives = {'characters/hunter.xml': source}
        self.patches = [
            patch('qt_asset_preview.archive_folder', return_value=self.folder),
            patch('qt_asset_preview.archive_entries', return_value=archives)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.window = QtAssetPreviewWindow(self.parent, {
            'id': 'a' * 20, 'name': 'characters_hunters_maggie_data.pak',
        })
        self.addCleanup(self.window.close)

    def test_text_preview_and_noneditable_controls(self):
        self.window.show()
        iterator = QTreeWidgetItemIterator(self.window.tree)
        choice = None
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == 'characters/hunter.xml':
                choice = item
                break
            iterator += 1
        self.assertIsNotNone(choice)
        self.window.tree.setCurrentItem(choice)
        self.assertIn('Character name', self.window.text.toPlainText())
        self.assertTrue(self.window.text.isReadOnly())
        self.assertEqual(self.source.read_text(), '<Character name="Maggie"/>')
        labels = ' '.join(x.text() for x in self.window.findChildren(QLabel))
        self.assertIn('previewing and exporting only', labels)
        self.assertNotIn('Save Changes', [
            x.text() for x in self.window.findChildren(QLabel)])


if __name__ == '__main__':
    import unittest
    unittest.main()
