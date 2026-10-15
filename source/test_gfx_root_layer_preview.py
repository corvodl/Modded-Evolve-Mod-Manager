"""Synthetic regression for preview-only root-layer diagnostics and filtering."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from gfx_preview_backend import GfxError, render_gfx
from gfx_layout_backend import _raw, _records, _encode
from gfx_root_layer_preview import (root_layers, isolate_root_depth,
                                    likely_main_menu_dialog)
from gfx_synthetic_fixtures import (synthetic_gfx, _tag, _rect,
                                    _uint, _signed, _bitpack)


def two_root_layers(*, compressed=False):
    original = synthetic_gfx(sprites=False, compressed=compressed)
    raw = _raw(original)
    _, records = _records(raw)
    # Add another placement of the same shape at a distinct root depth.
    # This fixture is constructed from scratch; it contains no game data.
    second = struct.pack("<HH", 1, 5) + b"\x00"
    extra = struct.pack("<H", (4 << 6) | len(second))
    where = next(i for i, record in enumerate(records) if record[0] == 1)
    records.insert(where, (4, second, extra))
    return _encode(raw, records)


def synthetic_dialog_main_menu(*, recognized=True):
    """Fake dialog hierarchy with IDs/bounds from analysis, not game assets."""
    edges = [292.15, 1630.15, 25.55, 1030.55]
    if not recognized:
        edges[1] = 1500
    coords = [round(value * 20) for value in edges]
    bits = max(abs(v).bit_length() + 2 for v in coords)
    bounds = _bitpack(_uint(bits, 5) +
                      sum((_signed(v, bits) for v in coords), []))
    background = _tag(2, struct.pack("<H", 265) + bounds + b"\x00\x00\x00")
    nested = b""
    for depth, character in ((3, 265), (22, 205), (4, 221), (33, 192)):
        nested += _tag(26, b"\x06" + struct.pack("<HH", depth, character)
                       + b"\x00")
    nested += _tag(1) + _tag(0)
    sprite = _tag(39, struct.pack("<HH", 270, 1) + nested)
    place_root = _tag(26, b"\x06" + struct.pack("<HH", 74, 270) + b"\x00")
    content = background + sprite + place_root + _tag(1) + _tag(0)
    body = _rect(1920, 1080) + struct.pack("<HH", 24 << 8, 1) + content
    return b"GFX\x0f" + struct.pack("<I", 8 + len(body)) + body


class RootLayerIsolationTests(unittest.TestCase):
    def test_recognized_main_menu_dialog_is_not_assumed_in_other_movies(self):
        movie = synthetic_dialog_main_menu()
        found = likely_main_menu_dialog(movie, "Libs/UI/main_menu.gfx")
        self.assertIsNotNone(found)
        self.assertEqual((found.depth, found.character), (74, 270))
        self.assertIsNone(likely_main_menu_dialog(movie, "Libs/UI/options.gfx"))
        self.assertIsNone(likely_main_menu_dialog(
            synthetic_dialog_main_menu(recognized=False),
            "Libs/UI/main_menu.gfx"))
        filtered = isolate_root_depth(movie, mode="hide", depth=found.depth)
        self.assertFalse(root_layers(filtered))
        self.assertEqual(root_layers(movie)[0].character, 270)

    def test_root_layers_and_unmodified_movie(self):
        original = two_root_layers()
        before = bytes(original)
        items = root_layers(original)
        self.assertEqual([(x.depth, x.character) for x in items],
                         [(1, 1), (5, 1)])
        self.assertEqual(isolate_root_depth(original), before)
        self.assertEqual(original, before)
        self.assertEqual([layer.depth for layer in root_layers(original, frame=2)], [5])
        with self.assertRaisesRegex(GfxError, "outside"):
            root_layers(original, frame=9)

    def test_hide_isolate_removes_only_root_placement_tags(self):
        movie = two_root_layers()
        hidden = isolate_root_depth(movie, mode="hide", depth=5)
        isolated = isolate_root_depth(movie, mode="isolate", depth=5)
        self.assertEqual([x.depth for x in root_layers(hidden)], [1])
        self.assertEqual([x.depth for x in root_layers(isolated)], [5])
        # The geometry definition and all nested content are still present.
        # Filtering drops matching RemoveObject updates too; all
        # non-placement definitions and frame boundaries must stay intact.
        operations = {4, 5, 26, 28, 70}
        self.assertEqual(
            [code for code, _, _ in _records(_raw(movie))[1]
             if code not in operations],
            [code for code, _, _ in _records(_raw(isolated))[1]
             if code not in operations])
        self.assertEqual(root_layers(movie), root_layers(two_root_layers()))

    def test_filters_never_confuse_sprite_internal_layers_with_root(self):
        source = synthetic_gfx(sprites=True)
        visible = root_layers(source)
        self.assertEqual([(x.depth, x.character) for x in visible], [(1, 2)])
        filtered = isolate_root_depth(source, mode="hide", depth=1)
        self.assertFalse(root_layers(filtered))
        self.assertIn(39, [code for code, _, _ in _records(_raw(filtered))[1]])

    def test_missing_root_depth_and_wrong_mode_fail_closed(self):
        movie = two_root_layers(compressed=True)
        self.assertEqual([x.depth for x in root_layers(movie)], [1, 5])
        hidden = isolate_root_depth(movie, mode="hide", depth=5)
        self.assertEqual([x.depth for x in root_layers(hidden)], [1])
        for invalid in (None, True, 0, 9):
            with self.subTest(invalid=invalid):
                with self.assertRaises(GfxError):
                    isolate_root_depth(movie, mode="hide", depth=invalid)
        with self.assertRaisesRegex(GfxError, "visibility"):
            isolate_root_depth(movie, mode="unsupported", depth=1)
        self.assertEqual(isolate_root_depth(movie), movie)

    def test_ffdec_receives_only_filtered_render_copy(self):
        source = two_root_layers()
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ffdec-cli.exe"
            exe.write_bytes(b"mock")
            def mock_execute(args, *, cwd, cancelled, timeout):
                staged = Path(args[-1]).read_bytes()
                self.assertEqual([x.depth for x in root_layers(staged)], [1])
                Image.new("RGBA", (64, 48), (0, 0, 0, 0)).save(
                    Path(args[-2]) / "1.png")
            messages = []
            with patch("gfx_preview_backend._execute", side_effect=mock_execute):
                raster = render_gfx(
                    source, virtual="Libs/UI/test.gfx", backend=str(exe),
                    root_layer_mode="hide", root_layer_depth=5,
                    render_diagnostics=messages)
            self.assertTrue(raster.startswith(b"\x89PNG"))
            self.assertIn("Preview-only root hide", messages[0])
            self.assertEqual([x.depth for x in root_layers(source)], [1, 5])

    @unittest.skipUnless(os.getenv("EVOLVE_GFX_FFDEC"),
                         "Pinned FFDec available on Windows GFX CI")
    def test_real_ffdec_hiding_red_shape_yields_transparent_frame(self):
        import io
        movie = synthetic_gfx(sprites=False)
        exe = os.environ["EVOLVE_GFX_FFDEC"]
        show = render_gfx(movie, virtual="Libs/UI/test.gfx", backend=exe)
        blank = render_gfx(movie, virtual="Libs/UI/test.gfx", backend=exe,
                           root_layer_mode="hide", root_layer_depth=1)
        with Image.open(io.BytesIO(show)) as img:
            self.assertGreater(img.convert("RGBA").getpixel((24, 16))[0], 150)
        with Image.open(io.BytesIO(blank)) as img:
            self.assertEqual(img.convert("RGBA").getpixel((24, 16))[3], 0)


if __name__ == "__main__":
    unittest.main()
