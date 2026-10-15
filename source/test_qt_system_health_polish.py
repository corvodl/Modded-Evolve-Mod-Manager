"""Qt grouping, minimal health controls, and recovery-navigation regression."""
from __future__ import annotations
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton, QDialog, QPlainTextEdit
from launch_integration import SwapStatus
from qt_manager import QtManager, SECTIONS
from qt_system_health import SystemHealthPanel


class ProjectGroupedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        patches = [
            patch("qt_manager.settings_source", return_value=root / "settings.json"),
            patch("qt_manager.allowed_paks", return_value={}),
            patch("qt_manager.save_settings", return_value=None),
            patch("qt_manager.state_status", return_value=SwapStatus(
                "missing", "Not prepared"))]
        for ctx in patches:
            ctx.start()
            self.addCleanup(ctx.stop)
        self.win = QtManager()
        self.addCleanup(self.win.close)

    def test_group_expand_filter_and_exact_workspace_identity(self):
        root = Path(self.temp.name)
        paths = [root / name for name in ("Real-One", "Real-Two", "Real-Three")]
        for path in paths:
            path.mkdir()
        rows = (
            dict(workspace=str(paths[0]), label="Same Label", target_archive="A.pak",
                 changed=1, source_build_id="beta", status="modified", details=""),
            dict(workspace=str(paths[1]), label="Same Label", target_archive="A.pak",
                 changed=0, source_build_id="beta", status="draft", details=""),
            dict(workspace=str(paths[2]), label="Different", target_archive="Z.pak",
                 changed=0, source_build_id=None, status="draft", details=""),
        )
        self.win.current_workspace = str(paths[0])
        self.win.refresh_project_list(rows=rows, skip_changes=True)
        table = self.win.project_table
        self.assertEqual(table.rowCount(), 5)  # 2 headers, 3 editable projects
        self.assertIn("2 project(s)", table.item(0, 0).text())
        self.assertEqual(table.item(1, 0).text(), "Real-One")
        self.assertEqual(table.item(1, 0).data(0x0100), str(paths[0]))
        self.assertIn("Edited", table.item(1, 5).text())
        self.win._project_row_clicked(0, 0)
        self.assertEqual(table.rowCount(), 3)  # collapsed A, header Z, one Z project
        self.win._project_row_clicked(0, 0)
        self.assertEqual(table.rowCount(), 5)
        self.win.project_filter.setText("Real-Two")
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.item(1, 0).text(), "Real-Two")
        self.assertEqual(table.item(1, 0).data(0x0100), str(paths[1]))

    def test_group_heading_cannot_open_an_arbitrary_project(self):
        path = Path(self.temp.name) / "One"
        path.mkdir()
        self.win.refresh_project_list(rows=(
            dict(workspace=str(path), label="one", target_archive="A.pak",
                 changed=0, source_build_id=None, status="draft", details=""),
        ), skip_changes=True)
        self.win.project_table.clearSelection()
        self.win.project_table.setCurrentCell(0, 0)
        self.assertIsNone(self.win.selected_project_context())


class SystemHealthPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_recovery_only_for_unsafe_swap_and_navigation_only(self):
        manager = MagicMock()
        manager.pages = {"settings": MagicMock()}
        manager.pages["settings"].findChildren.return_value = []
        panel = SystemHealthPanel(manager)
        values = {key: ("Ready", "fine") for key, *_ in panel.LABELS}
        panel._finished("snapshot", values, "")
        self.assertTrue(panel.recovery_button.isHidden())
        values["storage"] = ("Attention needed", "Swap journal: unsafe")
        panel._finished("snapshot", values, "")
        self.assertFalse(panel.recovery_button.isHidden())
        panel._open_recovery()
        manager.open_page.assert_called_once_with(SECTIONS.index("Settings"))
        panel.close()

    def test_support_report_requires_editable_preview(self):
        manager = MagicMock()
        manager.game_root = ""
        manager.settings = {}
        manager._find_game_root.return_value = ""
        manager.swap_path = "/empty"
        manager.projects_path = "/projects"
        manager.stage_path = "/stage"
        panel = SystemHealthPanel(manager)
        panel._last = {"firewall": ("Offline", "C:\\Users\\Private\\secrets.txt")}
        with patch("qt_system_health.QDialog.exec", return_value=0), \
                patch("installed_game_versions.installed_versions", return_value={
                    "client": "0.1.54", "game": "3.6"}):
            panel._report()
        dialog = panel.findChild(QDialog, "systemHealthPreview")
        self.assertIsNotNone(dialog)
        editor = dialog.findChild(QPlainTextEdit, "systemHealthPreviewText")
        self.assertTrue(editor.isReadOnly() is False)
        self.assertIn("Firewall: Offline", editor.toPlainText())
        self.assertNotIn("Private", editor.toPlainText())
        panel.close()


if __name__ == "__main__":
    unittest.main()
