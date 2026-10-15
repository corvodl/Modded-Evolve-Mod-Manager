"""Regression: Wraith/teleporter materials resolve sibling streamed DDS in TS PAKs.

Fixtures are synthetic; no game files or signing material is embedded here.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from asset_library import _invalidate
from material_preview import load_preview_materials
from multi_pak_assets import _candidates
from test_material_preview import uv_mesh


class WraithSharedTextureResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "AssetLibrary"
        self.model_id, self.ts_id = "a" * 20, "b" * 20
        self.model = "characters/monsters/teleporter/teleporter_body.skinm"
        self.cdf = "characters/monsters/teleporter/teleporter.cdf"
        self.material = "characters/monsters/teleporter/materials/body.mtl"
        self.texture = "characters/monsters/teleporter/textures/teleporter_diff.dds.0"
        self.color = (110, 55, 130)

        cdf = ('<CharacterDefinition><Model File="characters/monsters/teleporter/teleporter.chr"/>'
               '<AttachmentList><Attachment Type="CA_SKIN" AName="Body" '
               'Binding="characters/monsters/teleporter/teleporter_body.skin" '
               'Material="' + self.material + '"/></AttachmentList></CharacterDefinition>')
        material = ('<Material Name="body" Shader="Illum"><Textures>'
                    '<Texture Map="Diffuse" File="teleporter_diff.tif"/>'
                    '</Textures></Material>')
        buf = BytesIO()
        Image.new("RGB", (16, 16), self.color).save(buf, format="DDS")
        self._archive(self.model_id, "characters_monsters_teleporter_data.pak", {
            self.model: uv_mesh(),
            self.cdf: cdf.encode(),
            self.material: material.encode(),
        })
        self._archive(self.ts_id, "characters_monsters_teleporter_ts.pak", {
            self.texture: buf.getvalue(),
        })
        _invalidate()
        self.addCleanup(_invalidate)

    def _archive(self, identifier, name, files):
        directory = self.root / identifier
        for virtual, payload in files.items():
            target = directory / "files" / virtual
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (directory / "asset-library.json").write_text(json.dumps({
            "version": 1,
            "name": name,
            "entries": [{"path": name, "bytes": len(payload)}
                        for name, payload in files.items()],
        }), encoding="utf-8")

    def test_library_model_links_sibling_textures_across_imported_paks(self):
        with patch("asset_library.LIBRARY_HOME", self.root):
            materials = load_preview_materials(self.root / self.model_id, self.model)
        self.assertEqual(materials.count, 1)
        self.assertEqual(materials.textures[0].size, (16, 16))
        self.assertEqual(materials.textures[0].convert("RGB").getpixel((0, 0)), self.color)
        from multi_pak_assets import model_material_links
        with patch("asset_library.LIBRARY_HOME", self.root):
            links = model_material_links(self.root / self.model_id, self.model)
        self.assertEqual(links["status"], "found")
        self.assertEqual(links["textures"][0]["status"], "found")
        self.assertEqual(links["textures"][0]["paths"], [self.texture])

    def test_windows_native_companion_path_is_a_valid_lookup_argument(self):
        # Prevent reintroducing WindowsPath.strip() errors during GLB skin export.
        from pathlib import PureWindowsPath
        self.assertEqual(
            _candidates(PureWindowsPath(
                "characters/monsters/teleporter/teleporter_body.skin")),
            ["characters/monsters/teleporter/teleporter_body.skin"])

    def test_similarly_named_other_character_texture_is_not_used(self):
        other = "characters/monsters/other/textures/teleporter_diff.dds.0"
        source = self.root / self.ts_id / "files" / self.texture
        buf = BytesIO()
        Image.new("RGB", (16, 16), (10, 200, 90)).save(buf, format="DDS")
        self._archive("c" * 20, "characters_monsters_other_ts.pak",
                      {other: buf.getvalue()})
        _invalidate()
        with patch("asset_library.LIBRARY_HOME", self.root):
            materials = load_preview_materials(self.root / self.model_id, self.model)
        self.assertEqual(materials.count, 1)
        self.assertEqual(materials.textures[0].convert("RGB").getpixel((0, 0)), self.color)


if __name__ == "__main__":
    unittest.main()
