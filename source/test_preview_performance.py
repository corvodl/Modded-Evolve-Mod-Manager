"""Regression tests for responsive CPU 3D previews (no game assets)."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from material_preview import PreviewMaterials, RenderCancelled, render_textured_mesh
from model_preview import ModelPreview, read_preview_mesh, render_mesh
from test_material_preview import uv_mesh


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.mesh = read_preview_mesh(uv_mesh(), 'fixture.skinm')
        self.mats = PreviewMaterials((Image.new('RGB', (16, 16), (170, 50, 85)),), ('body',), 'fixture.mtl')

    def test_prepared_texture_arrays_are_reused(self):
        first = self.mats.rgb_arrays
        self.assertIs(first, self.mats.rgb_arrays)
        self.assertEqual(first[0].shape, (16, 16, 3))
        render_textured_mesh(self.mesh, self.mats, 140, 140)
        self.assertIs(first, self.mats.rgb_arrays)

    def test_cancelled_raster_does_not_render_unnecessary_frames(self):
        with self.assertRaises(RenderCancelled):
            render_textured_mesh(self.mesh, self.mats, 320, 220, cancel=lambda: True)
        signal = threading.Event()
        signal.set()
        with self.assertRaises(RenderCancelled):
            render_textured_mesh(self.mesh, self.mats, 320, 220, cancel=signal.is_set)

    def test_face_budget_keeps_shaded_render_correct(self):
        low = render_mesh(self.mesh, 260, 180, max_draw_triangles=2)
        full = render_mesh(self.mesh, 260, 180)
        self.assertEqual(low.size, full.size)
        self.assertNotEqual(low.getpixel((130, 90)), (32, 33, 42))

    def test_moving_skips_uv_renderer_until_release(self):
        import tkinter as tk
        from tkinter import ttk
        try:
            root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f'No Tk display: {e}')
        root.geometry('700x500+0+0')
        try:
            viewer = ModelPreview(root)
            viewer.frame.pack(fill='both', expand=True)
            root.update()
            viewer.renderer_mode.set('Software')  # Exercise the CPU fallback deliberately.
            with patch.object(viewer, '_render_textured_async') as textured:
                viewer.set_mesh(self.mesh)
                viewer.set_materials(self.mats)
                viewer._cancel_timer('_pending')
                viewer.press(SimpleNamespace(x=100, y=100))
                viewer.drag(SimpleNamespace(x=150, y=112))
                viewer.render()
                self.assertIn('Fast rotation preview', viewer.details.get())
                self.assertFalse(viewer._want_textured)
                textured.assert_not_called()
                viewer.release(SimpleNamespace(x=150, y=112))
                self.assertFalse(viewer._interacting)
                viewer._cancel_timer('_settle_pending')
                viewer._settle()
                self.assertTrue(viewer._want_textured)
                textured.assert_called_once()
        finally:
            root.destroy()

    def test_drag_cancels_active_render_and_clear_cleans_timers(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f'No Tk display: {e}')
        try:
            viewer = ModelPreview(root)
            viewer.set_mesh(self.mesh)
            current = threading.Event()
            viewer._render_cancel = current
            viewer.press(SimpleNamespace(x=0, y=0))
            self.assertTrue(current.is_set())
            viewer.drag(SimpleNamespace(x=30, y=10))
            viewer.clear()
            self.assertIsNone(viewer.mesh)
            self.assertIsNone(viewer._pending)
            self.assertIsNone(viewer._settle_pending)
        finally:
            root.destroy()

    def test_quality_bounds(self):
        fast = ModelPreview.QUALITY_LIMITS['Fast']
        balanced = ModelPreview.QUALITY_LIMITS['Balanced']
        detailed = ModelPreview.QUALITY_LIMITS['Detailed']
        self.assertTrue(fast[0] < balanced[0] < detailed[0])
        self.assertTrue(fast[1] < balanced[1] < detailed[1])


if __name__ == '__main__':
    unittest.main()
