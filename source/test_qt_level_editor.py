"""Offscreen Qt smoke tests for the Unreal-inspired Evolve level editor."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from qt_level_editor import UnrealStyleLevelEditor
from test_level_editor import LAYER_A, LAYER_B, OBJECTS_A, OBJECTS_B


class UnrealStyleLevelEditorSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws=Path(self.temp.name)/"project"
        files=self.ws/"files"
        for name,data in ((LAYER_A,OBJECTS_A),(LAYER_B,OBJECTS_B)):
            p=files/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(data)
        patcher=patch("evolve_pak_workspace.load_workspace",
            return_value={"entries":[{"path":LAYER_A},{"path":LAYER_B}]})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.parent=QMainWindow()
        self.editor=UnrealStyleLevelEditor(self.parent,self.ws)
        self.addCleanup(self.editor.close)
        self.addCleanup(self.parent.close)

    def test_outliner_search_waits_for_typing_to_settle(self):
        e = self.editor
        original = e.outliner.topLevelItemCount()
        e.filter.setText('R')
        e.filter.setText('Rock')
        self.assertTrue(e._outliner_search_timer.isActive())
        self.assertEqual(e.outliner.topLevelItemCount(), original)
        e._outliner_search_timer.timeout.emit()
        self.assertFalse(e._outliner_search_timer.isActive())
        self.assertIn('visible', e.out_count.text())
        self.assertGreaterEqual(e.outliner.topLevelItemCount(), 1)

    def test_preparsed_first_map_skips_constructor_disk_parse(self):
        # The manager now does LevelProject parsing in a worker and hands the
        # parsed project to a Qt-only constructor on the main event thread.
        with patch('qt_level_editor.LevelProject',
                   side_effect=AssertionError('synchronous parse')):
            other = UnrealStyleLevelEditor(
                self.parent, self.ws, prepared=self.editor.project)
            try:
                self.assertIs(other.project, self.editor.project)
                self.assertEqual(len(other.project.objects), 3)
            finally:
                other.close()

    def test_large_outliner_uses_virtual_model_and_selects_exact_object(self):
        from types import SimpleNamespace
        from xml.etree.ElementTree import Element
        editor = self.editor
        old = editor.project
        fake = SimpleNamespace(
            objects=tuple(SimpleNamespace(name='Stone ' + str(i),
                kind='Brush', layer=LAYER_A, geometry='objects/stone.cgf',
                index=i) for i in range(2500)),
            _node=lambda layer, index: Element('Object', {'Layer': 'TestLayer'}))
        try:
            editor.project = fake
            editor._populate_outliner()
            self.assertTrue(editor.virtual_outliner.isVisibleTo(editor)) if editor.isVisible() else self.assertTrue(editor.outliner.isHidden())
            self.assertTrue(editor.outliner.isHidden())
            self.assertEqual(len(editor._outliner_model.root.children[0].children), 2500)
            folder = editor._outliner_model.index(0, 0)
            editor._outliner_model.fetchMore(folder)
            self.assertEqual(editor._outliner_model.rowCount(folder), 256)
            editor.filter.setText('Stone 2499')
            editor._populate_outliner()
            self.assertEqual(len(editor._outliner_model.root.children[0].children), 1)
            self.assertEqual(editor._outliner_model.root.children[0].children[0].value,
                             (LAYER_A, 2499))
        finally:
            editor.project = old
            editor._outliner_catalog = None
            editor.filter.clear()
            editor._populate_outliner()

    def test_unreal_layout_widgets_and_outliner(self):
        e=self.editor
        self.assertEqual(len(e.project.objects),3)
        self.assertEqual(e.outliner.topLevelItemCount(),2)
        self.assertEqual(e.content.topLevelItemCount(),1)
        self.assertEqual(e.content.topLevelItem(0).text(0),"levels")
        self.assertEqual(len(e.boxes["Rotate"]),4)
        self.assertEqual(e.viewport.mode,"select")
        self.assertIn("Level Editor",e.windowTitle())

    def test_world_outliner_details_and_transform_undo(self):
        e=self.editor
        e.select_object(LAYER_A,0)
        self.assertEqual(e.details_name.text(),"Rock_A")
        e.boxes["Pos"][0].setValue(17)
        e.apply_details()
        self.assertEqual(e.project.get(LAYER_A,0).pos[0],17.)
        e.undo()
        self.assertEqual(e.project.get(LAYER_A,0).pos[0],15.)
        self.assertEqual(e.project.dirty_layers(),set())

    def test_viewport_move_and_mode_switch(self):
        e=self.editor
        e._set_mode("move")
        self.assertEqual(e.viewport.mode,"move")
        e.select_object(LAYER_B,0)
        e._viewport_moved(LAYER_B,0,(100,200,4))
        self.assertEqual(e.project.get(LAYER_B,0).pos,(100.,200.,4.))
        e.undo()
        self.assertEqual(e.project.get(LAYER_B,0).pos,(5.,3.,0.))

    def _another_project(self):
        target=Path(self.temp.name)/'alternate'
        for name,data in ((LAYER_A,OBJECTS_A),(LAYER_B,OBJECTS_B)):
            output=target/'files'/name
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(data)
        return target

    def test_in_window_map_switch_resets_project_and_selection(self):
        other=self._another_project()
        self.editor.select_object(LAYER_A,0)
        with patch.object(self.editor.viewport,'reload_scene') as reload_scene:
            self.assertTrue(self.editor.switch_map(other))
        self.assertEqual(self.editor.project.workspace,other.resolve())
        self.assertIsNone(self.editor.selected)
        self.assertIs(self.editor.viewport.project,self.editor.project)
        reload_scene.assert_called_once()

    def test_unsaved_cancel_preserves_current_edits_and_scene(self):
        other=self._another_project()
        self.editor.project.apply(LAYER_A,0,'Pos',(47,1,1))
        with patch('qt_level_editor.QMessageBox.question',
                   return_value=QMessageBox.Cancel):
            self.assertFalse(self.editor.switch_map(other))
        self.assertEqual(self.editor.project.workspace,self.ws.resolve())
        self.assertEqual(self.editor.project.get(LAYER_A,0).pos[0],47.)
        self.assertTrue(self.editor.project.dirty_layers())
        self.editor.project.undo()  # Avoid modal unsaved prompt during test cleanup.

    def test_unsaved_save_failure_preserves_old_project(self):
        other=self._another_project()
        self.editor.project.apply(LAYER_A,0,'Pos',(47,1,1))
        with (patch('qt_level_editor.QMessageBox.question',
                    return_value=QMessageBox.Save),
              patch.object(self.editor.project,'save',
                           side_effect=OSError('read-only destination')),
              patch('qt_level_editor.QMessageBox.critical')):
            self.assertFalse(self.editor.switch_map(other))
        self.assertEqual(self.editor.project.workspace,self.ws.resolve())
        self.assertEqual(self.editor.project.get(LAYER_A,0).pos[0],47.)
        self.editor.project.undo()  # Avoid modal unsaved prompt during test cleanup.

    def test_failed_new_map_load_keeps_old_map(self):
        other=self._another_project()
        with (patch('qt_level_editor.LevelProject',
                    side_effect=ValueError('invalid map')),
              patch('qt_level_editor.QMessageBox.warning')):
            self.assertFalse(self.editor.switch_map(other))
        self.assertEqual(self.editor.project.workspace,self.ws.resolve())
        self.assertIs(self.editor.viewport.project,self.editor.project)


class StreamedMapTextureRetentionTests(unittest.TestCase):
    """Camera residency changes must not discard still-live GL uploads."""

    def _fake_context(self, textures):
        from types import SimpleNamespace
        from unittest.mock import Mock
        return SimpleNamespace(
            _textures=dict(textures), _terrain_texture=99,
            _terrain_uvs={12: 'terrain UVs'}, isValid=lambda: True,
            makeCurrent=Mock(), doneCurrent=Mock())

    def test_unchanged_stream_keeps_gpu_textures_and_terrain(self):
        from types import SimpleNamespace
        from qt_level_scene_render import LevelGLViewport
        live_image = object()
        asset = SimpleNamespace(materials=SimpleNamespace(textures=(live_image,)))
        context = self._fake_context({id(live_image): 42})
        with patch('OpenGL.GL.glDeleteTextures') as delete:
            LevelGLViewport._retain_streamed_textures(context, {'model': asset})
        self.assertEqual(context._textures, {id(live_image): 42})
        delete.assert_not_called()
        context.makeCurrent.assert_not_called()
        self.assertEqual(context._terrain_texture, 99)
        self.assertEqual(context._terrain_uvs, {12: 'terrain UVs'})

    def test_stream_eviction_deletes_only_unused_model_uploads(self):
        from types import SimpleNamespace
        from qt_level_scene_render import LevelGLViewport
        live_image, evicted_image = object(), object()
        asset = SimpleNamespace(materials=SimpleNamespace(textures=(live_image,)))
        context = self._fake_context({id(live_image): 42, id(evicted_image): 43})
        with patch('OpenGL.GL.glDeleteTextures') as delete:
            LevelGLViewport._retain_streamed_textures(context, {'model': asset})
        self.assertEqual(context._textures, {id(live_image): 42})
        delete.assert_called_once_with([43])
        context.makeCurrent.assert_called_once()
        context.doneCurrent.assert_called_once()
        self.assertEqual(context._terrain_texture, 99)




if __name__=="__main__":
    unittest.main()
