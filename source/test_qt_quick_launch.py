"""Qt quick-launch navigation regression checks, without live game access."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from qt_manager import QtManager, SECTIONS
from qt_navigation_icons import navigation_icon


class QuickLaunchQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from launch_integration import SwapStatus
        fixtures = [
            patch("qt_manager.settings_source",
                  return_value=Path(self.temp.name) / "no-settings.json"),
            patch("qt_manager.allowed_paks", return_value={}),
            patch("qt_manager.state_status",
                  return_value=SwapStatus("missing", "Synthetic game unavailable")),
            patch("qt_manager.save_settings", return_value=None),
        ]
        for item in fixtures:
            item.start()
            self.addCleanup(item.stop)
        self.win = QtManager()
        self.addCleanup(self.win.close)
        self.win.show()
        self.application.processEvents()

    def test_no_old_play_page_and_bottom_rail_has_three_controls(self):
        self.assertNotIn("Play & Restore", SECTIONS)
        self.assertNotIn("play", self.win.pages)
        self.assertEqual(self.win.stack.count(), len(SECTIONS))
        rail = self.win.nav_rail.layout()
        bottom = [rail.itemAt(rail.count() - count).widget()
                  for count in (3, 2, 1)]
        self.assertEqual([button.accessibleName() for button in bottom], [
            "Play with Mods", "Play without Mods", "Settings"])
        self.assertIs(bottom[-1], self.win.rail_buttons[SECTIONS.index("Settings")])
        for button in bottom:
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.toolTip(), button.accessibleName())

    def test_home_play_and_client_status_without_old_navigation_tile(self):
        from PySide6.QtWidgets import QPushButton
        home = self.win.home_dashboard
        self.assertNotIn("Play & Restore", home.tiles)
        texts = {button.text() for button in home.findChildren(QPushButton)}
        self.assertIn("Play with Mods", texts)
        self.assertIn("Play without Mods", texts)
        self.assertTrue(home.client_version_label.text())
        self.assertFalse(hasattr(home, "client_update_status"))
        self.assertFalse(navigation_icon("Play with Mods").isNull())
        self.assertFalse(navigation_icon("Play without Mods").isNull())

    def test_settings_remains_accessible_and_restoration_is_available(self):
        from PySide6.QtWidgets import QPushButton
        self.win.rail_buttons[SECTIONS.index("Settings")].click()
        self.application.processEvents()
        self.assertEqual(self.win.stack.currentIndex(), SECTIONS.index("Settings"))
        labels = {button.text() for button in
                  self.win.pages["settings"].findChildren(QPushButton)}
        self.assertIn("Restore Game Files", labels)


if __name__ == "__main__":
    unittest.main()
