"""Extracted PAK library tests: synthetic CryPAKs, no game installation needed."""
from __future__ import annotations
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import json

import asset_library as lib


class ExtractedAssetLibraryTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "EvolveGame"
        self.game.mkdir()
        self.library = self.root / "AssetLibrary"
        self.source = self.game / "characters_hunters_shared_ts.pak"
        self.source.write_bytes(b"synthetic signed archive input")
        self.key = self.root / "RSAKeyData.bin"
        self.key.write_bytes(b"test public key")
        self.data = {
            "characters/hunters/shared/textures/eyeball_3p_diff.dds.0": b"DDS0 fake pixel",
            "characters/hunters/shared/textures/eyeball_3p_diff.dds.1": b"DDS1 fragment",
            "characters/hunters/merc_tech/animations/3p/idle.caf": b"CrChF CAF data",
            "characters/hunters/shared/model.chrparams": b"<Params />",
        }
        self.info = {"entries": {key: {
            "method": 13, "extra_length": 0,
            "original_size": len(value), "compressed_size": len(value)}
            for key, value in self.data.items()}}

    def _import(self):
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", self.source.name)) as verify, \
             patch("evolve_gameplay_editor.decrypt_entry",
                   side_effect=lambda source, info, entry: self.data[entry]) as decrypt:
            result = lib.import_pak(self.source, root=self.library,
                                    extra_keys=[self.key])
        return result, verify, decrypt

    def test_extract_all_references_and_reuse_without_unpacking(self):
        result, verify, decrypt = self._import()
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["entries"], 4)
        self.assertEqual(decrypt.call_count, 4)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(self.source.read_bytes(), b"synthetic signed archive input")
        index = lib.extracted_index(self.library)
        self.assertEqual(len(index), 4)
        key = "characters/hunters/merc_tech/animations/3p/idle.caf"
        self.assertEqual(index[key][0]["file"].read_bytes(), self.data[key])
        self.assertTrue(index[key][0]["file"].is_relative_to(self.library))
        with patch("evolve_pak_rekey.extract_plain_cdr") as verify_again, \
             patch("evolve_gameplay_editor.decrypt_entry") as decrypt_again:
            repeat = lib.import_pak(self.source, root=self.library,
                                    extra_keys=[self.key])
        self.assertEqual(repeat["status"], "already-imported")
        verify_again.assert_not_called()
        decrypt_again.assert_not_called()

    def test_integrity_verification_and_explicit_removal(self):
        self._import()
        report = lib.verify_library(self.library)
        self.assertEqual(report["archives"], 1)
        self.assertEqual(report["files"], 4)
        self.assertEqual(report["corrupt"], [])
        index = lib.extracted_index(self.library)
        index[next(iter(index))][0]["file"].write_bytes(b"changed")
        damaged = lib.verify_library(self.library)
        self.assertEqual(len(damaged["corrupt"]), 1)
        archive_id = lib.imported_archives(self.library)[0]["id"]
        lib.remove_archive(archive_id, self.library)
        self.assertFalse(lib.imported_archives(self.library))
        self.assertTrue(self.source.exists())

    def test_archive_path_traversal_causes_full_rollback(self):
        self.info["entries"]["../executable.exe"] = {
            "method": 13, "extra_length": 0, "original_size": 3,
            "compressed_size": 3}
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", self.source.name)), \
             patch("evolve_gameplay_editor.decrypt_entry",
                   side_effect=lambda *args: b"abc"):
            with self.assertRaises(ValueError):
                lib.import_pak(self.source, root=self.library,
                               extra_keys=[self.key])
        self.assertEqual(lib.imported_archives(self.library), ())
        self.assertFalse((self.root / "executable.exe").exists())

    def test_reject_unsigned_or_unverifiable_pak(self):
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   side_effect=ValueError("Signature mismatch")):
            with self.assertRaisesRegex(ValueError, "not a verified"):
                lib.import_pak(self.source, root=self.library,
                               extra_keys=[self.key])
        self.assertFalse(self.library.is_dir())

    def test_user_added_arbitrary_named_pak_is_supported(self):
        special = self.game / "maps_special_level.pak"
        special.write_bytes(b"fictional level archive")
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", special.name)), \
             patch("evolve_gameplay_editor.decrypt_entry",
                   side_effect=lambda source, info, entry: self.data[entry]):
            result = lib.import_pak(special, root=self.library,
                                    extra_keys=[self.key])
        self.assertEqual(result["status"], "imported")
        self.assertEqual(lib.imported_archives(self.library)[0]["name"],
                         special.name)

    def test_required_archive_status_checks_expected_names_not_total_count(self):
        # An arbitrary tenth PAK must not make the six+three archive check pass.
        other = self.game / "custom_objects.pak"
        other.write_bytes(b"synthetic custom PAK")
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", other.name)), \
             patch("evolve_gameplay_editor.decrypt_entry",
                   side_effect=lambda source, info, entry: self.data[entry]):
            lib.import_pak(other, root=self.library, extra_keys=[self.key])
        status = lib.required_archive_status(self.library)
        self.assertEqual((status["shared"], status["animations"]), (0, 0))
        self.assertEqual(len(status["missing"]), 9)
        self._import()
        status = lib.required_archive_status(self.library)
        self.assertEqual((status["shared"], status["animations"]), (1, 0))
        self.assertEqual(len(status["missing"]), 8)
        self.assertIn("characters_hunters_shared_data.pak", status["missing"])
        self.assertEqual(
            lib.reference_status(
                "characters/hunters/shared/textures/eyeball_3p_diff.dds.0",
                self.library), ("characters_hunters_shared_ts.pak",))

    def test_missing_library_file_is_not_counted_as_complete(self):
        self._import()
        index = lib.extracted_index(self.library)
        index[next(iter(index))][0]["file"].unlink()
        self.assertEqual(lib.required_archive_status(self.library)["shared"], 0)
        with self.assertRaisesRegex(ValueError, "missing or damaged"):
            lib.import_pak(self.source, root=self.library, extra_keys=[self.key])

    def test_discovery_only_nine_game_defaults(self):
        (self.game / "bin64_SteamRetail").mkdir()
        (self.game / "bin64_SteamRetail" / "Evolve.exe").write_bytes(b"fixture")
        (self.game / "AnimationsCAF.pak").write_bytes(b"dummy")
        (self.game / "some_other_level.pak").write_bytes(b"dummy")
        names = [p.name.lower() for p in lib.installed_defaults(self.game)]
        self.assertIn("animationscaf.pak", names)
        self.assertIn(self.source.name, names)
        self.assertNotIn("some_other_level.pak", names)


if __name__ == "__main__":
    import unittest
    unittest.main()
