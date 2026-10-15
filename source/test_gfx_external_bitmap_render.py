"""Regression tests for render-only Scaleform external DDS bitmap materialization.

Uses fabricated GFX records/images, never game files. CI's pinned FFDec
integration tests verify pixels and timeline placement in the actual renderer.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from gfx_preview_backend import (GfxError, _CACHE, render_gfx)
from gfx_placement_diagnostics import _root_tags
from gfx_external_bitmap_render import prepare_textured_gfx
from gfx_synthetic_fixtures import (
    _tag, _rect, _uint, _signed, _bitpack, synthetic_gfx
)


def _matrix(dx=0, dy=0):
    if not dx and not dy:
        return b"\x00"
    x, y = dx * 20, dy * 20
    bits = max(abs(x).bit_length() + 1, abs(y).bit_length() + 1, 2)
    return _bitpack([0, 0] + _uint(bits, 5) + _signed(x, bits) +
                    _signed(y, bits))


def synthetic_compiled_bitmap(*, offset=(0, 0), sprite=False, alpha=True,
                              full_atlas=True):
    # Replaces a redistributable DefineShape solid color with one bitmap
    # FillStyle ID #9 and matrix identity. The existing shape edge records,
    # root show-frame, alpha and placement semantics stay intact.
    base = synthetic_gfx(sprites=sprite, width=48, height=36)
    tags = []
    for code, payload in _root_tags(base):
        if code == 2:
            pos = 2
            pos += (5 + 4 * (payload[pos] >> 3) + 7) // 8
            old_style = b"\x01\x00\xff\x00\x00\x00"
            assert payload[pos:pos + len(old_style)] == old_style
            payload = (payload[:pos] + b"\x01\x41\x09\x00\x00\x00" +
                       payload[pos + len(old_style):])
        elif code == 4 and not sprite:
            assert payload[:4] == struct.pack("<HH", 1, 1)
            payload = payload[:4] + _matrix(*offset)
        tags.append(_tag(code, payload))
    # An image index 0 with a 16x16 rectangle inside a 24x24 DDS.
    name = b"test_I9.tif"
    image = (struct.pack("<5H", 0, 9, 19, 24, 24) + b"\x00" +
             bytes([len(name)]) + name + b"\x00")
    crop = struct.pack("<6H", 9, 0, 0, 0, 16, 16)
    base_header = base[:8 + len(_rect(48, 36)) + 4]
    body = (base_header[8:] + _tag(1009, image) + _tag(1008, crop) +
            b"".join(tags))
    return b"GFX" + base[3:4] + struct.pack("<I", len(body) + 8) + body


class ExternalBitmapRenderTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()
        self.original = synthetic_compiled_bitmap()
        self.paths = ("Libs/UI/test_I9.dds.0",)
        self.calls = []

    def image(self, path):
        self.calls.append(path)
        # Verify exact cropped pixels, not a colored overlaid square.
        im = Image.new("RGBA", (24, 24), (255, 0, 0, 255))
        for y in range(16):
            for x in range(16):
                im.putpixel((x, y), (0, 245, 35, 255))
        return im

    def test_compiled_atlas_becomes_lossless_character_not_original_edit(self):
        source = self.original
        digest = hashlib.sha256(source).hexdigest()
        transformed = prepare_textured_gfx(
            source, "Libs/UI/test.gfx", self.paths, self.image)
        self.assertEqual(transformed.converted, 1)
        self.assertEqual(transformed.skipped, 0)
        self.assertIn(36, [tag for tag, _ in _root_tags(transformed.movie)])
        self.assertNotIn(1008, [tag for tag, _ in _root_tags(transformed.movie)])
        self.assertIn(1009, [tag for tag, _ in _root_tags(transformed.movie)])
        self.assertEqual(self.calls, [self.paths[0]])
        self.assertEqual(hashlib.sha256(source).hexdigest(), digest)
        payload = next(x for code, x in _root_tags(transformed.movie) if code == 36)
        self.assertEqual(struct.unpack_from("<HBHH", payload), (9, 5, 16, 16))
        argb = __import__("zlib").decompress(payload[7:])
        self.assertEqual(argb[:4], bytes((255, 0, 245, 35)))

    def test_missing_wrong_dimensions_duplicate_and_cancel_are_safe(self):
        result = prepare_textured_gfx(
            self.original, "Libs/UI/test.gfx", (),
            self.image)
        self.assertEqual(result.converted, 0)
        self.assertEqual(result.movie, self.original)
        self.assertEqual(self.calls, [])
        bad = prepare_textured_gfx(
            self.original, "Libs/UI/test.gfx", self.paths,
            lambda _: Image.new("RGB", (8, 8)))
        self.assertEqual(bad.converted, 0)
        self.assertTrue(any("Atlas" in x for x in bad.diagnostics))
        with self.assertRaises(InterruptedError):
            prepare_textured_gfx(
                self.original, "Libs/UI/test.gfx", self.paths, self.image,
                cancelled=lambda: True)
        with self.assertRaisesRegex(GfxError, "budget"):
            prepare_textured_gfx(
                self.original, "Libs/UI/test.gfx", self.paths, self.image,
                max_bitmaps=200)
        self.assertEqual(prepare_textured_gfx(
            synthetic_gfx(), "Libs/UI/test.gfx", self.paths, self.image).converted, 0)

    def test_ffdec_stages_only_synthetic_raster_movie_and_preserves_alpha(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "ffdec-cli.exe"
            tool.write_bytes(b"fake")
            saved = []
            def fake_ffdec(args, *, cwd, cancelled, timeout):
                movie = Path(args[-1]).read_bytes()
                saved.append(movie)
                self.assertIn(36, [tag for tag, _ in _root_tags(movie)])
                Image.new("RGBA", (48, 36), (0, 255, 0, 0)).save(
                    Path(args[-2]) / "1.png")
            notes = []
            with patch("gfx_preview_backend._execute", side_effect=fake_ffdec):
                result = render_gfx(
                    self.original, virtual="Libs/UI/test.gfx", backend=str(tool),
                    available_paths=self.paths, texture_loader=self.image,
                    render_diagnostics=notes)
            self.assertTrue(notes)
            self.assertNotEqual(saved[0], self.original)
            self.assertEqual(self.original[:3], b"GFX")
            with Image.open(BytesIO(result)) as im:
                self.assertEqual(im.getpixel((6, 8))[3], 0)

    @unittest.skipUnless(os.environ.get("EVOLVE_GFX_FFDEC"),
                         "Pinned FFDec available only in GFX Windows CI")
    def test_real_ffdec_respects_original_bitmap_fill_and_translated_position(self):
        ffdec = os.environ["EVOLVE_GFX_FFDEC"]
        movie = synthetic_compiled_bitmap(offset=(10, 5))
        raw = render_gfx(movie, virtual="Libs/UI/test.gfx",
                         backend=ffdec, frame=1)
        mapped = render_gfx(
            movie, virtual="Libs/UI/test.gfx", backend=ffdec,
            frame=1, available_paths=self.paths, texture_loader=self.image)
        with Image.open(BytesIO(mapped)) as im:
            rgba = im.convert("RGBA")
            # The original DefineShape and PlaceObject positioning survive
            # unchanged. Only the bitmap ID is made readable by FFDec.
            colored = rgba.getpixel((12, 8))
            self.assertGreater(colored[1], colored[0] + 80)
            self.assertGreater(colored[3], 150)
            self.assertEqual(rgba.getpixel((1, 1))[3], 0)
        self.assertNotEqual(raw, mapped)


if __name__ == "__main__":
    unittest.main()
