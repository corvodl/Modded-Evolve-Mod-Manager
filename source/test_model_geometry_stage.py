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


if __name__ == '__main__':
    import unittest
    unittest.main()
