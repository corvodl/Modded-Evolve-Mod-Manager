"""Flaticon Uicons sidebar integration checks for Windows release builds."""
from __future__ import annotations
import os
from pathlib import Path
from xml.etree import ElementTree
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from qt_navigation_icons import NAV_ASSETS, NAV_STEMS, navigation_icon


class FlaticonNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_eight_official_flaticon_assets_generated(self):
        self.assertEqual(len(NAV_STEMS), 8)
        for section, stem in NAV_STEMS.items():
            with self.subTest(section=section):
                path = NAV_ASSETS / (stem + ".svg")
                self.assertTrue(path.is_file(), "Missing generated Flaticon glyph: " + str(path))
                root = ElementTree.parse(path).getroot()
                self.assertEqual(root.attrib["width"], "32")
                self.assertIn("viewBox", root.attrib)
                paths = list(root.iter("{http://www.w3.org/2000/svg}path"))
                self.assertEqual(len(paths), 1)
                self.assertGreater(len(paths[0].attrib["d"]), 40)
                self.assertEqual(paths[0].attrib["fill"], "#f5f3f6")

    def test_sidebar_flaticon_icons_have_visible_24px_pixels(self):
        for section in NAV_STEMS:
            with self.subTest(section=section):
                picture = navigation_icon(section, "#ffffff", 24).pixmap(24, 24)
                self.assertFalse(picture.isNull())
                image = picture.toImage().convertToFormat(QImage.Format_RGBA8888)
                alpha_pixels = [
                    (x, y) for y in range(image.height()) for x in range(image.width())
                    if image.pixelColor(x, y).alpha() >= 128
                ]
                self.assertGreater(len(alpha_pixels), 35, section)
                self.assertGreater(max(x for x, y in alpha_pixels) -
                                   min(x for x, y in alpha_pixels), 10, section)
                self.assertGreater(max(y for x, y in alpha_pixels) -
                                   min(y for x, y in alpha_pixels), 10, section)

    def test_flaticon_attribution_is_bundled(self):
        root = Path(__file__).resolve().parent.parent
        info = root / "Docs" / "FLATICON-UICONS-ATTRIBUTION.txt"
        self.assertTrue(info.is_file())
        attribution = info.read_text(encoding="utf-8")
        self.assertIn("Uicons by Flaticon", attribution)
        self.assertIn("flaticon-uicons/blob/main/LICENSE", attribution)


if __name__ == "__main__":
    unittest.main()
