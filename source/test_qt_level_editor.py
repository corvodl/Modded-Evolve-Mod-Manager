"""Offscreen Qt smoke tests for the Unreal-inspired Evolve level editor."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtWidgets import QApplication, QMainWindow
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


if __name__=="__main__":
    unittest.main()
