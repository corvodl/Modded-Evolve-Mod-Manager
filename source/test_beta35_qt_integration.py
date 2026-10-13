"""Qt-only smoke tests for beta.35 navigation, library and project surfaces."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTableWidget
from qt_manager import QtManager, SECTIONS


class Beta35QtIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from launch_integration import SwapStatus
        self.patches = [
            patch("qt_manager.settings_source",
                  return_value=Path(self.tmp.name) / "missing-settings.json"),
            patch("qt_manager.allowed_paks", return_value={}),
            patch("qt_manager.state_status",
                  return_value=SwapStatus("missing", "No synthetic installed game")),
            patch("qt_manager.save_settings", return_value=None),
            patch("qt_manager.QtManager.refresh_project_list",
                  autospec=True, return_value=None),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.window = QtManager()
        self.addCleanup(self.window.close)
        self.window.show()
        self.app.processEvents()

    def test_project_table_is_primary_and_legacy_browser_is_advanced(self):
        self.assertIsInstance(self.window.project_table, QTableWidget)
        self.assertEqual(self.window.project_table.columnCount(), 6)
        self.window.open_page(SECTIONS.index("Modding"))
        self.app.processEvents()
        self.assertTrue(self.window.project_table.isVisible())
        self.assertFalse(self.window.legacy_archive_panel.isVisible())
        self.window.toggle_advanced_archive_tools()
        self.assertTrue(self.window.legacy_archive_panel.isVisible())

    def test_explicit_by_build_view_does_not_drop_archive_row_id(self):
        self.assertEqual(self.window.asset_view_tabs.tabText(0), "All Assets")
        self.assertEqual(self.window.asset_view_tabs.tabText(1), "By Build")
        self.window.open_page(SECTIONS.index("Asset Library"))
        self.window.asset_view_tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(self.window.asset_build_filter.isVisible())
        self.assertTrue(self.window.asset_table.isSortingEnabled())

    def test_settings_update_action_stays_first(self):
        self.window.open_page(SECTIONS.index("Settings"))
        self.app.processEvents()
        self.assertEqual(self.window.settings_sections[0], "UPDATES")
        self.assertTrue(self.window.update_button.isVisible())
        self.assertEqual(self.window.update_button.text(), "Check for Updates")


if __name__ == "__main__":
    unittest.main()
