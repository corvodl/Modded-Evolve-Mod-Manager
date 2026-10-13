"""Navigation/Settings regression checks for the shared Qt GUI update."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton, QLabel
from qt_manager import QtManager, SECTIONS
from qt_navigation_icons import navigation_icon


class ModernQtNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from launch_integration import SwapStatus
        self.patchers = [
            patch("qt_manager.settings_source",
                  return_value=Path(self.tmp.name) / "missing-settings.json"),
            patch("qt_manager.allowed_paks", return_value={}),
            patch("qt_manager.state_status",
                  return_value=SwapStatus("missing", "Synthetic unavailable game")),
            patch("qt_manager.save_settings", return_value=None),
        ]
        for entry in self.patchers:
            entry.start()
            self.addCleanup(entry.stop)
        self.win = QtManager()
        self.addCleanup(self.win.close)
        self.win.show()
        self.app.processEvents()

    def test_picture_icons_accessibility_and_navigation(self):
        for label in SECTIONS[:-1]:
            button = self.win.nav_buttons[SECTIONS.index(label)]
            self.assertFalse(button.icon().isNull(), label)
            self.assertFalse(button.icon().pixmap(24, 24).isNull(), label)
            self.assertEqual(button.toolTip(), label)
            self.assertEqual(button.accessibleName(), label)
            button.click()
            self.app.processEvents()
            self.assertEqual(self.win.stack.currentIndex(), SECTIONS.index(label))
        self.assertTrue(navigation_icon("Settings").availableSizes())

    def test_sidebar_icons_have_unclipped_bounds_at_narrow_width(self):
        # Exercise the actual sidebar buttons, not just isolated SVG assets.
        self.win.menu_button.click()
        self.win.menu_animation.setCurrentTime(205)
        self.app.processEvents()
        for label in SECTIONS[:-1]:
            button = self.win.nav_buttons[SECTIONS.index(label)]
            region = button.icon_bounds()
            self.assertGreaterEqual(region.left(), 8, label)
            self.assertGreaterEqual(region.top(), 6, label)
            self.assertLessEqual(region.right(), button.width() - 8, label)
            self.assertLessEqual(region.bottom(), button.height() - 6, label)
            self.assertFalse(button.icon().isNull(), label)

    def test_sidebar_icon_renders_with_visible_pixels_and_scaled_asset(self):
        # QWidget.render(QPainter) into a synthetic 2x QImage causes an
        # offscreen Qt paint-device error on Windows. Capture the real button
        # instead, and test the bundled icon's 2x raster independently.
        from PySide6.QtGui import QImage
        self.win.menu_button.click()
        self.win.menu_animation.setCurrentTime(205)
        self.app.processEvents()
        button = self.win.nav_buttons[0]
        bounds = button.icon_bounds()
        screenshot = button.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        self.assertFalse(screenshot.isNull())
        self.assertGreaterEqual(screenshot.width(), bounds.right() + 1)
        self.assertGreaterEqual(screenshot.height(), bounds.bottom() + 1)
        # Icon strokes are light; the dark/red navigation background is not.
        bright = sum(
            1 for y in range(bounds.top(), bounds.bottom() + 1)
            for x in range(bounds.left(), bounds.right() + 1)
            if all(channel > 145 for channel in (
                screenshot.pixelColor(x, y).red(),
                screenshot.pixelColor(x, y).green(),
                screenshot.pixelColor(x, y).blue()
            ))
        )
        self.assertGreater(bright, 5, 'Navigation SVG did not render in its bounds')
        large = navigation_icon('Instructions', '#ffffff', 40).pixmap(40, 40)
        self.assertFalse(large.isNull())
        self.assertGreaterEqual(large.width(), 40)

    def test_update_first_section_and_one_real_action(self):
        settings = self.win.pages["settings"]
        top = settings.widget().layout()
        # Title, concise intro, UPDATES heading, description, version card.
        labels = [top.itemAt(i).widget() for i in range(min(top.count(), 6))
                  if isinstance(top.itemAt(i).widget(), QLabel)]
        self.assertEqual(labels[0].text(), "Settings")
        self.assertEqual(labels[2].text(), "UPDATES")
        self.assertEqual(self.win.settings_sections[0], "UPDATES")
        matching = [button for button in settings.findChildren(QPushButton)
                    if button.text() == "Check for Updates"]
        self.assertEqual(matching, [self.win.update_button])
        self.win.open_page(SECTIONS.index("Settings"))
        self.app.processEvents()
        self.assertTrue(self.win.update_button.isVisible())
        # The button keeps a connection to the original bound method.
        # When busy, that method shows a modal information dialog; mock only
        # the dialog, not check_updates, so the actual guard is exercised.
        with patch.object(self.win, "info") as info:
            self.win.busy = True
            try:
                self.win.update_button.click()
                info.assert_called_once_with(
                    "Updates", "Finish the current operation before updating.")
                self.assertFalse(self.win.update_running)
                self.assertTrue(self.win.update_button.isEnabled())
            finally:
                self.win.busy = False

    def test_icon_offline_themes(self):
        for tone in ("#ffffff", "#202129"):
            for section in SECTIONS[:-1]:
                self.assertFalse(navigation_icon(section, tone, 24).isNull())


if __name__ == "__main__":
    unittest.main()
