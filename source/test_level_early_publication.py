"""A verified logical map must reach the GUI before optional terrain work."""
import unittest
from types import SimpleNamespace
from threading import Event
from unittest.mock import patch

from qt_level_scene_render import prepare_scene


class EarlyMapPublicationTests(unittest.TestCase):
    def test_scene_publishes_before_optional_terrain_atlas_decode(self):
        calls = []
        object_position = SimpleNamespace(
            model='', status='placed',
            world=SimpleNamespace(pos=(2., 4., 6.)))
        scene = (object_position,)
        project = SimpleNamespace(workspace='/tmp/no-real-files',
                                  level_name='fixture', scene=scene)
        def partial(assets, issues, details):
            calls.append(('partial', details))
            self.assertEqual(details['scene'], scene)
            self.assertTrue(details['pending'])
            self.assertEqual(assets, {})
        def atlas(*_args, **_kwargs):
            calls.append(('atlas', None))
            return None
        with patch('level_asset_mount.mount_map_index',
                   return_value=({}, {'mounts':0})), \
             patch('level_map_assembly.assemble_map_scene',
                   return_value=(scene, ())), \
             patch('level_asset_mount.resolve_scoped',
                   return_value=(None, 'missing')), \
             patch('level_terrain_tiles.load_tile_atlas',
                   side_effect=atlas):
            assets, issues, final = prepare_scene(
                project, Event(), preview=False, partial=partial)
        self.assertEqual([x[0] for x in calls], ['partial', 'atlas'])
        self.assertFalse(final.get('pending', False))
        self.assertEqual(final['scene'], scene)
        self.assertEqual(assets, {})

    def test_cancelled_scene_never_emits_partial(self):
        project = SimpleNamespace(workspace='/tmp/no-real-files',
                                  level_name='fixture', scene=())
        cancelled = Event()
        cancelled.set()
        with patch('level_asset_mount.mount_map_index') as index:
            result = prepare_scene(project, cancelled, preview=False,
                                   partial=lambda *_: self.fail('published'))
        self.assertIsNone(result)
        index.assert_not_called()


if __name__ == '__main__':
    unittest.main()
