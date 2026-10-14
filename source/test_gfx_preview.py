"""Scaleform GFX/CFX static preview tests, no proprietary movie payloads."""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from gfx_preview_backend import (
    inspect_gfx, resolve_imports, safe_import_path, render_gfx,
    export_gfx_png, ffdec_command, GfxError, _CACHE,
)
from gfx_synthetic_fixtures import synthetic_gfx


class GfxStaticPreviewTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()

    def test_gfx_movie_frames_sprite_dimensions_and_script_warnings(self):
        movie = synthetic_gfx(scripts=True, imports=True)
        self.assertEqual(movie[:3], b"GFX")
        info = inspect_gfx(movie)
        self.assertEqual((info.width, info.height, info.fps, info.frames),
                         (64, 48, 24, 2))
        self.assertEqual(info.sprites, ((2, 1),))
        self.assertTrue(info.scripts)
        self.assertEqual(info.root_placements, 1)
        self.assertIn("gfxfontlib_locfont.gfx", info.imports)
        self.assertTrue(any("ActionScript" in x for x in info.notes))

    def test_zero_frame_class_symbols_are_valid_but_not_renderable(self):
        movie = synthetic_gfx(
            scripts=True, sprites=True, nonvisual_sprite=True,
            exported_names=True)
        info = inspect_gfx(movie)
        self.assertEqual(info.frames, 2)
        self.assertEqual(dict(info.sprites), {2: 1, 3: 0})
        self.assertIn((2, "mcButtonMainMenu"), info.exports)
        self.assertTrue(any("nonvisual" in note for note in info.notes))
        # The zero-frame definition is not a valid render request.
        with tempfile.TemporaryDirectory() as tmp:
            ffdec = Path(tmp) / "ffdec-cli.exe"
            ffdec.write_bytes(b"mock")
            with self.assertRaisesRegex(GfxError, "unavailable"):
                render_gfx(movie, symbol=3, frame=1, backend=str(ffdec))

    def test_real_swf_movie_dependency_header_is_supported(self):
        from types import SimpleNamespace
        info = SimpleNamespace(imports=("effectsModeSelect.swf",))
        blob = synthetic_gfx()
        imported = b"FWS" + blob[3:]
        matched, errors = resolve_imports(
            info, "Libs/UI/main_menu.gfx",
            lambda name: imported if name == "Libs/UI/effectsModeSelect.swf" else None)
        self.assertEqual(matched, {"Libs/UI/effectsModeSelect.swf": imported})
        self.assertFalse(errors)
        _, errors = resolve_imports(
            info, "Libs/UI/main_menu.gfx",
            lambda _: imported[:-3])
        self.assertTrue(any("length" in warning for warning in errors))

    def test_cfx_zlib_movie_matches_uncompressed_metadata(self):
        a = inspect_gfx(synthetic_gfx())
        b = inspect_gfx(synthetic_gfx(compressed=True))
        self.assertEqual((a.width, a.height, a.frames, a.sprites),
                         (b.width, b.height, b.frames, b.sprites))
        self.assertEqual(b.compression, "CFX / zlib")

    def test_truncated_malformed_and_excessive_dimensions_fail_closed(self):
        sample = synthetic_gfx()
        for data in (sample[:6], sample[:-5], b"FWS" + sample[3:],
                     sample + b"\x00", b""):
            with self.subTest(data=data[:8]):
                with self.assertRaises(GfxError):
                    inspect_gfx(data)
        with self.assertRaises(GfxError):
            inspect_gfx(synthetic_gfx(width=8192, height=8192))
        with self.assertRaises(GfxError):
            inspect_gfx(synthetic_gfx(compressed=True)[:-2])

    def test_same_folder_import_resolution_rejects_urls_and_traversal(self):
        for value in ("https://example.com/malware.gfx", "../outside.gfx",
                      "/etc/movie.gfx", r"C:\Other\file.gfx"):
            with self.subTest(value=value):
                with self.assertRaises(GfxError):
                    safe_import_path(value, "Libs/UI/screen.gfx")
        self.assertEqual(
            safe_import_path("gfxfontlib_locfont.gfx", "Libs/UI/screen.gfx"),
            "Libs/UI/gfxfontlib_locfont.gfx")
        data = synthetic_gfx(imports=True)
        allowed = {"Libs/UI/gfxfontlib_locfont.gfx": synthetic_gfx()}
        info = inspect_gfx(data)
        found, missing = resolve_imports(info, "Libs/UI/screen.gfx",
                                         lambda name: allowed.get(name))
        self.assertEqual(set(found), set(allowed))
        self.assertFalse(missing)
        found, missing = resolve_imports(info, "Libs/UI/screen.gfx",
                                         lambda _: None)
        self.assertFalse(found)
        self.assertIn("matching-context", missing[0])

    def test_backend_not_configured_is_actionable(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(GfxError, "Configure FFDec 26.3.0"):
                ffdec_command()
        with self.assertRaisesRegex(GfxError, "missing or unsafe"):
            ffdec_command("/this/FFDEC/path/does/not/exist")
        with self.assertRaisesRegex(GfxError, "Select ffdec-cli.exe"):
            with tempfile.TemporaryDirectory() as tmp:
                arbitrary = Path(tmp) / "evil.jar"
                arbitrary.write_bytes(b"x")
                ffdec_command(arbitrary)

    def test_frame_and_symbol_bounds_and_pixel_limits(self):
        data = synthetic_gfx(width=128, height=96)
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "ffdec-cli.exe"
            tool.write_bytes(b"mock-binary")
            with self.assertRaisesRegex(GfxError, "unavailable"):
                render_gfx(data, frame=3, backend=str(tool))
            with self.assertRaisesRegex(GfxError, "unavailable"):
                render_gfx(data, symbol=123, backend=str(tool))
        huge = synthetic_gfx(width=4096, height=4096)
        with self.assertRaisesRegex(GfxError, "limit"):
            render_gfx(huge, frame=1, scale=2)

    def test_rendered_png_from_backend_is_not_viewer_screenshot(self):
        source = synthetic_gfx()
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "ffdec-cli.exe"
            tool.write_bytes(b"mock-binary")
            def fake_ffdec(args, *, cwd, cancelled, timeout):
                output = Path(args[-2]) / "1.png"
                background = (255, 0, 0, 0) if "-ignorebackground" in args else (
                    255, 0, 0, 255)
                Image.new("RGBA", (64, 48), background).save(output)
            with patch("gfx_preview_backend._execute", side_effect=fake_ffdec) as invoke:
                png = render_gfx(source, backend=str(tool),
                                 build_context="stage2-A")
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual(png, render_gfx(source, backend=str(tool),
                                                build_context="stage2-A"))
                self.assertEqual(invoke.call_count, 1)
                render_gfx(source, backend=str(tool), build_context="stage2-B")
                self.assertEqual(invoke.call_count, 2)
                render_gfx(source, backend=str(tool),
                           build_context="stage2-A",
                           imports={"Libs/UI/font.gfx": source})
                self.assertEqual(invoke.call_count, 3)
            target = Path(tmp) / "exported.png"
            original_digest = hashlib.sha256(source).hexdigest()
            export_gfx_png(png, target)
            self.assertEqual(hashlib.sha256(source).hexdigest(), original_digest)
            with Image.open(target) as image:
                self.assertEqual(image.size, (64, 48))
                self.assertEqual(image.getpixel((25, 25))[3], 0)
            with self.assertRaises(FileExistsError):
                export_gfx_png(png, target)

    @unittest.skipUnless(os.environ.get("EVOLVE_GFX_FFDEC"),
                         "Pinned FFDec executable unavailable; use dedicated GFX CI workflow")
    def test_real_ffdec_renders_red_vector_and_transparent_second_frame(self):
        configured = os.environ["EVOLVE_GFX_FFDEC"]
        movie = synthetic_gfx()
        image_data = render_gfx(movie, virtual="Libs/UI/test shape.gfx",
                                backend=configured, frame=1)
        with Image.open(io.BytesIO(image_data)) as img:
            self.assertEqual(img.size, (64, 48))
            rgba = img.convert("RGBA")
            self.assertGreater(rgba.getpixel((30, 20))[0], 200)
            self.assertLess(rgba.getpixel((30, 20))[1], 50)
        blank_data = render_gfx(movie, virtual="Libs/UI/test shape.gfx",
                                backend=configured, frame=2)
        with Image.open(io.BytesIO(blank_data)) as img:
            self.assertEqual(img.size, (64, 48))
            self.assertEqual(img.convert("RGBA").getpixel((30, 20))[3], 0)
        sprite = render_gfx(movie, virtual="Libs/UI/test shape.gfx",
                            backend=configured, symbol=2, frame=1)
        with Image.open(io.BytesIO(sprite)) as img:
            self.assertGreater(img.convert("RGBA").getpixel((30, 20))[0], 200)


if __name__ == "__main__":
    unittest.main()
