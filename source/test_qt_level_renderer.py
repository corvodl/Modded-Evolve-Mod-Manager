"""Software-OpenGL integration smoke: actual triangles, no proprietary assets.

Run under Xvfb with LIBGL_ALWAYS_SOFTWARE=1 and QT_QPA_PLATFORM=xcb.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from xml.etree.ElementTree import Element

import numpy as np
from PySide6.QtWidgets import QApplication

from gpu_model_preview import prepare_geometry
from level_editor_core import PlacedObject
from level_scene_core import SceneObject, WorldTransform
from qt_level_scene_render import LevelGLViewport, RenderAsset


class TestRenderedLevelViewport(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_actual_depth_tested_triangle_rasterization(self):
        cube = SimpleNamespace(
            vertices=((-2.,-2.,0.),(2.,-2.,0.),(2.,2.,0.),(-2.,2.,0.)),
            triangles=((0,1,2),(0,2,3)),
            center=(0.,0.,0.),radius=2.83,
            source_name="synthetic level square",uvs=(),subsets=())
        arrays = prepare_geometry(cube,"smooth")
        asset = RenderAsset(cube,arrays,None,1024,"synthetic.cgf",
                            ((-2.,-2.,0.),(2.,2.,0.)))
        world=WorldTransform()
        item=SceneObject("layer.lyr","cube","","Synthetic floor","Brush",
                         "objects/ground.cgf","",world,world,"model reference available")
        placed=PlacedObject("layer.lyr",0,"Synthetic floor","Brush",
                            "objects/ground.cgf",(0.,0.,0.),(1.,0.,0.,0.),(1.,1.,1.))
        class Project:
            scene=(item,)
            objects=(placed,)
            level_name="synthetic"
            def _node(self,*_):
                return Element("Object",{"Id":"cube","Type":"Brush"})
        data=({("objects/ground.cgf",""):asset},{},{"mounts":1})
        with patch("qt_level_scene_render.prepare_scene",return_value=data):
            canvas=LevelGLViewport(Project())
            try:
                canvas.resize(600,420)
                canvas.camera=[0.,0.,0.]
                canvas.distance=12.
                canvas.show()
                for i in range(60):
                    self.app.processEvents()
                    canvas._receive()
                    if canvas.assets and canvas.isValid():
                        break
                    time.sleep(.02)
                self.assertTrue(canvas.isValid(), "OpenGL context unavailable in software GL test")
                self.assertTrue(canvas.assets)
                image=canvas.grabFramebuffer()
                self.assertFalse(image.isNull())
                center=image.pixelColor(image.width()//2,image.height()//2)
                corner=image.pixelColor(2,2)
                self.assertGreater(center.lightness()-corner.lightness(),25,
                                   "Scene triangle did not actually rasterize")
            finally:
                canvas.close()


if __name__=="__main__":
    import unittest
    unittest.main()
