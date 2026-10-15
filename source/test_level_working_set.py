"""25,000-placement scene spatial selection and hysteresis regressions."""
import unittest
from types import SimpleNamespace

from level_working_set import PlacementGrid


def placed(i, x, y, z=0):
    return SimpleNamespace(
        model='mesh' if i % 13 else '',
        status='visible',
        kind='Brush',
        world=SimpleNamespace(pos=(float(x), float(y), float(z))),
        asset_key='variant-' + str(i))


class PlacementGridTests(unittest.TestCase):
    def test_large_scene_finds_local_items_without_losing_logical_placements(self):
        # Keep the entire placement inventory immutable, not a 25k-row cap.
        scene = tuple(placed(i, i % 250 * 100, i // 250 * 100)
                      for i in range(25000))
        index = PlacementGrid(scene, lambda item: item.asset_key,
                              cell_size=250.)
        self.assertEqual(index.total, 25000 - (25000 + 12) // 13)
        self.assertGreater(len(index.cells), 1000)
        near = index.nearby((100., 100., 0.), 360.)
        self.assertTrue(0 < len(near) < 150)
        self.assertIn('variant-1', near)
        self.assertNotIn('variant-10000', near)
        # The source remains complete for searching, transforms and exports.
        self.assertEqual(len(scene), 25000)

    def test_hysteresis_retains_old_resource_without_far_world_scan(self):
        a = placed(1, 0, 0)
        b = placed(2, 110, 0)
        distant = placed(3, 1500, 0)
        grid = PlacementGrid([a, b, distant], lambda item: item.asset_key,
                             cell_size=64)
        initial = grid.nearby((0, 0, 0), 100)
        self.assertEqual(set(initial), {'variant-1'})
        retained = grid.nearby((0, 0, 0), 100,
                               retain=['variant-2'], margin=1.2)
        self.assertEqual(set(retained), {'variant-1', 'variant-2'})
        self.assertNotIn('variant-3', retained)

    def test_group_multiple_placements_by_closest_instance_and_facing(self):
        left = placed(1, 100, 0)
        right = placed(2, -100, 0)
        right.asset_key = left.asset_key
        grid = PlacementGrid((left, right), lambda item: item.asset_key, cell_size=64)
        nearby = grid.nearby((0, 0, 0), 120,
                             eye=(0, 0, 0), forward=(1, 0, 0))
        self.assertEqual(len(nearby), 1)
        self.assertEqual(nearby['variant-1'], (10000, True))

    def test_empty_and_invalid_index(self):
        grid = PlacementGrid((), lambda item: item.asset_key)
        self.assertEqual(grid.nearby((0, 0, 0), 50), {})
        with self.assertRaises(ValueError):
            PlacementGrid((), lambda item: item.asset_key, cell_size=0)


if __name__ == '__main__':
    unittest.main()
