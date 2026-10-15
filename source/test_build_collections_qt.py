"""Offscreen Qt smoke coverage for sorting-safe library selections."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_whole_imported_legacy_build_export_action_is_wired(self):
        from build_collections import register_build, assign_archive
        source = register_build("Legacy source", "legacy", root=self.root)
        target = register_build("Stage 2 target", "stage2", root=self.root)
        assign_archive(source, "a" * 20, root=self.root)
        assign_archive(target, "b" * 20, root=self.root)
        controller = AssetLibraryController()
        dialog = AssetLibraryDialog(None, controller, self.root)
        try:
            idx = dialog.build_filter.findData(source)
            self.assertGreaterEqual(idx, 0)
            dialog.build_filter.setCurrentIndex(idx)
            self.assertIn("Export Legacy Build", dialog.bulk_transfer_button.text())
            self.assertTrue(dialog.bulk_transfer_button.isEnabled())
            self.assertTrue(dialog._historical_target.__call__)
            choices = iter((
                ("Stage 2 target [" + target[:8] + "]", True),
                ("All imported PAKs in this Legacy build", True),
                ("Compare entire selection (manifest only, no files)", True),
            ))
            with patch("qt_asset_library.QInputDialog.getItem",
                       side_effect=lambda *args, **kwargs: next(choices)), \
                 patch("qt_asset_library.QInputDialog.getText",
                       return_value=("", True)), \
                 patch("qt_asset_library.QFileDialog.getExistingDirectory",
                       return_value=str(self.root.parent)), \
                 patch("qt_asset_library.QMessageBox.question",
                       return_value=qt_asset_library.QMessageBox.Yes), \
                 patch.object(controller, "export_legacy_build",
                              return_value=True) as started:
                dialog.export_legacy_build()
            started.assert_called_once()
            args, kwargs = started.call_args
            self.assertEqual(args[:2], (source, target))
            self.assertIsNone(kwargs["source_archive"])
            self.assertTrue(kwargs["plan_only"])
            self.assertFalse(kwargs["include_identical"])
        finally:
            dialog.close()

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
