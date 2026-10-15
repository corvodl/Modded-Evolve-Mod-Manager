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

    def test_ui_data_texture_gallery_loads_only_selected_image(self):
        from PIL import Image
        widget = self.win.gfx_view
        refs = {
            "UI/UIElements/MainMenu.xml": (
                b'<UIElements><UIElement><GFx file="main_menu.gfx"/></UIElement></UIElements>'),
            "UI/TexturePreload.xml": (
                b'<Preload><NoLevel><Preload file="Libs/UI/effects*"/></NoLevel></Preload>')
        }
        files = (*refs, "Libs/UI/main_menu_I9.dds.0",
                 "Libs/UI/effectsModeSelect_I100.dds.0")
        loads = []
        def load_image(path):
            loads.append(path)
            return Image.new("RGBA", (16, 16), (80, 120, 160, 255))
        widget.open_movie(self.original, self.relative,
                          resolve=lambda path: refs.get(path),
                          available_paths=files, texture_preview=load_image,
                          context="test")
        self.assertEqual(widget.texture_select.count(), 2)
        self.assertEqual(loads, [])  # No eager decoding of large UI_Assets.
        widget.tabs.setCurrentIndex(1)
        self.assertEqual(len(loads), 1)
        self.assertFalse(widget.texture_image.pixmap().isNull())

    def test_gfx_canvas_transparency_backgrounds_and_content_zoom(self):
        from io import BytesIO
        from PIL import Image
        from qt_gfx_preview import _Canvas
        canvas = _Canvas()
        self.addCleanup(canvas.close)
        self.assertEqual(canvas._background, "transparent")
        self.assertFalse(canvas._trim)  # Original placement remains the default.

        image = Image.new("RGBA", (80, 60), (255, 255, 255, 0))
        for y in range(10, 16):
            for x in range(42, 50):
                image.putpixel((x, y), (255, 255, 255, 255))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        canvas.set_png(data)
        self.assertEqual((canvas._pixmap.width(), canvas._pixmap.height()), (80, 60))
        self.assertFalse(canvas._fully_opaque)
        self.assertEqual((canvas._visible_rect.x(), canvas._visible_rect.y(),
                          canvas._visible_rect.width(), canvas._visible_rect.height()),
                         (42, 10, 8, 6))

        canvas.set_trim(True)
        self.assertEqual((canvas._pixmap.width(), canvas._pixmap.height()), (8, 6))
        canvas.set_background("dark")
        self.assertEqual(canvas._background, "dark")
        canvas.set_background("light")
        canvas.set_background("white")
        canvas.set_background("transparent")
        with self.assertRaises(ValueError):
            canvas.set_background("invalid")
        canvas.set_trim(False)
        self.assertEqual((canvas._pixmap.width(), canvas._pixmap.height()), (80, 60))

        # An actually opaque white fill cannot be made transparent by a
        # viewer background setting. Do not erase legitimate white artwork.
        buffer = BytesIO()
        Image.new("RGBA", (40, 32), (255, 255, 255, 255)).save(
            buffer, format="PNG")
        canvas.set_png(buffer.getvalue())
        self.assertTrue(canvas._fully_opaque)
        self.assertEqual((canvas._pixmap.width(), canvas._pixmap.height()), (40, 32))

    def test_gfx_background_controls_do_not_change_movie_or_export(self):
        widget = self.win.gfx_view
        self.assertEqual(widget.background.currentData(), "transparent")
        self.assertFalse(widget.trim.isChecked())
        self.assertEqual(widget.canvas._background, "transparent")
        initial = self.asset.read_bytes()
        widget.background.setCurrentIndex(1)
        self.assertEqual(widget.canvas._background, "dark")
        widget.background.setCurrentIndex(2)
        self.assertEqual(widget.canvas._background, "light")
        widget.background.setCurrentIndex(3)
        self.assertEqual(widget.canvas._background, "white")
        widget.background.setCurrentIndex(0)
        widget.trim.setChecked(True)
        self.assertTrue(widget.canvas._trim)
        widget.trim.setChecked(False)
        self.assertEqual(self.asset.read_bytes(), initial)

    def test_exact_atlas_crop_is_decoded_only_when_selected(self):
        from PIL import Image
        from test_gfx_placement_diagnostics import synthetic_atlas
        viewer = self.win.gfx_view
        decoded = []
        def texture_reader(virtual):
            decoded.append(virtual)
            return Image.new("RGBA", (32, 32), (40, 200, 80, 255))
        viewer.open_movie(
            synthetic_atlas(), "Libs/UI/test.gfx",
            available_paths=("Libs/UI/test_I9.dds.0",),
            texture_preview=texture_reader)
        self.assertEqual(decoded, [])  # Do not eagerly read the atlas.
        self.assertEqual(viewer.bitmap_select.count(), 1)
        viewer.tabs.setCurrentIndex(2)
        self.assertEqual(decoded, ["Libs/UI/test_I9.dds.0"])
        self.assertFalse(viewer.bitmap_image.pixmap().isNull())
        self.assertEqual((viewer.bitmap_image.pixmap().width(),
                          viewer.bitmap_image.pixmap().height()), (16, 18))
        self.assertIn("Shapes: #10", viewer.bitmap_status.text())
        self.assertIn("referenced by first static frame",
                      viewer.bitmap_status.text())
        self.assertEqual(self.asset.read_bytes(), self.original)

    def test_featured_promo_options_are_guarded_and_not_game_changes(self):
        dialog = GfxEditorDialog(self.win, self.relative)
        self.addCleanup(dialog.close)
        self.assertIsNotNone(dialog.promo_group)
        self.assertEqual(dialog.promo_mode.count(), 3)
        self.assertEqual(dialog.promo_target.currentData(), "all")
        self.assertFalse(dialog.promo_text.isEnabled())

        dialog.promo_mode.setCurrentIndex(2)
        self.assertEqual(dialog.promo_mode.currentData(), "custom")
        self.assertFalse(dialog.promo_target.isEnabled())
        self.assertTrue(dialog.promo_text.isEnabled())
        self.assertFalse(dialog.promo_stage.isEnabled())
        dialog.promo_text.setText("GOLIATH")
        self.assertTrue(dialog.promo_stage.isEnabled())

        # This fixture has no featured runtime scripts. A click must fail
        # visibly rather than saving an unrelated static text resource.
        with patch("qt_gfx_editor.QMessageBox.warning") as warning:
            dialog.promo_stage.click()
        self.assertTrue(warning.called)
        self.assertFalse(dialog.apply.isEnabled())
        self.assertEqual(self.asset.read_bytes(), self.original)

        dialog.promo_mode.setCurrentIndex(1)
        self.assertEqual(dialog.promo_mode.currentData(), "remove")
        self.assertTrue(dialog.promo_target.isEnabled())
        self.assertFalse(dialog.promo_text.isEnabled())
        self.assertTrue(dialog.promo_stage.isEnabled())

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

    def test_library_read_only_cannot_open_mutable_session(self):
        self.win.read_only_library = True
        try:
            with self.assertRaises(PermissionError):
                GfxEditorDialog(self.win, self.relative)
        finally:
            self.win.read_only_library = False


if __name__ == "__main__":
    unittest.main()
