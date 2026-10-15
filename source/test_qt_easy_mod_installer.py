"""Qt smoke tests for unobtrusive installer and searchable Ctrl+K commands."""
from __future__ import annotations
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget
from qt_easy_mod_installer import EasyModInstaller
from qt_quick_commands import QuickCommandPalette


class _Manager(QWidget):
    def __init__(self):
        super().__init__()
        self.stage_path = "/unused-stage"
        self.swap_path = "/unused-swap"
        self.open_mod_profiles = MagicMock()
        self.open_easy_mod_installer = MagicMock()
        self.undo_mod_profile = MagicMock()
        self.open_page = MagicMock()
        self.section_index = lambda name: {"Home": 0, "Modding": 3,
                                           "Asset Library": 2, "Settings": 4}[name]
        self.open_project_dashboard = MagicMock()
        self.check_updates = MagicMock()
        self.restore = MagicMock()
        self.toggle_activity = MagicMock()


class EasyModQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = _Manager()
        self.addCleanup(self.manager.close)

    def test_installer_starts_unverified_and_accepts_drop_path_without_staging(self):
        dialog = EasyModInstaller(self.manager, Path(self.temp.name) / "Profiles")
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.install_button.isEnabled())
        self.assertFalse(dialog.review is not None)
        package = Path(self.temp.name) / "download.zip"
        package.write_bytes(b"synthetic fixture")
        dialog.choose(package)
        self.assertEqual(dialog.selected_label.text(), "download.zip")
        self.assertFalse(dialog.install_button.isEnabled())
        self.manager.open_mod_profiles.assert_not_called()

    def test_installer_review_summary_is_enabled_only_after_signed_check(self):
        dialog = EasyModInstaller(self.manager, Path(self.temp.name) / "Profiles")
        self.addCleanup(dialog.close)
        from easy_mod_install import ModReview
        file = Path(self.temp.name) / "libs.pak"
        file.write_bytes(b"test")
        fake_review = ModReview(file, "Game/libs.pak", "libs.pak", 4,
                                "a" * 64, ("Game/test.xml",))
        with patch("qt_easy_mod_installer.begin_file_operation") as work:
            dialog.choose(file)
            dialog.inspect()
            self.assertFalse(dialog.install_button.isEnabled())
            self.assertTrue(work.called)
            work.call_args.kwargs["on_success"](fake_review)
            self.assertTrue(dialog.install_button.isEnabled())
            self.assertIn("Game/libs.pak", dialog.summary.text())

    def test_ctrl_k_palette_only_public_commands_and_search(self):
        palette = QuickCommandPalette(self.manager)
        self.addCleanup(palette.close)
        self.assertEqual(palette.objectName(), "managerQuickCommands")
        names = [label for label, _ in palette.commands]
        self.assertIn("Install Mod…", names)
        self.assertIn("Undo Profile Staging…", names)
        self.assertNotIn("Developer", " ".join(names))
        self.assertNotIn("Allow online", " ".join(names))
        palette.search.setText("install")
        self.assertEqual(palette.results.count(), 2)  # installer and Installed Mods
        self.assertEqual(palette.results.item(0).text(), "Install Mod…")
        palette.invoke()
        self.manager.open_easy_mod_installer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
