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
                self.assertEqual(root.attrib["width"], "24")
                self.assertEqual(root.attrib["height"], "24")
                self.assertEqual(root.attrib["viewBox"], "0 0 24 24")
                paths = list(root.iter("{http://www.w3.org/2000/svg}path"))
                self.assertEqual(len(paths), 1)
                self.assertGreater(len(paths[0].attrib["d"]), 40)
                self.assertEqual(paths[0].attrib["fill"], "#f5f3f6")

    def test_sidebar_flaticon_icons_are_centered_and_not_clipped(self):
        # Test BOTH the sidebar's normal size and narrow/high-DPI variants.
        # The earlier implementation only checked nonempty pixels: even a
        # small clipped corner could pass that assertion.
        for section in NAV_STEMS:
            for size in (18, 24, 30):
                with self.subTest(section=section, size=size):
                    picture = navigation_icon(section, "#ffffff", size).pixmap(size, size)
                    self.assertFalse(picture.isNull())
                    image = picture.toImage().convertToFormat(QImage.Format_RGBA8888)
                    pixels = [
                        (x, y) for y in range(image.height())
                        for x in range(image.width())
                        if image.pixelColor(x, y).alpha() >= 100
                    ]
                    self.assertGreater(len(pixels), size * size // 24, section)
                    left, right = min(x for x, y in pixels), max(x for x, y in pixels)
                    top, bottom = min(y for x, y in pixels), max(y for x, y in pixels)
                    self.assertGreater(left, 0, section)
                    self.assertGreater(top, 0, section)
                    self.assertLess(right, size - 1, section)
                    self.assertLess(bottom, size - 1, section)
                    self.assertGreater(right - left, size * 0.50, section)
                    self.assertGreater(bottom - top, size * 0.50, section)
                    self.assertLess(abs((left + right)/2 - (size - 1)/2), size * .16, section)
                    self.assertLess(abs((top + bottom)/2 - (size - 1)/2), size * .16, section)

    def test_semantic_glyphs_use_official_regular_rounded_codepoints(self):
        # Prevent a later build from silently switching back to the opaque
        # solid glyphs that rendered ambiguously at sidebar sizes.
        from build_flaticon_nav_icons import NAV_GLYPHS, UPSTREAM_PATH
        self.assertIn("regular-rounded.woff", UPSTREAM_PATH)
        self.assertEqual(NAV_GLYPHS, {
            "instructions": ("book-alt", 0xF274),
            "asset-library": ("archive", 0xF16D),
            "modding": ("tools", 0xFDCB),
            "play-restore": ("play", 0xFA9E),
            "settings": ("settings", 0xFBD2),
            "credits": ("users", 0xFEA6),
            "whats-new": ("newspaper", 0xF9A5),
            "developer": ("terminal", 0xFD78),
        })
        paths = [(NAV_ASSETS / (name + ".svg")).read_bytes()
                 for name in NAV_STEMS.values()]
        self.assertEqual(len(paths), len(set(paths)))

    def test_flaticon_attribution_is_bundled(self):
        root = Path(__file__).resolve().parent.parent
        info = root / "Docs" / "FLATICON-UICONS-ATTRIBUTION.txt"
        self.assertTrue(info.is_file())
        attribution = info.read_text(encoding="utf-8")
        self.assertIn("Uicons by Flaticon", attribution)
        self.assertIn("flaticon-uicons/blob/main/LICENSE", attribution)


if __name__ == "__main__":
    unittest.main()
