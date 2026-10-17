"""Offscreen end-to-end smoke test for the read-only imported-PAK viewer.

The fixture creates a real temporary Asset Library manifest and extracted
file. Both the Qt explorer and its read_asset() backend must resolve the
SAME library root. Mocking only the Qt explorer's archive folder would conceal
routing bugs and make the backend query the actual user's library.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QTreeWidgetItemIterator

from qt_asset_preview import QtAssetPreviewWindow


def same_directory(left: Path, right: Path) -> bool:
    """Compare filesystem identity across Windows long and 8.3 short paths."""
    return os.path.samefile(left, right)


class ReadOnlyAssetPreviewUITests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library_root = root / 'AssetLibrary'
        self.identifier = 'a' * 20
        self.folder = self.library_root / self.identifier
        self.relative = 'characters/hunter.xml'
        self.source = self.folder / 'files' / self.relative
        self.source.parent.mkdir(parents=True)
        self.source.write_text('<Character name="Maggie"/>', encoding='utf-8')
        # The viewer must group real virtual paths without parsing/changing
        # these synthetic binary mesh fixtures.
        self.models = [
            'characters/scrap/scrap_3p.skinm',
            'characters/scrap/scrap_3p_lod1.skinm',
            'characters/scrap/scrap_3p_lod2.skinm',
        ]
        for model in self.models:
            target = self.folder / 'files' / model
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'fixture')
        (self.folder / 'asset-library.json').write_text(json.dumps({
            'version': 1,
            'name': 'characters_hunters_maggie_data.pak',
            'entries': ([{
                'path': self.relative,
                'bytes': self.source.stat().st_size,
            }] + [{
                'path': relative,
                'bytes': (self.folder / 'files' / relative).stat().st_size,
            } for relative in self.models]),
        }), encoding='utf-8')

        # Patch the one backend source of truth, not archive lookups in just
        # one UI module; this exercises real manifest validation + read_asset.
        library_home = patch('asset_library_preview.LIBRARY_HOME',
                             self.library_root)
        library_home.start()
        self.addCleanup(library_home.stop)
        self.parent = QMainWindow()
        self.parent.settings = {}
        self.parent.persist = lambda: None
        self.parent.busy = False
        self.parent.stage_path = str(root / 'stage')
        self.parent.projects_path = str(root / 'projects')
        self.parent.game_root = None
        self.parent._find_game_root = lambda: None
        self.parent.open_pak_diff = lambda: None
        self.parent.open_level_editor = lambda *_: None
        self.parent.export_library_archive_by_id = lambda identifier: None
        self.addCleanup(self.parent.close)
        self.window = QtAssetPreviewWindow(self.parent, {
            'id': self.identifier, 'name': 'characters_hunters_maggie_data.pak',
        })
        self.addCleanup(self.window.close)

    def test_compact_library_header_preserves_explorer_and_read_only_status(self):
        window = self.window
        layout = window.centralWidget().layout()
        # One header row, the splitter, and a bottom status line: no stacked
        # banners stealing model viewport space.
        self.assertEqual(layout.count(), 3)
        self.assertIs(layout.itemAt(0).layout(), window.workspace_header)
        self.assertIs(layout.itemAt(1).widget(), window.editor_split)
        self.assertIs(window.workspace_header.itemAt(0).widget(),
                      window.current_file_label)
        self.assertIs(window.workspace_header.itemAt(1).widget(),
                      window.selection_label)
        self.assertIs(window.workspace_header.itemAt(2).widget(),
                      window.sidebar_button)
        self.assertEqual(window.selection_label.objectName(),
                         'libraryReadOnlyNotice')
        self.assertIn('Read-only', window.selection_label.text())
        self.assertIn('editing and importing are disabled',
                      window.selection_label.toolTip().lower())
        self.assertIn('editable copy', window.selection_label.toolTip())
        self.assertTrue(window.read_only_library)
        self.assertIs(window.editor_split.widget(1), window.explorer)
        self.assertEqual(window.explorer.objectName(), 'fileExplorerPane')
        self.assertEqual(window.explorer.findChild(
            QLabel, 'fileExplorerHeading').text(), 'FILE EXPLORER')
        self.assertIs(window.search.parentWidget(), window.explorer)
        self.assertEqual(window.sidebar_button.size().width(), 36)

        for width in (660, 840, 1280):
            window.resize(width, 480)
            window.show()
            self.app.processEvents()
            # Native Windows Qt may add 2–3px of style metrics to this row.
            self.assertLessEqual(window.workspace_header.geometry().height(), 40)
            self.assertLessEqual(window.selection_label.height(), 22)
            self.assertEqual(layout.count(), 3)
            self.assertGreater(window.editor_split.height(), 300)
            self.assertTrue(window.explorer.isVisible())

        item = None
        walker = QTreeWidgetItemIterator(window.file_tree)
        while walker.value():
            if walker.value().data(0, Qt.UserRole) == self.relative:
                item = walker.value()
                break
            walker += 1
        self.assertIsNotNone(item)
        window.file_tree.setCurrentItem(item)
        self.app.processEvents()
        self.assertIn('selected', window.selection_label.text())
        self.assertEqual(layout.count(), 3)
        self.assertEqual(self.source.read_text(encoding='utf-8'),
                         '<Character name="Maggie"/>')

    def test_compact_library_selection_recovers_read_only_tooltip(self):
        window = self.window
        with patch.object(window, 'selected_virtual_files',
                          side_effect=ValueError('At most 500 files')):
            window._report_selection()
        self.assertEqual(window.selection_label.text(), 'Selection limit')
        self.assertIn('At most 500', window.selection_label.toolTip())
        window._report_selection()
        self.assertIn('Read-only', window.selection_label.text())
        self.assertIn('editable copy', window.selection_label.toolTip())

    def test_compact_character_equipment_menu_is_data_driven(self):
        from types import SimpleNamespace
        window = self.window
        menu = window.character_equipment_menu
        layout = window.centralWidget().layout()
        self.assertFalse(menu.menuAction().isVisible())
        root = 'characters/hunters/merc_emet/'
        normal = root + 'emet_3p_face_normal.skin'
        oh = root + 'emet_3p_face_oh.skin'
        cannon = root + 'emetremotecannon_3p.skin'
        window.character_preview = SimpleNamespace(
            equipment_options=(('remotecannon', cannon),),
            selected_equipment=cannon,
            faceplate_options=(('normal', normal), ('oh', oh)),
            selected_faceplate=normal)
        window._update_character_equipment_menu()
        self.assertTrue(menu.menuAction().isVisible())
        self.assertEqual(layout.count(), 3)
        groups = {action.text(): action.menu() for action in menu.actions()
                  if action.menu() is not None}
        self.assertEqual(set(groups), {'Weapons', 'Face / Head Variants'})
        self.assertTrue(groups['Weapons'].actions()[0].isChecked())
        self.assertTrue(groups['Face / Head Variants'].actions()[0].isChecked())
        with patch.object(window, '_select_faceplate') as select:
            groups['Face / Head Variants'].actions()[1].trigger()
            select.assert_called_once_with(oh)

        # Swapping to a file without an owning CDF hides all menu UI.
        window.character_preview = None
        window._update_character_equipment_menu()
        self.assertFalse(menu.menuAction().isVisible())
        self.assertEqual(layout.count(), 3)

    def test_restore_default_loadout_keeps_emet_normal_face(self):
        from types import SimpleNamespace
        window = self.window
        root = 'characters/hunters/merc_emet/'
        normal = root + 'emet_3p_face_normal.skin'
        alternate = root + 'emet_3p_face_evil.skin'
        window.character_preview = SimpleNamespace(
            faceplate_options=(('evil', alternate), ('normal', normal),))
        window.mode = 'models'
        window.current = self.models[0]
        window.full_character_active = True
        window.character_skin_choice = root + 'emetremotecannon_3p.skin'
        window.character_faceplate_choice = alternate
        with patch.object(window, 'load') as reload:
            window._restore_character_loadout()
        reload.assert_called_once_with(self.models[0], full_character=True)
        self.assertIsNone(window.character_skin_choice)
        self.assertEqual(window.character_faceplate_choice, normal)

    def test_opening_model_defers_library_validation_to_worker(self):
        # A model click must never perform manifest/file system checks on
        # the Qt event loop. Background library_model_index still validates
        # archive entries and byte sizes before passing source paths onward.
        from asset_library_preview import archive_entries, library_model_index
        relative = self.models[0]
        with patch('asset_library_preview.archive_entries',
                   wraps=archive_entries) as checked, patch.object(
                   self.window, '_load_model_background') as load:
            self.window.load(relative)
            checked.assert_not_called()
        load.assert_called_once_with(relative)
        self.assertEqual(self.window.current, relative)
        # The same manifest checks still run in the worker's resolver.
        index = library_model_index(self.identifier)
        self.assertIn(relative.casefold(), index)
        chosen = next(row for row in index[relative.casefold()]
                      if row.get('_asset_library') == self.identifier)['file']
        # Windows CI may mix long and 8.3 aliases for the temp directory.
        # Compare actual file identity, not superficial path spellings.
        self.assertTrue(Path(chosen).samefile(self.folder / 'files' / relative))

    def test_gfx_library_preview_and_rgba_png_export_preserve_source(self):
        import io
        from PIL import Image
        from gfx_preview_backend import GfxError
        from gfx_synthetic_fixtures import synthetic_gfx
        from asset_library_preview import gfx_movie
        relative = "Libs/UI/film.gfx"
        movie = synthetic_gfx(scripts=True)
        path = self.folder / "files" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(movie)
        manifest = self.folder / "asset-library.json"
        data = json.loads(manifest.read_text("utf-8"))
        data["entries"].append({"path": relative, "bytes": len(movie)})
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.window.refresh()
        with patch("qt_gfx_preview.ffdec_command",
                   side_effect=GfxError("Configure FFDec 26.3.0")):
            self.window.preview(relative)
        self.assertEqual(self.window.mode, "gfx")
        self.assertEqual(self.window.tabs.currentIndex(), 3)
        self.assertEqual(gfx_movie(self.identifier, relative).frames, 2)
        self.assertFalse(self.window.gfx_view.export.isEnabled())
        picture = io.BytesIO()
        Image.new("RGBA", (64, 48), (200, 30, 10, 90)).save(picture, "PNG")
        self.window.gfx_view._finished(
            self.window.gfx_view._serial, picture.getvalue(), None)
        output = Path(self.temp.name) / "frame.png"
        with patch("qt_workspace_editor.QFileDialog.getSaveFileName",
                   return_value=(str(output), "PNG (*.png)")):
            self.window.export_png()
        with Image.open(output) as result:
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.getpixel((20, 20))[3], 90)
        self.assertEqual(path.read_bytes(), movie)
        self.window.gfx_view._finished(
            self.window.gfx_view._serial - 1, b"invalid old PNG", None)
        self.assertEqual(self.window.gfx_view.selected_png(), picture.getvalue())

    def test_legacy_archive_preview_uses_unassigned_context(self):
        # A readable legacy manifest without build membership must still
        # support read-only preview, while never pulling foreign dependencies.
        from asset_library_preview import library_model_index
        from build_collections import resolve_archive_build
        self.assertEqual(resolve_archive_build(
            self.identifier, root=self.library_root), "unassigned")
        index = library_model_index(self.identifier, root=self.library_root)
        self.assertIn(self.models[0], index)
        self.assertEqual(index[self.models[0]][0]["_asset_library"], self.identifier)

    def test_text_preview_and_noneditable_controls(self):
        self.window.show()
        self.assertTrue(same_directory(self.window.folder, self.folder))
        iterator = QTreeWidgetItemIterator(self.window.tree)
        choice = None
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == self.relative:
                choice = item
                break
            iterator += 1
        self.assertIsNotNone(choice)
        self.window.tree.setCurrentItem(choice)
        self.assertIn('Character name', self.window.text.toPlainText())
        self.assertTrue(self.window.text.isReadOnly())
        self.assertEqual(self.source.read_text(encoding='utf-8'),
                         '<Character name="Maggie"/>')
        labels = ' '.join(x.text() for x in self.window.findChildren(QLabel))
        self.assertIn('Read-only', labels)
        self.assertIn('preview and export only',
                      self.window.selection_label.toolTip())
        self.assertNotIn('Save Changes', [
            x.text() for x in self.window.findChildren(QLabel)])

    def test_model_lods_share_a_grouped_explorer_family(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator = QTreeWidgetItemIterator(self.window.tree)
        found = {}
        while iterator.value():
            node = iterator.value()
            rel = node.data(0, Qt.UserRole)
            if rel in self.models:
                found[rel] = node
            iterator += 1
        self.assertEqual(set(found), set(self.models))
        primary = found[self.models[0]]
        for lod in self.models[1:]:
            self.assertIs(found[lod].parent(), primary)
        self.assertTrue(primary.text(0).casefold().find('model') >= 0)

    @staticmethod
    def _fake_export_glb(_archive, _rel, target):
        from types import SimpleNamespace
        from model_blender_export import glb_bytes
        from model_textured_export import embed_diffuse_textures
        from material_preview import PreviewMaterials
        from PIL import Image
        mesh=SimpleNamespace(vertices=[(0,0,0),(1,0,0),(0,1,0)],
                             triangles=[(0,1,2)],uvs=[(0,0),(1,0),(0,1)],
                             subsets=())
        materials=PreviewMaterials(
            (Image.new('RGB',(16,16),(100,120,140)),),('body',),'body.mtl')
        blob,_=embed_diffuse_textures(glb_bytes(mesh),materials)
        target.write_bytes(blob)
        return target, {'textures':1,'animation_clips':0}

    def test_asset_library_character_folder_routes_to_canonical_export(self):
        """Reproduce the Emet menu route, not the obsolete GLB-per-file loop."""
        from types import SimpleNamespace
        from model_blender_export import glb_bytes
        character = 'characters/hunters/merc_emet'
        model = character + '/emet_3p.skinm'
        texture = character + '/textures/emet_diff.dds'
        for rel, payload in ((model, b'synthetic mesh'),
                             (texture, b'DDS source')):
            source = self.folder / 'files' / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(payload)
        manifest_file = self.folder / 'asset-library.json'
        manifest = json.loads(manifest_file.read_text('utf-8'))
        manifest['entries'].extend([
            {'path': path, 'bytes': (self.folder / 'files' / path).stat().st_size}
            for path in (model, texture)])
        manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
        self.window.refresh()

        selected = None
        iterator = QTreeWidgetItemIterator(self.window.file_tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole + 1) == character:
                selected = item
                break
            iterator += 1
        self.assertIsNotNone(selected)
        self.window.file_tree.setCurrentItem(selected)
        self.assertEqual(self.window._current_model_folder_relative(), character)
        self.assertTrue(same_directory(
            self.window._current_model_source_folder(),
            self.folder / 'files' / character))

        destination = Path(self.temp.name) / 'BlenderExports'
        destination.mkdir()
        mesh = SimpleNamespace(
            vertices=[(0,0,0),(1,0,0),(0,1,0)],
            triangles=[(0,1,2)], uvs=[(0,0),(1,0),(0,1)], subsets=())
        blob = glb_bytes(mesh)
        body = (blob, {'cdf': character + '/emet_3p.cdf',
                       'preferred_full_character': True, 'bone_count':3,
                       'part_count':2, 'embedded_animations':0})
        cannon = (blob, {'cdf': character + '/emetcannon.cdf',
                         'bone_count':0, 'part_count':1,
                         'embedded_animations':0})

        def run_inline(_parent, _title, _label, work, **callbacks):
            result = work(lambda *_: None, lambda: False)
            callbacks['on_success'](result)

        def fake_animations(root, *_args, **_kwargs):
            directory = Path(root) / 'animations'
            directory.mkdir()
            (directory / 'animation_manifest.json').write_text(
                json.dumps({'converted_clips':[], 'raw_sources':[]}),
                encoding='utf-8')
            return {'animations': 0, 'sources': 0}

        with (patch('qt_workspace_editor.QFileDialog.getExistingDirectory',
                    return_value=str(destination)) as picker,
              patch('qt_background_exports.begin_file_operation',
                    side_effect=run_inline),
              patch('character_export_command.build_cdf_folder_glbs',
                    return_value=({
                        'Assemblies/emet_3p.glb': body,
                        'Assemblies/emetcannon.glb': cannon}, ())),
              patch('character_animation_export.export_character_animation_folder',
                    side_effect=fake_animations),
              patch('qt_workspace_editor.QMessageBox.information'),
              patch('qt_asset_preview.export_glb') as old_export):
            self.window.export_blender_folder()
        picker.assert_called_once()
        old_export.assert_not_called()
        root = destination / 'Emet'
        self.assertTrue((root / 'Emet.glb').is_file())
        self.assertEqual((root / 'textures' / 'emet_diff.dds').read_bytes(),
                         b'DDS source')
        self.assertTrue((root / 'animations' / 'animation_manifest.json').exists())
        self.assertTrue((root / 'models' / 'emetcannon.glb').is_file())
        self.assertTrue((root / 'models' / 'emet_3p.skinm').is_file())
        self.assertFalse((root / 'Assemblies').exists())
        self.assertFalse((root / 'CDF_assembly_report.json').exists())
        self.assertFalse((root / 'models' / 'emet_3p.glb').exists())
        self.assertFalse((destination / 'merc_emet').exists())
        self.assertFalse(any(destination.glob('*.glb')))

    def test_export_folder_uses_selected_model_parent_without_source_dialog(self):
        iterator = QTreeWidgetItemIterator(self.window.file_tree)
        model_item = None
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == self.models[0]:
                model_item = item
                break
            iterator += 1
        self.assertIsNotNone(model_item)
        self.window.file_tree.setCurrentItem(model_item)
        self.assertEqual(
            self.window._current_model_folder_relative(), 'characters/scrap')
        self.assertTrue(same_directory(
            self.window._current_model_source_folder(),
            self.folder / 'files' / 'characters' / 'scrap'))

    def test_same_modding_editor_model_tools_but_no_import_or_save(self):
        from qt_workspace_editor import QtWorkspaceEditor
        self.assertIsInstance(self.window, QtWorkspaceEditor)
        self.assertEqual(self.window.editor_split.objectName(),
                         'unifiedEditorSplitter')
        self.assertIs(self.window.file_tree, self.window.tree)
        self.assertTrue(self.window.read_only_library)
        self.assertTrue(self.window.actions['files']['Save'].isHidden())
        self.assertTrue(self.window.actions['files']['Import / Replace File'].isHidden())
        self.assertTrue(self.window.actions['images']['Import DDS'].isHidden())
        self.assertTrue(self.window.actions['images']['Import PNG'].isHidden())
        self.assertFalse(self.window.save_shortcut.isEnabled())
        model_names = [action.text() for action in
                       self.window.editor_menus['Model'].actions()
                       if action.isVisible()]
        self.assertIn('Open GPU Viewer in New Window', model_names)
        self.assertIn('Compare Models Side by Side…', model_names)
        self.assertNotIn('Import Same-Layout Model…', model_names)
        with self.assertRaises(PermissionError):
            self.window.import_dds()
        with self.assertRaises(PermissionError):
            self.window.import_png()
        with self.assertRaises(PermissionError):
            self.window.import_model()
        with self.assertRaises(PermissionError):
            self.window._import_and_reload(lambda _: None)
        self.window.save()
        self.assertEqual(self.source.read_text(encoding='utf-8'),
                         '<Character name="Maggie"/>')


if __name__ == '__main__':
    import unittest
    unittest.main()
