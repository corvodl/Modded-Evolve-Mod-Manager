"""Offscreen Qt smoke tests for compact Game Storage settings."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget
from qt_game_storage import GameStorageSettings, StorageReview
from game_storage import CATEGORIES


class GameStorageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        manager = QWidget()
        manager.game_storage_root = str(root / "GameSets")
        manager.stage_path = str(root / "GameSets" / "Setup_One" / "staged")
        manager.swap_path = str(root / "GameSets" / "Setup_One" / "swap")
        manager.projects_path = str(root / "Projects")
        manager.game_root = None
        manager._find_game_root = lambda: ""
        manager.browse_folder = MagicMock()
        self.manager = manager
        self.addCleanup(manager.close)
        fields = {name: QLineEdit() for name in ("stage", "swap", "projects")}
        self.panel = GameStorageSettings(manager, fields)
        self.addCleanup(self.panel.close)

    def test_one_visible_root_and_advanced_paths_collapsed(self):
        self.assertEqual(self.panel.objectName(), "gameStorageSettings")
        self.assertIn("GameSets", self.panel.root_input.text())
        self.assertFalse(self.panel.advanced.toggle.isChecked())
        self.assertFalse(self.panel._scanned)
        self.assertEqual(set(self.panel.legend), set(CATEGORIES))
        self.assertIn("Setup_One", self.panel.active_label.text())

    def test_real_storage_bytes_populate_colored_bar_and_legend(self):
        amounts = {"Active setup": 500, "Old setups": 300,
                   "Projects": 100, "Asset Library": 100, "Other": 0}
        with patch.object(self.panel, "refresh") as refresher:
            self.panel._complete("scan", {"bytes": amounts, "total": 1000}, "")
            refresher.assert_not_called()
        self.assertTrue(self.panel._scanned)
        self.assertIn("500", self.panel.legend["Active setup"].text())
        self.assertIn("300", self.panel.legend["Old setups"].text())
        self.assertGreater(
            self.panel.bar_layout.stretch(self.panel.bar_layout.indexOf(
                self.panel.bars["Active setup"])),
            self.panel.bar_layout.stretch(self.panel.bar_layout.indexOf(
                self.panel.bars["Projects"])))

    def test_review_disables_unsafe_cleanup_rows(self):
        root = Path(self.temp.name)
        dialog = StorageReview(self.panel, (
            (root / "Setup_active", 100, "Active signing or swap setup"),
            (root / "Setup_old", 100, None)),
            lambda paths: None)
        self.addCleanup(dialog.close)
        first = dialog.table.item(0, 0)
        second = dialog.table.item(1, 0)
        from PySide6.QtCore import Qt
        self.assertFalse(first.flags() & Qt.ItemIsUserCheckable)
        self.assertTrue(second.flags() & Qt.ItemIsUserCheckable)

    def test_analysis_is_on_demand_and_off_gui_thread(self):
        with patch.object(self.panel, "_thread_pool") as pool, \
                patch("qt_game_storage.measure_storage") as analyze:
            self.panel.refresh()
            pool.start.assert_called_once()
            analyze.assert_not_called()
            self.assertFalse(self.panel.scan_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
