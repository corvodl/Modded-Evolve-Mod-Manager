"""Synthetic signed-directory reference tests; no game files needed."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import shared_pak_cache as shared
from multi_pak_assets import _resolve

PREFIX = "characters/hunters/shared/textures/"
ASSETS = {
    PREFIX + "eye.dds.0": b"DDS part zero",
    PREFIX + "eye.dds.1": b"DDS part one",
    "characters/hunters/shared/base.mtl": b"<Material/>",
}


class SharedPAKCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.paks = self.stage / "paks" / "Game"
        self.paks.mkdir(parents=True)
        self.key = self.stage / "mykeys" / "public_key.bin"
        self.key.parent.mkdir(parents=True)
        self.key.write_bytes(b"synthetic-test-public-key")
        self.source = self.paks / "characters_hunters_shared_ts.pak"
        self.source.write_bytes(b"fake signed archive")
        (self.stage / "rekey_plan.json").write_text(json.dumps({
            "blocked": [], "entries": [{"kind": "signed-encrypted",
                "path": "Game/" + self.source.name}]
        }))
        self.info = {"entries": {
            key: {"method": 13, "extra_length": 0,
                  "original_size": len(value), "compressed_size": len(value)}
            for key, value in ASSETS.items()}}

    def scan(self):
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", self.source.name)):
            return shared.SharedCatalog(
                self.stage, self.root / "preview-cache", self.key).scan()

    def test_metadata_index_is_read_only_until_reference_used(self):
        catalog = self.scan()
        self.assertEqual(len(catalog.archives), 1)
        self.assertEqual(len(catalog.index), 3)
        self.assertFalse((self.root / "preview-cache").exists())
        with patch.object(shared, "_current", catalog), patch(
                "evolve_gameplay_editor.decrypt_entry",
                side_effect=lambda archive, info, name: ASSETS[name]):
            hits, state = _resolve(catalog.index, PREFIX + "eye.tif")
        self.assertEqual(state, "found")
        self.assertEqual(hits[0]["file"].read_bytes(), ASSETS[PREFIX + "eye.dds.0"])
        self.assertEqual(hits[0]["file"].with_name("eye.dds.1").read_bytes(),
                         ASSETS[PREFIX + "eye.dds.1"])
        self.assertIsNone(hits[0]["workspace"])

    def test_extracted_project_wins_without_shared_decrypt(self):
        catalog = self.scan()
        local = self.root / "project" / "files" / "base.mtl"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"edited material")
        key = "characters/hunters/shared/base.mtl"
        index = dict(catalog.index)
        index[key] = list(index[key]) + [{
            "workspace": self.root / "project", "file": local,
            "archive": "edited-project", "relative": key}]
        with patch.object(shared, "_current", catalog), patch(
                "evolve_gameplay_editor.decrypt_entry") as decrypt:
            hits, state = _resolve(index, key,
                preferred_workspace=self.root / "project")
        self.assertEqual(state, "found")
        self.assertEqual(hits[0]["file"], local)
        decrypt.assert_not_called()

    def test_changed_source_invalidates_cached_metadata(self):
        catalog = self.scan()
        self.assertTrue(catalog.is_current())
        self.source.write_bytes(b"updated archive with new content")
        self.assertFalse(catalog.is_current())
        with patch.object(shared, "_current", catalog):
            with self.assertRaisesRegex(ValueError, "changed"):
                shared.materialize_shared_record(
                    catalog.index["characters/hunters/shared/base.mtl"][0])

    def test_only_six_known_shared_archive_names(self):
        extra = self.paks / "unrelated.pak"
        extra.write_bytes(b"ignored")
        plan = self.stage / "rekey_plan.json"
        data = json.loads(plan.read_text())
        data["entries"].append({"kind": "signed-encrypted",
                                "path": "Game/unrelated.pak"})
        plan.write_text(json.dumps(data))
        catalog = self.scan()
        self.assertEqual(len(catalog.archives), 1)


if __name__ == "__main__":
    unittest.main()
