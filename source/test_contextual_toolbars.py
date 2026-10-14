"""Context-sensitive toolbar and no-clipped-actions regressions for Qt editors."""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QTableWidgetItem
from qt_workspace_editor import QtWorkspaceEditor
from qt_manager import QtManager


class ContextualToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def test_editor_quick_commands_are_contextual_and_menus_retain_tools(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        folder = root / "files"
        folder.mkdir()
        (folder/"test.xml").write_text("<root />", encoding="utf-8")
        (root/".evolve-pak-workspace.json").write_text(
            json.dumps({"entries":[{"path":"test.xml", "format":"text"}]}))
        manager = QMainWindow()
        manager.settings = {}
        manager.busy = False
        manager.projects_path = root
        manager.stage_path = root
        manager.current_workspace = str(root)
        manager.persist = lambda: None
        manager.open_pak_diff = lambda: None
        editor = QtWorkspaceEditor(manager, root)
        self.addCleanup(editor.close)
        self.addCleanup(manager.close)
        editor.resize(660, 460)
        editor.show()
        self.app.processEvents()
        self.assertTrue(editor.actions["files"]["Import / Replace File"].isHidden())
        self.assertTrue(editor.actions["files"]["Restore Original"].isHidden())
        self.assertTrue(editor.actions["images"]["Import DDS"].isHidden())
        self.assertTrue(editor.actions["images"]["Import PNG"].isHidden())
        self.assertTrue(editor.actions["files"]["Save"].isHidden())
        menus = editor.editor_menus
        self.assertIn("Import Replacement DDS…",
            [action.text() for action in menus["Textures"].actions()])
        self.assertIn("Import / Replace File…",
            [action.text() for action in menus["Edit"].actions()])
        editor.load("test.xml")
        self.app.processEvents()
        self.assertFalse(editor.actions["files"]["Find Text"].isHidden())
        self.assertTrue(editor.actions["files"]["Save"].isHidden())
        editor.views["files"].setPlainText("<changed />")
        self.app.processEvents()
        self.assertFalse(editor.actions["files"]["Save"].isHidden())
        self.assertTrue(editor.actions["files"]["Save"].isEnabled())
        # The visible toolbar consists of only two short, contextual actions.
        self.assertLessEqual(sum(
            1 for b in editor.actions["files"].values() if b.isVisible()), 2)
        # Clear synthetic unsaved edits before close cleanup; otherwise the
        # real editor correctly opens a modal confirmation that blocks CI.
        editor.views["files"].setPlainText(editor.loaded_text)
        self.app.processEvents()

    def test_asset_library_quick_buttons_follow_archive_selection(self):
        from unittest.mock import patch
        from launch_integration import SwapStatus
        with TemporaryDirectory() as tmp, \
             patch("qt_manager.settings_source",
                   return_value=Path(tmp)/"settings-missing.json"), \
             patch("qt_manager.allowed_paks", return_value={}), \
             patch("qt_manager.state_status",
                   return_value=SwapStatus("missing", "Synthetic game unavailable")), \
             patch("qt_manager.save_settings", return_value=None):
            manager = QtManager()
            self.addCleanup(manager.close)
            buttons = manager.asset_quick
            self.assertTrue(buttons["Preview Files"].isHidden())
            self.assertTrue(buttons["Edit / Add to Mod Project"].isHidden())
            self.assertTrue(buttons["Open Map"].isHidden())
            self.assertTrue(buttons["Open Asset Library Folder"].isHidden())
            manager._library_rows = {
                "legacy-test": {"id": "legacy-test", "name": "textures.pak",
                                "source": r"C:\\Temp\\textures.pak"}}
            table = manager.asset_table
            table.insertRow(0)
            first = QTableWidgetItem("Textures")
            first.setData(Qt.UserRole, "legacy-test")
            table.setItem(0, 0, first)
            table.selectRow(0)
            manager._sync_library_quick_actions()
            self.assertFalse(buttons["Preview Files"].isHidden())
            self.assertFalse(buttons["Edit / Add to Mod Project"].isHidden())
            self.assertTrue(buttons["Open Map"].isHidden())
            manager._library_rows["legacy-test"]["name"] = "levels_canyon.pak"
            manager._sync_library_quick_actions()
            # A level-looking name alone is not proof of map ownership.
            self.assertTrue(buttons["Open Map"].isHidden())
            manager._library_rows["legacy-test"]["source"] = (
                r"C:\\Games\\ModdedEvolve\\EvolveGame\\Game\\levels_canyon.pak")
            manager._sync_library_quick_actions()
            self.assertFalse(buttons["Open Map"].isHidden())
            manager._library_rows["legacy-test"]["name"] = "level.pak"
            manager._library_rows["legacy-test"]["source"] = (
                r"C:\\Games\\ModdedEvolve\\EvolveGame\\Game\\levels\\Tutorial\\level.pak")
            manager._sync_library_quick_actions()
            self.assertFalse(buttons["Open Map"].isHidden())


if __name__ == "__main__":
    unittest.main()
