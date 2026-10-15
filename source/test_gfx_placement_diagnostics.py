"""Synthetic Scaleform atlas, shape and first-frame placement diagnostics."""
from __future__ import annotations

import struct
import unittest

from gfx_synthetic_fixtures import _tag, _rect
from gfx_placement_diagnostics import inspect_bitmap_placement


def synthetic_atlas(*, placed=True, atlas=(4, 6, 20, 24), image_id=0,
                    with_shape=True):
    path = b"test_I9.tif"
    bitmap_record = struct.pack("<6H", 9, image_id, *atlas)
    image_record = (struct.pack("<5H", image_id, 9, 19, 32, 32)
                    + b"\x00" + bytes([len(path)]) + path + b"\x00")
    # Shape #10 uses a clipped bitmap FillStyle with bitmap character #9.
    shape = (struct.pack("<H", 10) + _rect(32, 32)
             + b"\x01\x41" + struct.pack("<H", 9) + b"\x00")
    nested = _tag(26, b"\x06" + struct.pack("<HH", 1, 10) + b"\x00")
    sprite = _tag(39, struct.pack("<HH", 13, 1) +
                  nested + _tag(1) + _tag(0))
    root = (_tag(26, b"\x06" + struct.pack("<HH", 1, 13) + b"\x00")
            if placed else b"")
    content = (_tag(1009, image_record) + _tag(1008, bitmap_record)
               + (_tag(2, shape) if with_shape else b"") +
               sprite + root + _tag(1) + _tag(0))
    body = _rect(64, 48) + struct.pack("<HH", 24 << 8, 1) + content
    return b"GFX\x0f" + struct.pack("<I", len(body) + 8) + body


class PlacementDiagnosticsTests(unittest.TestCase):
    def test_image_character_atlas_shape_sprite_root_chain(self):
        report = inspect_bitmap_placement(
            synthetic_atlas(), "Libs/UI/test.gfx",
            ["Libs/UI/test_I9.dds.0"])
        self.assertEqual(report.images, 1)
        self.assertEqual(len(report.movies), 1)
        binding, = report.movies
        self.assertEqual((binding.bitmap_character, binding.atlas_id), (9, 0))
        self.assertEqual(binding.texture, "Libs/UI/test_I9.dds.0")
        self.assertEqual(binding.logical_size, (32, 32))
        self.assertEqual(binding.atlas_rectangle, (4, 6, 20, 24))
        self.assertEqual(binding.shape_ids, (10,))
        self.assertTrue(binding.static_first_frame)
        self.assertTrue(binding.file_found)

    def test_no_root_placement_or_missing_texture_does_not_claim_visibility(self):
        report = inspect_bitmap_placement(
            synthetic_atlas(placed=False), "Libs/UI/test.gfx")
        self.assertFalse(report.movies[0].static_first_frame)
        self.assertFalse(report.movies[0].file_found)
        self.assertIn("No root first-frame placements", report.messages[0])

    def test_out_of_bounds_atlas_is_rejected(self):
        report = inspect_bitmap_placement(
            synthetic_atlas(atlas=(0, 0, 33, 32)),
            "Libs/UI/test.gfx", ["Libs/UI/test_I9.dds.0"])
        self.assertEqual(report.movies, ())
        self.assertTrue(any("invalid atlas rectangle" in x
                            for x in report.messages))

    def test_unrecognized_shape_fill_is_explicit(self):
        report = inspect_bitmap_placement(
            synthetic_atlas(with_shape=False), "Libs/UI/test.gfx")
        self.assertEqual(report.movies[0].shape_ids, ())
        self.assertFalse(report.movies[0].static_first_frame)

    def test_whole_dds_fallback_stays_in_same_build(self):
        report = inspect_bitmap_placement(
            synthetic_atlas(), "Libs/UI/test.gfx",
            ["Libs/UI/test_I9.dds"])
        self.assertTrue(report.movies[0].file_found)
        self.assertEqual(report.movies[0].texture, "Libs/UI/test_I9.dds")


if __name__ == "__main__":
    unittest.main()
