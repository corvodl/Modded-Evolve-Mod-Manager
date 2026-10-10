"""GPU mesh preparation, selection, and portable software fallback tests.

GPU context creation is validated in the real Windows application when an
accelerated GPU is present; Linux CI exercises the safe software path.
"""
from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import numpy as np
from PIL import Image
from material_preview import PreviewMaterials
from model_preview import ModelPreview, read_preview_mesh
from gpu_model_preview import GPUUnavailable, PIXELFORMATDESCRIPTOR, prepare_geometry, gpu_supported_platform
from test_material_preview import uv_mesh


class GPUViewerTests(unittest.TestCase):
    def setUp(self):
        self.mesh = read_preview_mesh(uv_mesh(), 'synthetic.skinm')
        self.materials = PreviewMaterials((Image.new('RGB', (24, 24), (120, 150, 210)),),('body',),'test.mtl')

    def test_buffers_are_contiguous_valid_and_usable_by_opengl(self):
        obj = prepare_geometry(self.mesh)
        self.assertEqual(obj.vertices.shape, (len(self.mesh.vertices), 3))
        self.assertEqual(obj.uvs.shape, (len(self.mesh.vertices), 2))
        self.assertEqual(obj.normals.shape, obj.vertices.shape)
        self.assertEqual(len(obj.indices), len(self.mesh.triangles)*3)
        self.assertEqual(obj.vertices.dtype, np.float32)
        self.assertEqual(obj.indices.dtype, np.uint32)
        self.assertTrue(obj.vertices.flags['C_CONTIGUOUS'])
        self.assertTrue(obj.normals.flags['C_CONTIGUOUS'])
        self.assertEqual(sum(sub[1] for sub in obj.draws), len(obj.indices))
        self.assertTrue(np.isfinite(obj.normals).all())
        self.assertEqual(ctypes.sizeof(PIXELFORMATDESCRIPTOR), 40)

    def test_invalid_indices_are_never_passed_to_gpu(self):
        from dataclasses import replace
        altered = replace(self.mesh, triangles=((0, 1, 999999),))
        with self.assertRaises(ValueError):
            prepare_geometry(altered)
        altered = replace(self.mesh, vertices=((0, 0, 0), (1, 0, 0), (float('nan'), 1, 0)))
        with self.assertRaises(ValueError):
            prepare_geometry(altered)

    def test_bad_material_subsets_are_rejected(self):
        from dataclasses import replace
        altered = replace(self.mesh, subsets=((0, 3, 0),))
        with self.assertRaisesRegex(ValueError,'cover all'):
            prepare_geometry(altered)
        altered = replace(self.mesh, subsets=((1, 3, 0),))
        with self.assertRaisesRegex(ValueError,'subset'):
            prepare_geometry(altered)

    def test_hardware_only_on_windows(self):
        from sys import platform
        self.assertEqual(gpu_supported_platform(), platform == 'win32')

    def test_gpu_mode_renders_using_backend_and_handles_ui_interactions(self):
        import tkinter as tk
        try:
            root=tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry('760x520+0+0')
        viewer=None
        try:
            viewer=ModelPreview(root)
            viewer.frame.pack(fill='both',expand=True)
            root.update()
            from unittest.mock import MagicMock
            class FakeGPU:
                def __init__(self,parent):
                    self.renderer='Test Hardware GPU'
                    self.frame=tk.Frame(parent)
                    self.frame.pack(fill='both',expand=True)
                    self.draws=[]
                def set_mesh(self, mesh):self.mesh=mesh
                def set_quality(self, mode):self.quality=mode
                def set_materials(self,mats):self.materials=mats
                def draw(self,mesh,materials,**kw):self.draws.append((mesh,materials,kw))
                def close(self):self.frame.destroy()
            with patch('gpu_model_preview.gpu_supported_platform',return_value=True),\
                 patch('gpu_model_preview.Win32GPUPreview',FakeGPU),\
                 patch.object(viewer, '_render_textured_async') as cpu:
                viewer.set_mesh(self.mesh)
                viewer.set_materials(self.materials)
                viewer.render()
                self.assertIsInstance(viewer._gpu, FakeGPU)
                self.assertEqual(viewer.renderer_mode.get(),'GPU (Auto)')
                viewer.press(SimpleNamespace(x=10,y=10))
                viewer.drag(SimpleNamespace(x=50,y=18))
                viewer.render()
                self.assertTrue(viewer._gpu.draws[-1][2]['textured'])
                self.assertIn('GPU OpenGL',viewer.details.get())
                viewer.release(SimpleNamespace())
                cpu.assert_not_called()
                viewer.renderer_mode.set('Software')
                viewer._change_renderer()
                self.assertIsNone(viewer._gpu)
                viewer._cancel_timer('_pending')
        finally:
            if viewer: viewer._cancel_inflight()
            root.destroy()

    def test_gpu_error_falls_back_without_breaking_software_preview(self):
        import tkinter as tk
        try:root=tk.Tk()
        except tk.TclError as exc:self.skipTest(str(exc))
        try:
            viewer=ModelPreview(root)
            viewer.frame.pack(fill='both',expand=True)
            root.update()
            with patch('gpu_model_preview.gpu_supported_platform',return_value=True), \
                 patch('gpu_model_preview.Win32GPUPreview',side_effect=GPUUnavailable('Driver missing')):
                viewer.set_mesh(self.mesh)
                viewer.render()
                self.assertIsNone(viewer._gpu)
                self.assertEqual(viewer._gpu_unavailable,'Driver missing')
                self.assertIsNotNone(viewer._photo)
                self.assertTrue(viewer.label.winfo_manager())
        finally:
            root.destroy()


if __name__=='__main__':unittest.main()
