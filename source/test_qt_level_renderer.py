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

    def test_verified_terrain_renders_in_software_opengl(self):
        from level_terrain_decode import decode_terrain
        from test_level_terrain_decode import synthetic_heightmap

        terrain=decode_terrain(synthetic_heightmap(32,32,maximum=256,unit=2))
        class Project:
            scene=()
            objects=()
            level_name="Synthetic"
            def _node(self,*_):
                return Element("Object")
        with patch("qt_level_scene_render.prepare_scene",
                   return_value=({}, {}, {"terrain":terrain,"mounts":1})):
            canvas=LevelGLViewport(Project())
            try:
                canvas.resize(600,420)
                canvas.camera=[32.,32.,62.]
                canvas.distance=110.
                canvas.pitch=.8
                canvas.show()
                for _ in range(80):
                    self.app.processEvents()
                    canvas._receive()
                    if canvas.terrain is not None and canvas.isValid():
                        break
                    time.sleep(.02)
                self.assertTrue(canvas.isValid(),"Software OpenGL context unavailable")
                self.assertIsNotNone(canvas.terrain)
                image=canvas.grabFramebuffer()
                self.assertFalse(image.isNull())
                pixels=np.asarray(
                    [image.pixelColor(x,y).lightness()
                     for y in range(50,image.height()-50,20)
                     for x in range(50,image.width()-50,20)])
                corner=image.pixelColor(3,3).lightness()
                self.assertGreater(int(pixels.max())-int(corner),20,
                    "Actual decoded terrain did not rasterize in the framebuffer.")
            finally:
                canvas.close()

    def test_chunked_terrain_tile_preview_and_stale_progress(self):
        from PIL import Image
        from level_progress import LoadProgress
        from level_terrain_decode import decode_terrain_chunks
        from test_level_terrain_decode import synthetic_heightmap
        terrain=tuple(decode_terrain_chunks(synthetic_heightmap(32,32,maximum=256,unit=2),chunk_cells=16))
        project=SimpleNamespace(scene=(),objects=(),level_name='synthetic')
        details={'terrain':terrain[0],'terrain_chunks':terrain,
                 'terrain_atlas':Image.new('RGB',(16,16),(230,30,20))}
        with patch('qt_level_scene_render.prepare_scene',return_value=({}, {}, details)):
            canvas=LevelGLViewport(project)
            try:
                canvas.resize(600,420)
                canvas.camera=[32.,32.,62.]
                canvas.distance=110.
                canvas.pitch=.8
                canvas.show_terrain_tiles=True
                canvas.show()
                for _ in range(80):
                    self.app.processEvents()
                    canvas._receive()
                    if canvas.terrain is not None and canvas.isValid():break
                    time.sleep(.02)
                image=canvas.grabFramebuffer()
                pixels=[image.pixelColor(x,y) for y in range(50,370,20) for x in range(50,550,20)]
                self.assertTrue(any(p.red()>p.green()*2 for p in pixels))
                events=[]
                canvas.progressChanged.connect(events.append)
                canvas._apply_progress(LoadProgress(canvas._generation-1,'decode',1,2,'stale'))
                self.assertFalse(events)
                canvas.cancel_loading()
                canvas._apply_progress(LoadProgress(canvas._generation,'decode',1,2,'cancelled'))
                self.assertFalse(events)
            finally:
                canvas.close()



if __name__=="__main__":
    import unittest
    unittest.main()
