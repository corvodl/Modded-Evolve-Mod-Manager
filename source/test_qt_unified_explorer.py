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
        self.assertEqual(editor.tabs.count(), 5)
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
        # Qt-wide semantic selection coloring replaces per-window red QSS.
        self.assertNotIn("#772b3d", tree.styleSheet().lower())
        self.assertIs(self.editor.editor_split.widget(0), self.editor.tabs)
        self.assertIs(self.editor.editor_split.widget(1), self.editor.explorer)

    def test_large_inventory_uses_virtual_tree_and_full_catalog_search(self):
        editor = self.editor
        for number in range(3500):
            rel = f'configs/generated_{number:04d}.xml'
            editor.records[rel] = {'path': rel, 'format': 'text'}
        editor.populate()
        self.assertTrue(editor._virtual_active)
        self.assertTrue(editor.file_tree.isHidden())
        self.assertEqual(editor.explorer_count.text(), '3503 files')
        self.assertLessEqual(editor._virtual_model.rowCount(), 256)
        editor.search.setText('configs/ability.xml')
        editor.populate()
        self.assertEqual(editor.explorer_count.text(), '1 files')
        folder = editor._virtual_model.index(0, 0)
        self.assertEqual(folder.data(Qt.UserRole + 1), 'configs')
        child = editor._virtual_model.materialize_row(folder, 0)
        self.assertEqual(child.data(Qt.UserRole), 'configs/ability.xml')
        editor.virtual_file_tree.setCurrentIndex(child)
        self.assertEqual(editor.selected(), 'configs/ability.xml')
        self.assertEqual(editor.mode, 'files')
        self.assertEqual(editor.views['files'].toPlainText(),
                         "<Ability value='1' />")
        # Data absent from the first 256 fetched rows must remain findable.
        editor.search.setText('generated_3499')
        editor.populate()
        folder = editor._virtual_model.index(0, 0)
        child = editor._virtual_model.materialize_row(folder, 0)
        self.assertEqual(child.data(Qt.UserRole),
                         'configs/generated_3499.xml')

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

    def test_model_resolve_reuses_existing_library_index(self):
        editor = self.editor
        editor.read_only_library = True
        editor.library_archive = {'id': 'synthetic-pak'}
        original_index = {'characters/hero.skinm': []}
        editor.preview_index = original_index
        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}), \
             patch('qt_workspace_editor.prepare_model_cached',
                   return_value=({}, True, 1.0)) as prepare, \
             patch('asset_library_preview.library_model_index') as build, \
             patch('shared_pak_cache.start_character_catalog') as catalog, \
             patch.object(editor, '_model_ready'):
            editor._load_model_background('characters/hero.skinm')
            build.assert_not_called()
            self.assertIs(prepare.call_args.kwargs['index'], original_index)
            catalog.assert_called_once_with(
                editor.manager.stage_path, 'characters/hero.skinm')

    def test_hidden_desktop_editor_never_runs_model_decode_on_qt_thread(self):
        from unittest.mock import Mock
        editor = self.editor
        pool = Mock()
        editor._model_pool = None
        # The first click may happen just before the preview tab is shown.
        # It must queue work regardless of widget visibility; only offscreen
        # test platforms should retain deterministic synchronous behavior.
        with patch.dict(os.environ, {'QT_QPA_PLATFORM':'windows'}), \
             patch.object(editor, 'isVisible', return_value=False), \
             patch('PySide6.QtCore.QThreadPool', return_value=pool), \
             patch('qt_workspace_editor.prepare_model_cached') as decode, \
             patch.object(editor, '_model_ready') as ready:
            editor._load_model_background('characters/hero.skinm')
        decode.assert_not_called()
        ready.assert_not_called()
        pool.setMaxThreadCount.assert_called_once_with(1)
        pool.start.assert_called_once()
        self.assertIn('Loading model', editor.model_loading_label.text())

    def test_left_side_setting_controls_new_editor(self):
        self.editor.manager.settings['file_explorer_side'] = 'Left'
        other = QtWorkspaceEditor(self.editor.manager, self.editor.workspace)
        self.addCleanup(other.close)
        self.assertEqual(other._file_explorer_side, 'Left')
        self.assertIs(other.editor_split.widget(0), other.explorer)
        self.assertIs(other.editor_split.widget(1), other.tabs)

    def test_side_change_keeps_splitter_widths_and_hidden_visibility(self):
        editor = self.editor
        editor.show()
        self.app.processEvents()
        editor.editor_split.setSizes([800, 310])
        self.app.processEvents()
        before = editor._logical_split_sizes()
        editor.set_file_explorer_side('Left')
        self.app.processEvents()
        self.assertIs(editor.editor_split.widget(0), editor.explorer)
        self.assertIs(editor.editor_split.widget(1), editor.tabs)
        after = editor._logical_split_sizes()
        self.assertLessEqual(abs(before[0] - after[0]), 30)
        self.assertLessEqual(abs(before[1] - after[1]), 30)
        editor.toggle_file_explorer(False)
        editor.set_file_explorer_side('Right')
        self.assertFalse(editor._explorer_visible)
        editor.toggle_file_explorer(True)
        self.app.processEvents()
        self.assertIs(editor.editor_split.widget(0), editor.tabs)
        self.assertIs(editor.editor_split.widget(1), editor.explorer)
        self.assertGreater(editor._logical_split_sizes()[1], 0)

    def test_layout_presets_remain_semantic_after_changing_sides(self):
        editor = self.editor
        editor.show()
        self.app.processEvents()
        for side in ('Left', 'Right'):
            editor.set_file_explorer_side(side)
            editor.set_workspace_layout('Model Focus')
            preview, explorer = editor._logical_split_sizes()
            self.assertGreater(preview, explorer)
            editor.set_workspace_layout('Files First')
            preview, explorer = editor._logical_split_sizes()
            self.assertGreater(explorer, preview)
        editor.set_file_explorer_side('Left')
        editor.save_workspace_layout()
        editor.set_workspace_layout('Model Focus')
        editor._restore_workspace_layout()
        preview, explorer = editor._logical_split_sizes()
        self.assertGreater(explorer, preview)

    def test_typing_search_coalesces_rebuilds(self):
        editor = self.editor
        old_count = editor.explorer_count.text()
        editor.search.setText('c')
        editor.search.setText('ch')
        editor.search.setText('character')
        self.assertTrue(editor._search_timer.isActive())
        self.assertNotEqual(editor.explorer_count.text(), old_count)
        self.assertIn('Search pending', editor.explorer_count.text())
        editor._search_timer.stop()
        # Qt retains the slot bound when the signal was connected; emitting
        # the settled timeout verifies the actual populated result.
        editor._search_timer.timeout.emit()
        self.assertEqual(editor.explorer_count.text(), '1 files')

    def test_search_explorer_and_pak_selection_are_in_explorer(self):
        editor = self.editor
        self.assertIs(editor.search.parentWidget(), editor.explorer)
        editor.search.setText("eye")
        self.assertTrue(editor._search_timer.isActive())
        editor.populate()  # Settle the debounced query deterministically.
        self.assertEqual(editor.explorer_count.text(), "1 files")
        editor.search.clear()
        editor.populate()
        self.assertEqual(editor.explorer_count.text(), "3 files")
        editor.toggle_file_explorer(False)
        self.assertFalse(editor._explorer_visible)
        editor.toggle_file_explorer(True)
        self.assertTrue(editor._explorer_visible)


if __name__ == "__main__":
    unittest.main()
