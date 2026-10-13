"""Offscreen Qt smoke coverage for sorting-safe library selections."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import asset_library
import qt_asset_library
from qt_asset_library import AssetLibraryDialog, AssetLibraryController
from test_build_collections import fixture


class BuildLibraryQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = asset_library.LIBRARY_HOME
        self.old_ui = qt_asset_library.LIBRARY_HOME
        asset_library.LIBRARY_HOME = self.root
        qt_asset_library.LIBRARY_HOME = self.root
        fixture(self.root, "a" * 20, b"old", "alpha.pak")
        fixture(self.root, "b" * 20, b"new", "zeta.pak")
        asset_library._invalidate()

    def tearDown(self):
        asset_library.LIBRARY_HOME = self.old
        qt_asset_library.LIBRARY_HOME = self.old_ui
        asset_library._invalidate()
        self.tmp.cleanup()

    def test_sort_actions_follow_identifier(self):
        controller = AssetLibraryController()
        dialog = AssetLibraryDialog(None, controller, self.root)
        try:
            dialog.table.sortItems(0, Qt.DescendingOrder)
            dialog.table.setCurrentCell(0, 0)
            selected = dialog._selected_record()
            self.assertEqual(selected["name"], "zeta.pak")
            self.assertEqual(selected["id"], "b" * 20)
            dialog.table.sortItems(0, Qt.AscendingOrder)
            dialog.table.setCurrentCell(0, 0)
            self.assertEqual(dialog._selected_record()["id"], "a" * 20)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
