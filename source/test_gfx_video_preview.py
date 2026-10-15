"""Synthetic-only tests for Scaleform video handling and dependency provenance."""
from __future__ import annotations

import struct
import unittest
import zlib

from gfx_synthetic_fixtures import synthetic_gfx, _tag
from gfx_preview_backend import inspect_gfx, _decompress_limited
from gfx_placement_diagnostics import _root_tags, _first_frame_symbols
from gfx_video_preview import video_streams, hide_video_placeholders
from gfx_dependency_diagnostics import dependency_report


def video_fixture(*, compressed=False, nested=False):
    sample = synthetic_gfx(sprites=False)
    nbits = sample[8] >> 3
    offset = 8 + (5 + 4 * nbits + 7) // 8 + 4
    stream = _tag(60, struct.pack("<HHHHBB", 237, 10, 640, 360, 0, 2))
    placed = _tag(4, struct.pack("<HH", 237, 9) + b"\x00")
    if nested:
        # A root sprite contains the video; its display-list must be safe too.
        movie = _tag(39, struct.pack("<HH", 33, 1) +
                     placed + _tag(1) + _tag(0))
        placed = _tag(4, struct.pack("<HH", 33, 7) + b"\x00")
        stream += movie
    raw = bytearray(sample[:offset] + stream + placed + sample[offset:])
    struct.pack_into("<I", raw, 4, len(raw))
    if compressed:
        return b"CFX" + raw[3:8] + zlib.compress(raw[8:])
    return bytes(raw)


class GfxVideoPreviewTests(unittest.TestCase):
    def test_video_streams_are_reported_without_guessing_artwork(self):
        original = video_fixture()
        self.assertEqual(video_streams(original)[0].character_id, 237)
        self.assertEqual((video_streams(original)[0].width,
                          video_streams(original)[0].height), (640, 360))
        rows = dependency_report(original, "Libs/UI/main_menu.gfx")
        self.assertEqual(len([r for r in rows if r.kind == "Video"]), 1)
        self.assertIn("placeholder hidden", rows[-1].status)

    def test_render_only_hide_omits_video_but_keeps_other_shapes(self):
        for compressed in (False, True):
            for nested in (False, True):
                with self.subTest(compressed=compressed, nested=nested):
                    original = video_fixture(compressed=compressed, nested=nested)
                    filtered, removed = hide_video_placeholders(original)
                    self.assertEqual(removed, 1)
                    self.assertEqual(inspect_gfx(filtered).frames, 2)
                    self.assertNotEqual(filtered, original)
                    self.assertNotIn(237, _first_frame_symbols(_root_tags(filtered)))
                    self.assertTrue(any(code == 60 for code, _ in _root_tags(filtered)))
                    self.assertTrue(any(code == 2 for code, _ in _root_tags(filtered)))

    def test_no_video_is_bitwise_unchanged(self):
        for compressed in (False, True):
            original = synthetic_gfx(compressed=compressed)
            self.assertEqual(hide_video_placeholders(original), (original, 0))

    def test_import_and_embedded_font_provenance(self):
        original = synthetic_gfx(imports=True)
        imports = {"Libs/UI/gfxfontlib_locfont.gfx": b"movie"}
        rows = dependency_report(
            original, "Libs/UI/main_menu.gfx",
            resolved_imports=imports,
            available_paths=("Libs/UI/gfxfontlib_locfont.gfx",))
        found = [r for r in rows if r.kind == "Font / movie"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "Resolved")
        rows = dependency_report(
            original, "Libs/UI/main_menu.gfx", resolved_imports={})
        self.assertIn("Missing", rows[0].status)


if __name__ == "__main__":
    unittest.main()
