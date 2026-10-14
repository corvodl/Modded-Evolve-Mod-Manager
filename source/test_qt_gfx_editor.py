"""Qt GFX authoring path in a synthetic editable PAK workspace."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from gfx_synthetic_fixtures import synthetic_gfx
from gfx_edit_backend import use_edited_gfx_file
from qt_workspace_editor import QtWorkspaceEditor
from qt_gfx_editor import GfxEditorDialog


class GfxQtEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "EditProject"
        self.relative = "Libs/UI/main_menu.gfx"
        self.asset = self.project / "files" / self.relative
        self.asset.parent.mkdir(parents=True)
        self.original = synthetic_gfx(nonvisual_sprite=True,
                                      exported_names=True, scripts=True)
        self.edited = synthetic_gfx(width=80, nonvisual_sprite=True,
                                    exported_names=True, scripts=True)
        self.asset.write_bytes(self.original)
        (self.project / ".evolve-pak-workspace.json").write_text(
            json.dumps({"entries": [{
                "path": self.relative, "format": "raw",
                "sha256": hashlib.sha256(self.original).hexdigest(),
            }]}), encoding="utf-8")
        parent = QMainWindow()
        parent.settings = {}
        parent.busy = False
        parent.persist = lambda: None
        parent.projects_path = str(self.root)
        parent.stage_path = str(self.root)
        parent.open_pak_diff = lambda: None
        self.win = QtWorkspaceEditor(parent, self.project)
        self.win.load(self.relative)
        self.addCleanup(self.win.close)
        self.addCleanup(parent.close)

    def test_gfx_authoring_panel_has_all_edit_actions_and_real_confirm(self):
        dialog = GfxEditorDialog(self.win, self.relative)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.viewer.symbol.count(), 2)
        self.assertEqual(dialog.category.currentData(), "shape")
        self.assertEqual(dialog.resource.currentData(), 1)
        self.assertFalse(dialog.apply.isEnabled())
        self.assertFalse(dialog.undo.isEnabled())
        self.assertEqual(self.asset.read_bytes(), self.original)
        staged = self.root / "complete-authoring-result.gfx"
        staged.write_bytes(self.edited)
        use_edited_gfx_file(self.project, dialog.snapshot.session, staged)
        dialog.reload_edited()
        self.assertTrue(dialog.apply.isEnabled())
        self.assertTrue(dialog.undo.isEnabled())
        with patch("qt_gfx_editor.QMessageBox.question",
                   return_value=QMessageBox.Yes):
            dialog.apply_to_workspace()
        self.assertEqual(self.asset.read_bytes(), self.edited)
        backups = list((self.project / "EditorBackups").rglob("main_menu.gfx"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertFalse((self.project / "GfxEditSessions").is_symlink())

    def test_advanced_gfx_commands_fit_under_menus(self):
        dialog = GfxEditorDialog(self.win, self.relative)
        self.addCleanup(dialog.close)
        self.assertEqual(
            [menu.title() for menu in (dialog.file_menu, dialog.edit_menu,
                                       dialog.tools_menu)],
            ["File", "Edit", "Tools"])
        menu_labels = {menu.title(): {a.text() for a in menu.actions()}
                       for menu in (dialog.file_menu, dialog.edit_menu,
                                    dialog.tools_menu)}
        self.assertIn("Rebuild from edited XML…", menu_labels["Tools"])
        self.assertIn("Export ActionScript sources…", menu_labels["File"])
        self.assertIn("Undo staged edit", menu_labels["Edit"])
        for button in (dialog.open_ffdec, dialog.xml_export, dialog.xml_import,
                       dialog.import_movie, dialog.reload_movie, dialog.undo,
                       dialog.script_export, dialog.script_import):
            self.assertTrue(button.isHidden())
        self.assertFalse(dialog.replace.isHidden())

    def test_library_read_only_cannot_open_mutable_session(self):
        self.win.read_only_library = True
        try:
            with self.assertRaises(PermissionError):
                GfxEditorDialog(self.win, self.relative)
        finally:
            self.win.read_only_library = False


if __name__ == "__main__":
    unittest.main()
