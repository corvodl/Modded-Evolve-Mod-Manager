"""GFX external movie imports never cross registered game build boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from asset_library import _invalidate
from asset_library_preview import gfx_movie, library_gfx_imports
from build_collections import register_build, assign_archive
from gfx_synthetic_fixtures import synthetic_gfx


class GfxBuildScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.lib = self.home / "AssetLibrary"
        self.lib.mkdir()
        self.a = register_build("Stage 2 Build A", "stage2", root=self.lib)
        self.b = register_build("Legacy Build B", "legacy", root=self.lib)
        self.movie = synthetic_gfx(imports=True)
        self.font_a = synthetic_gfx(width=40, height=40)
        self.font_b = synthetic_gfx(width=24, height=24)
        self.source = "a" * 20
        self.font_owner = "b" * 20
        self.external_font_owner = "c" * 20
        self._archive(self.a, self.source,
                      {"Libs/UI/watermark.gfx": self.movie})
        self._archive(self.a, self.font_owner,
                      {"Libs/UI/gfxfontlib_locfont.gfx": self.font_a})
        self._archive(self.b, self.external_font_owner,
                      {"Libs/UI/gfxfontlib_locfont.gfx": self.font_b})
        self.addCleanup(_invalidate)

    def _archive(self, build, identifier, files):
        folder = self.lib / identifier
        for path, data in files.items():
            dest = folder / "files" / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        manifest = {
            "version": 1, "name": identifier + ".pak",
            "source": "/fixture/" + identifier + ".pak",
            "source_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
            "entries": [{"path": path, "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()}
                        for path, data in files.items()],
            "extracted_bytes": sum(len(data) for data in files.values()),
        }
        (folder / "asset-library.json").write_text(json.dumps(manifest))
        assign_archive(build, identifier, root=self.lib)
        _invalidate()

    def test_external_font_comes_only_from_selected_build(self):
        info = gfx_movie(self.source, "Libs/UI/watermark.gfx", root=self.lib)
        self.assertEqual(info.imports, ("gfxfontlib_locfont.gfx",))
        found, missing, context = library_gfx_imports(
            self.source, "Libs/UI/watermark.gfx", root=self.lib, build_id=self.a)
        self.assertEqual(context, self.a)
        self.assertFalse(missing)
        self.assertEqual(found, {"Libs/UI/gfxfontlib_locfont.gfx": self.font_a})
        self.assertNotIn(self.font_b, found.values())
        self.assertEqual(
            (self.lib / self.source / "files" / "Libs/UI/watermark.gfx").read_bytes(),
            self.movie)

    def test_flashassets_font_import_resolves_only_in_selected_build(self):
        from build_collections import remove_membership
        remove_membership(self.a, self.font_owner, root=self.lib)
        self._archive(self.a, "d" * 20,
                      {"Libs/UI/FlashAssets/gfxfontlib_locfont.gfx": self.font_a})
        found, missing, chosen = library_gfx_imports(
            self.source, "Libs/UI/watermark.gfx", root=self.lib, build_id=self.a)
        self.assertFalse(missing)
        self.assertEqual(chosen, self.a)
        self.assertEqual(
            found, {"Libs/UI/gfxfontlib_locfont.gfx": self.font_a})
        self.assertNotIn(self.font_b, found.values())

    def test_nested_ambiguous_font_import_is_rejected(self):
        from build_collections import remove_membership
        remove_membership(self.a, self.font_owner, root=self.lib)
        self._archive(self.a, "d" * 20,
                      {"Libs/UI/FlashAssets/gfxfontlib_locfont.gfx": self.font_a})
        self._archive(self.a, "e" * 20,
                      {"Libs/UI/Other/gfxfontlib_locfont.gfx": self.font_b})
        found, issues, _ = library_gfx_imports(
            self.source, "Libs/UI/watermark.gfx", root=self.lib, build_id=self.a)
        self.assertFalse(found)
        self.assertIn("Missing matching-context movie", issues[0])

    def test_duplicate_same_virtual_font_within_build_is_missing_not_guessed(self):
        self._archive(self.a, "d" * 20,
                      {"Libs/UI/gfxfontlib_locfont.gfx": self.font_b})
        found, issues, _ = library_gfx_imports(
            self.source, "Libs/UI/watermark.gfx", root=self.lib, build_id=self.a)
        self.assertEqual(found, {})
        self.assertIn("Missing matching-context movie", issues[0])

    def test_wrong_registered_build_is_rejected(self):
        with self.assertRaises(ValueError):
            library_gfx_imports(
                self.source, "Libs/UI/watermark.gfx",
                root=self.lib, build_id=self.b)


if __name__ == "__main__":
    unittest.main()
