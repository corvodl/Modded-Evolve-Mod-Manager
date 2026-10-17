"""Geometry stage must arrive before slow material/animation lookup."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from async_model_resolve import resolve_model


class GeometryStageTests(TestCase):
    def _prepare(self, *, cancel_after_stage=False):
        order = []
        cancelled = [False]
        mesh = SimpleNamespace(source_name='hero.skinm')
        path = SimpleNamespace(read_bytes=lambda: b'CrChF fixture')
        def geometry(_mesh, shading=None):
            self.assertEqual(shading, 'smooth')
            order.append('geometry')
            return 'verified-arrays'
        def stage(payload):
            order.append('stage')
            self.assertIs(payload['mesh'], mesh)
            self.assertEqual(payload['geometry'], 'verified-arrays')
            if cancel_after_stage:
                cancelled[0] = True
        def materials(*_args, **_kw):
            order.append('materials')
            return SimpleNamespace(textures=(), normal_maps=(), specular_maps=())
        def animations(*_args, **_kw):
            order.append('animations')
            return ((), None, None)
        with patch('async_model_resolve.checked_path', return_value=path), \
             patch('async_model_resolve.inspect_model',
                   return_value=SimpleNamespace(description='Model')), \
             patch('async_model_resolve.assemble_character', return_value=None), \
             patch('async_model_resolve.find_preview_mesh', return_value=mesh), \
             patch('async_model_resolve.prepare_geometry', side_effect=geometry), \
             patch('async_model_resolve.load_preview_materials', side_effect=materials), \
             patch('async_model_resolve.discover_animations', side_effect=animations):
            if cancel_after_stage:
                with self.assertRaises(InterruptedError):
                    resolve_model('/fixture', 'hero.skinm', index={},
                                  geometry_ready=stage,
                                  cancel=lambda: cancelled[0])
            else:
                result = resolve_model('/fixture', 'hero.skinm', index={},
                                       geometry_ready=stage)
                self.assertEqual(result['geometry'], 'verified-arrays')
        return order

    def test_stage_before_materials_and_animations(self):
        self.assertEqual(self._prepare(),
                         ['geometry', 'stage', 'materials', 'animations'])

    def test_cancel_after_stage_skips_remaining_cpu_work(self):
        self.assertEqual(self._prepare(cancel_after_stage=True),
                         ['geometry', 'stage'])


    def test_gpu_panel_defaults_smooth_but_faceted_remains_available(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from qt_gpu_viewer import QtGPUModelPanel
        from model_preview import read_preview_mesh
        from test_material_preview import uv_mesh

        app = QApplication.instance() or QApplication([])
        mesh = read_preview_mesh(uv_mesh(), 'test.skinm')
        panel = QtGPUModelPanel(None, mesh)
        try:
            self.assertEqual(panel.canvas.shading, 'smooth')
            self.assertEqual(panel.shading_combo.currentText(), 'Smooth')
            self.assertEqual(len(panel.canvas.geometry.vertices),
                             len(mesh.vertices))
            panel.shading_combo.setCurrentText('Faceted (hard edges)')
            self.assertEqual(panel.canvas.shading, 'faceted')
            self.assertEqual(len(panel.canvas.geometry.vertices),
                             len(mesh.triangles) * 3)
            panel.shading_combo.setCurrentText('Smooth')
            self.assertEqual(panel.canvas.shading, 'smooth')
        finally:
            panel.close()

    def test_prepared_faceted_mesh_is_not_mislabeled_smooth(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from qt_gpu_viewer import QtGLModelCanvas
        from gpu_model_preview import prepare_geometry
        from model_preview import read_preview_mesh
        from test_material_preview import uv_mesh

        app = QApplication.instance() or QApplication([])
        mesh = read_preview_mesh(uv_mesh())
        old_faceted = prepare_geometry(mesh, shading='faceted')
        canvas = QtGLModelCanvas(mesh, prepared_geometry=old_faceted)
        try:
            self.assertEqual(canvas.shading, 'smooth')
            self.assertEqual(len(canvas.geometry.vertices), len(mesh.vertices))
        finally:
            canvas.close()


if __name__ == '__main__':
    import unittest
    unittest.main()
