import unittest
import struct
import tempfile
from pathlib import Path
from normal_map_utils import NormalMap, is_normal_texture, decode_xy_normal_rgba, choose_highest_resolution, encode_normal_png, export_normal_png, inspect_dds_header, decode_bc5_normal, decode_bc5_dds


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

    def test_png_header_and_dimensions(self):
        normal = NormalMap(2, 1, bytes((128, 128, 255, 255)) * 2)
        png = encode_normal_png(normal)
        self.assertEqual(png[:8], bytes((137, 80, 78, 71, 13, 10, 26, 10)))
        self.assertEqual(struct.unpack(">II", png[16:24]), (2, 1))

    def test_export_folder_and_sanitization(self):
        with tempfile.TemporaryDirectory() as temp:
            normal = NormalMap(1, 1, bytes((128, 128, 255, 255)))
            path = export_normal_png(normal, Path(temp), "goliath")
            self.assertEqual(path.parent.name, "textures")
            self.assertTrue(path.is_file())
            with self.assertRaises(ValueError):
                export_normal_png(normal, Path(temp), "../bad")

    def test_dds_bc5_header(self):
        header = bytearray(128)
        header[:4] = b"DDS "
        struct.pack_into("<I", header, 4, 124)
        struct.pack_into("<II", header, 12, 1024, 2048)
        struct.pack_into("<I", header, 28, 11)
        struct.pack_into("<I", header, 76, 32)
        header[84:88] = b"ATI2"
        result = inspect_dds_header(bytes(header))
        self.assertEqual((result.width, result.height), (2048, 1024))
        self.assertEqual((result.x_channel, result.y_channel), (0, 1))

    def test_dds_dxt5_needs_swizzle_evidence(self):
        header = bytearray(128)
        header[:4] = b"DDS "
        struct.pack_into("<I", header, 4, 124)
        struct.pack_into("<II", header, 12, 4, 4)
        struct.pack_into("<I", header, 76, 32)
        header[84:88] = b"DXT5"
        self.assertIsNone(inspect_dds_header(bytes(header)).x_channel)

    def test_bc5_flat_normal_block(self):
        red = bytes((128, 128)) + bytes(6)
        green = bytes((128, 128)) + bytes(6)
        result = decode_bc5_normal(4, 4, red + green)
        self.assertEqual((result.width, result.height), (4, 4))
        self.assertTrue(all(result.rgba[i + 2] >= 250 for i in range(0, 64, 4)))

    def test_bc5_incomplete_mip_rejected(self):
        with self.assertRaises(ValueError):
            decode_bc5_normal(4, 4, bytes(15))

    def test_largest_mip(self):
        low = NormalMap(1, 1, bytes(4))
        high = NormalMap(4, 4, bytes(64))
        self.assertIs(choose_highest_resolution([low, high]), high)


if __name__ == "__main__":
    unittest.main()
