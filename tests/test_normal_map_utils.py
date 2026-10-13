import unittest
from normal_map_utils import NormalMap, is_normal_texture, decode_xy_normal_rgba, choose_highest_resolution


class NormalMapTests(unittest.TestCase):
    def test_cryengine_names(self):
        self.assertTrue(is_normal_texture("characters/rock_ddn.dds.0"))
        self.assertTrue(is_normal_texture("rock_ddn.dds"))
        self.assertFalse(is_normal_texture("rock_diff.dds"))

    def test_flat_normal(self):
        result = decode_xy_normal_rgba(1, 1, bytes((128, 128, 0, 255)))
        self.assertGreater(result.rgba[2], 250)
        self.assertEqual(result.rgba[3], 255)

    def test_dxt5nm_alpha_x(self):
        result = decode_xy_normal_rgba(1, 1, bytes((0, 128, 0, 255)),
                                       x_channel=3, y_channel=1)
        self.assertGreater(result.rgba[0], 200)

    def test_invalid_pixels(self):
        with self.assertRaises(ValueError):
            decode_xy_normal_rgba(2, 2, bytes(4))

    def test_largest_mip(self):
        low = NormalMap(1, 1, bytes(4))
        high = NormalMap(4, 4, bytes(64))
        self.assertIs(choose_highest_resolution([low, high]), high)


if __name__ == "__main__":
    unittest.main()
