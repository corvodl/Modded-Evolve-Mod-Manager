"""Regression coverage for offline vector icon assignment and the FFDec notice."""
import os
import unittest
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon

from qt_navigation_icons import navigation_icon, action_icon, PATHS
from qt_ffdec_prompt import FFDEC_DOWNLOAD, ffdec_is_ready, show_ffdec_requirement


class UiIconFfdecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_feature_icons_render_offline(self):
        for key in ("folder", "file", "text", "texture", "model", "gfx",
                    "search", "favorite", "save", "import", "export", "edit",
                    "refresh", "fit", "view", "material", "normals",
                    "grid", "wire", "solid", "animation", "play", "pause"):
            self.assertIn(key, PATHS)
            icon = navigation_icon(key)
            self.assertIsInstance(icon, QIcon)
            self.assertFalse(icon.isNull(), key)
            self.assertFalse(icon.pixmap(18, 18).isNull(), key)

    def test_action_icons(self):
        for name in ("Export PNG", "Import DDS", "Import PNG", "Find Text",
                     "Edit full GFX", "Reload FFDec changes", "Save",
                     "Open Folder", "Play"):
            self.assertFalse(action_icon(name).isNull(), name)

    def test_missing_ffdec_is_detected(self):
        manager = type("Manager", (), {"settings": {}})()
        with patch("qt_ffdec_prompt.ffdec_command", side_effect=ValueError("missing")):
            self.assertFalse(ffdec_is_ready(manager))
        with patch("qt_ffdec_prompt.ffdec_command", return_value=("ffdec-cli.exe",)):
            self.assertTrue(ffdec_is_ready(manager))

    def test_download_notice_contains_official_page(self):
        self.assertEqual(FFDEC_DOWNLOAD,
                         "https://github.com/jindrapetrik/jpexs-decompiler/releases")
        captured = {}
        def fake_exec(dialog):
            captured["title"] = dialog.windowTitle()
            captured["text"] = dialog.text()
            captured["detail"] = dialog.informativeText()
            captured["buttons"] = [b.text() for b in dialog.buttons()]
            return QMessageBox.Rejected
        with patch.object(QMessageBox, "exec", fake_exec):
            show_ffdec_requirement(None)
        self.assertIn("FFDec", captured["title"])
        self.assertIn("needed", captured["text"])
        self.assertIn(FFDEC_DOWNLOAD, captured["detail"])
        self.assertTrue(any("download" in text.lower() for text in captured["buttons"]))


if __name__ == "__main__":
    unittest.main()
