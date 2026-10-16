"""User-visible map frame markers must be emitted once per scene generation."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import time

from qt_level_scene_render import LevelGLViewport


class MapFrameMilestoneTests(TestCase):
    def test_geometry_texture_and_final_frames_report_independently(self):
        image = object()
        instance = SimpleNamespace(model='objects/test.cgf', material='map.mtl')
        asset = SimpleNamespace(materials=SimpleNamespace(textures=(image,)))
        viewport = SimpleNamespace(
            _map_frame_started_at=time.perf_counter() - 0.5,
            _geometry_frame_seen=False, _texture_frame_seen=False,
            _final_frame_seen=False,
            _visible=(instance,), terrain_chunks=(), _terrain_texture=0,
            _textures={id(image):41}, _residency=object(),
            _preparing=False, _streaming=False,
            assets={('objects/test.cgf','map.mtl'):asset})
        with patch('qt_level_scene_render.record') as record:
            LevelGLViewport._frame_presented(viewport)
            LevelGLViewport._frame_presented(viewport)
        self.assertEqual([c.args[1] for c in record.call_args_list],
                         ['first_geometry_frame','first_texture_frame',
                          'final_quality_frame'])
        self.assertTrue(viewport._final_frame_seen)

    def test_incomplete_texture_upload_is_not_final_quality(self):
        image = object()
        instance = SimpleNamespace(model='objects/test.cgf', material='map.mtl')
        asset = SimpleNamespace(materials=SimpleNamespace(textures=(image,)))
        viewport = SimpleNamespace(
            _map_frame_started_at=time.perf_counter(),
            _geometry_frame_seen=False, _texture_frame_seen=False,
            _final_frame_seen=False,
            _visible=(instance,), terrain_chunks=(), _terrain_texture=0,
            _textures={}, _residency=object(),
            _preparing=False, _streaming=False,
            assets={('objects/test.cgf','map.mtl'):asset})
        with patch('qt_level_scene_render.record') as record:
            LevelGLViewport._frame_presented(viewport)
        self.assertEqual([c.args[1] for c in record.call_args_list],
                         ['first_geometry_frame'])
        self.assertFalse(viewport._final_frame_seen)


if __name__ == '__main__':
    import unittest
    unittest.main()
