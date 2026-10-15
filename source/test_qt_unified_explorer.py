"""Unifed explorer file-to-viewer routing; synthetic projects only."""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QTreeWidgetItemIterator

from qt_workspace_editor import QtWorkspaceEditor


class UnifiedExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        workspace = root / "workspace"
        files = workspace / "files"
        for name, body in (
            ("configs/ability.xml", b"<Ability value='1' />"),
            ("textures/eye.dds", b"DDS fixture"),
            ("characters/hero.skinm", b"CrChF fixture")):
            file = files / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(body)
        (workspace / ".evolve-pak-workspace.json").write_text(json.dumps({
            "entries": [
                {"path": "configs/ability.xml", "format": "text"},
                {"path": "textures/eye.dds", "format": "dds"},
                {"path": "characters/hero.skinm", "format": "binary"}]}))
        manager = QMainWindow()
        manager.settings = {}
        manager.busy = False
        manager.projects_path = root
        manager.stage_path = root
        manager.current_workspace = str(workspace)
        manager.persist = lambda: None
        manager.open_pak_diff = lambda: None
        self.editor = QtWorkspaceEditor(manager, workspace)
        self.addCleanup(self.editor.close)
        self.addCleanup(manager.close)

    def _find(self, name):
        iterator = QTreeWidgetItemIterator(self.editor.file_tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == name:
                return item
            iterator += 1
        self.fail("File not found: " + name)

    def test_single_visible_file_tree_and_hidden_internal_tabs(self):
        editor = self.editor
        self.assertEqual(editor.explorer_count.text(), "3 files")
        self.assertEqual(editor.tabs.count(), 4)
        self.assertTrue(editor.tabs.tabBar().isHidden())
        self.assertIs(editor.trees["files"], editor.trees["images"])
        self.assertIs(editor.trees["images"], editor.trees["models"])
        self.assertEqual(editor.file_tree.columnCount(), 2)

    def test_explorer_names_scroll_and_icons_are_not_duplicated(self):
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView
        tree = self.editor.file_tree
        self.assertEqual(tree.columnWidth(0), 425)
        self.assertGreaterEqual(tree.columnWidth(1), 80)
        self.assertEqual(tree.header().sectionResizeMode(0),
                         QHeaderView.Interactive)
        self.assertEqual(tree.horizontalScrollMode(),
                         QAbstractItemView.ScrollPerPixel)
        self.assertEqual(tree.textElideMode(), Qt.ElideMiddle)
        self.assertFalse(tree.alternatingRowColors())
        self.assertNotIn("★", self.editor.favorites_only.text())
        for name in ("configs/ability.xml", "textures/eye.dds",
                     "characters/hero.skinm"):
            item = self._find(name)
            self.assertEqual(item.text(0), Path(name).name)
            self.assertFalse(item.icon(0).isNull())
            self.assertIn(name, item.toolTip(0))
        self.assertNotIn("#0000ff", tree.styleSheet().lower())
        self.assertIn("#772b3d", tree.styleSheet().lower())

    def test_click_selects_correct_editor_without_visible_tabs(self):
        editor = self.editor
        editor.file_tree.setCurrentItem(self._find("configs/ability.xml"))
        self.assertEqual(editor.tabs.currentIndex(), 0)
        self.assertEqual(editor.mode, "files")
        self.assertFalse(editor.views["files"].isReadOnly())
        with patch("qt_workspace_editor.preview_dds",
                   return_value=Image.new("RGBA", (2, 2))), patch(
                "qt_workspace_editor.parse_dds",
                return_value=SimpleNamespace(description="test fixture")):
            editor.file_tree.setCurrentItem(self._find("textures/eye.dds"))
        self.assertEqual(editor.mode, "images")
        self.assertEqual(editor.tabs.currentIndex(), 1)
        with patch.object(editor, "_load_model_background") as load_model:
            editor.file_tree.setCurrentItem(self._find("characters/hero.skinm"))
        self.assertEqual(editor.tabs.currentIndex(), 2)
        load_model.assert_called_once_with("characters/hero.skinm")

    def test_gfx_project_selection_uses_static_viewer_without_touching_file(self):
        from gfx_synthetic_fixtures import synthetic_gfx
        from gfx_preview_backend import GfxError
        relative = "Libs/UI/my_movie.gfx"
        source = self.editor.workspace / "files" / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        movie = synthetic_gfx(
            scripts=True, nonvisual_sprite=True, exported_names=True)
        source.write_bytes(movie)
        self.editor.records[relative] = {"path": relative, "format": "raw"}
        self.editor.populate()
        with patch("qt_gfx_preview.ffdec_command",
                   side_effect=GfxError("Configure FFDec 26.3.0")):
            self.editor.file_tree.setCurrentItem(self._find(relative))
        self.assertEqual(self.editor.mode, "gfx")
        self.assertEqual(self.editor.tabs.currentIndex(), 3)
        self.assertEqual(self.editor.gfx_view.frame.maximum(), 2)
        self.assertEqual(self.editor.gfx_view.symbol.count(), 2)
        self.assertIn("mcButtonMainMenu", self.editor.gfx_view.symbol.itemText(1))
        self.assertIn("nonvisual", self.editor.gfx_view.note.text())
        self.assertIn("Configure FFDec", self.editor.gfx_view.note.text())
        self.assertEqual(source.read_bytes(), movie)
        self.assertFalse(self.editor.gfx_view.export.isEnabled())

    def test_search_explorer_and_pak_selection_are_in_explorer(self):
        editor = self.editor
        self.assertIs(editor.search.parentWidget(), editor.explorer)
        editor.search.setText("eye")
        self.assertEqual(editor.explorer_count.text(), "1 files")
        editor.search.clear()
        self.assertEqual(editor.explorer_count.text(), "3 files")
        editor.toggle_file_explorer(False)
        self.assertFalse(editor._explorer_visible)
        editor.toggle_file_explorer(True)
        self.assertTrue(editor._explorer_visible)


if __name__ == "__main__":
    unittest.main()
