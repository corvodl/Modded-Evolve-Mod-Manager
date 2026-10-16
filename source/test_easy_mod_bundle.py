"""Multi-PAK import: all-or-nothing catalog, duplicate reuse, archive safety."""
import json
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from easy_mod_bundle import (review_mod_bundle, review_legacy_folder,
                             import_reviewed_bundle, _members)
from mod_profiles import ModProfiles
from mod_install_receipts import write_import_receipt, mark_staged, list_receipts

PAKS = {"game/libs.pak": "Game/libs.pak", "engine/shaders.pak": "Engine/shaders.pak"}


class BundleInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        self.catalog = self.root / "Profiles"
        (self.stage / "paks" / "Game").mkdir(parents=True)
        (self.stage / "paks" / "Engine").mkdir(parents=True)
        (self.stage / "paks" / "Game" / "libs.pak").write_bytes(b"baseline-game")
        (self.stage / "paks" / "Engine" / "shaders.pak").write_bytes(b"baseline-engine")
        (self.stage / "mykeys").mkdir()
        (self.stage / "mykeys" / "public_key.bin").write_bytes(b"public")
        self.store = ModProfiles(self.catalog)
        self.pid = self.store.create("My Mods")
        self.package = self.root / "bundle.zip"
        self.make_zip()

    def make_zip(self, entries=None):
        entries = entries or (("Game/libs.pak", b"modified-game"),
                              ("Engine/shaders.pak", b"modified-engine"))
        with zipfile.ZipFile(self.package, "w") as archive:
            for name, data in entries:
                archive.writestr(name, data)

    def review(self):
        with patch("easy_mod_bundle.allowed_paks", return_value=PAKS), \
                patch("easy_mod_bundle._locate_target", side_effect=self.locate), \
                patch("easy_mod_bundle.validate_archive_pair",
                      return_value=["changed/material.mtl"]):
            return review_mod_bundle(self.package, self.stage)

    @staticmethod
    def locate(stage, name):
        matches = [v for v in PAKS.values()
                   if Path(v).name.casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError("Ambiguous")
        return matches[0]

    def add_bundle(self, bundle, **kwargs):
        with patch("easy_mod_bundle.allowed_paks", return_value=PAKS), \
                patch("easy_mod_bundle._locate_target", side_effect=self.locate), \
                patch("easy_mod_bundle.validate_archive_pair",
                      return_value=["changed/material.mtl"]), \
                patch("mod_profiles.allowed_paks", return_value=PAKS):
            return import_reviewed_bundle(
                bundle, self.stage, self.catalog, self.pid, **kwargs)

    def test_two_paks_reviewed_and_saved_as_one_profile_commit(self):
        bundle = self.review()
        self.assertEqual(len(bundle.entries), 2)
        ids, paths, reused = self.add_bundle(bundle)
        self.assertEqual(len(ids), 2)
        self.assertFalse(reused)
        self.assertEqual(len(self.store.list_profiles()[0]["mods"]), 2)
        self.assertEqual([p.read_bytes() for p in paths],
                         [b"modified-game", b"modified-engine"])
        self.assertEqual((self.stage / "paks" / "Game" / "libs.pak").read_bytes(),
                         b"baseline-game")

    def test_duplicate_import_reuses_content_and_existing_profile_entries(self):
        bundle = self.review()
        old_ids, paths, _ = self.add_bundle(bundle)
        ids, second_paths, already = self.add_bundle(bundle)
        self.assertTrue(already)
        self.assertEqual(ids, old_ids)
        self.assertEqual(paths, second_paths)
        self.assertEqual(len(self.store.list_profiles()[0]["mods"]), 2)

    def test_failed_signature_aborts_entire_bundle(self):
        with patch("easy_mod_bundle.allowed_paks", return_value=PAKS), \
                patch("easy_mod_bundle.validate_archive_pair",
                      side_effect=[["changed"], ValueError("RSA signature mismatch")]):
            with self.assertRaisesRegex(ValueError, "RSA"):
                review_mod_bundle(self.package, self.stage)
        self.assertEqual(self.store.list_profiles()[0]["mods"], [])

    def test_changed_source_after_preview_never_changes_profile(self):
        bundle = self.review()
        self.make_zip((("Game/libs.pak", b"changed"),))
        with self.assertRaisesRegex(RuntimeError, "changed since review"):
            self.add_bundle(bundle)
        self.assertEqual(self.store.list_profiles()[0]["mods"], [])

    def test_same_target_twice_fails_before_import(self):
        self.make_zip((("a/libs.pak", b"x"), ("b/libs.pak", b"y")))
        with patch("easy_mod_bundle.allowed_paks", return_value=PAKS), \
                patch("easy_mod_bundle._locate_target", side_effect=self.locate), \
                patch("easy_mod_bundle.validate_archive_pair", return_value=["changed"]):
            with self.assertRaisesRegex(ValueError, "multiple PAKs"):
                review_mod_bundle(self.package, self.stage)

    def test_zip_traversal_symlink_backslash_and_duplicate_names_rejected(self):
        for name in ("../Game/libs.pak", "C:/Game/libs.pak",
                     "Game\\libs.pak", "/Game/libs.pak"):
            # Write exact raw ZIP names. Windows zipfile may normalize a
            # backslash before creating the archive.
            marker = "Z" * len(name)
            self.make_zip(((marker, b"bad"),))
            data = self.package.read_bytes()
            self.assertEqual(data.count(marker.encode("ascii")), 2)
            self.package.write_bytes(
                data.replace(marker.encode("ascii"), name.encode("ascii")))
            with self.subTest(name=name), zipfile.ZipFile(self.package) as archive:
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    _members(archive)
        self.make_zip((("Game/libs.pak", b"x"), ("Game/libs.pak", b"y")))
        with zipfile.ZipFile(self.package) as archive:
            with self.assertRaisesRegex(ValueError, "duplicate"):
                _members(archive)
        with zipfile.ZipFile(self.package, "w") as archive:
            info = zipfile.ZipInfo("Game/libs.pak")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "elsewhere")
        with zipfile.ZipFile(self.package) as archive:
            with self.assertRaisesRegex(ValueError, "unsafe"):
                _members(archive)

    def test_existing_target_collision_requires_explicit_approval(self):
        bundle = self.review()
        ids, _, _ = self.add_bundle(bundle)
        self.make_zip((("Game/libs.pak", b"another-game"),
                       ("Engine/shaders.pak", b"another-engine")))
        changed = self.review()
        with self.assertRaisesRegex(ValueError, "approval"):
            self.add_bundle(changed)
        self.assertEqual([m["id"] for m in self.store.list_profiles()[0]["mods"]], ids)
        other_ids, _, _ = self.add_bundle(changed, replace_conflicts=True)
        self.assertEqual(len(other_ids), 2)
        enabled = [m for m in self.store.list_profiles()[0]["mods"] if m["enabled"]]
        self.assertEqual(len(enabled), 2)
        self.assertEqual({m["id"] for m in enabled}, set(other_ids))

    def test_catalog_write_failure_removes_only_new_copies(self):
        bundle = self.review()
        with patch("easy_mod_bundle.allowed_paks", return_value=PAKS), \
                patch("easy_mod_bundle._locate_target", side_effect=self.locate), \
                patch("easy_mod_bundle.validate_archive_pair",
                      return_value=["changed/material.mtl"]), \
                patch("mod_profiles.allowed_paks", return_value=PAKS), \
                patch("mod_profiles._atomic_json", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                import_reviewed_bundle(bundle, self.stage, self.catalog, self.pid)
        self.assertEqual(self.store.list_profiles()[0]["mods"], [])
        self.assertFalse(list((self.catalog / "ImportedMods").rglob("*.pak")))

    def test_legacy_folder_references_user_owned_files_only(self):
        folder = self.root / "LegacyMods"
        folder.mkdir()
        (folder / "libs.pak").write_bytes(b"legacy-mod")
        with patch("easy_mod_bundle._locate_target", return_value="Game/libs.pak"), \
                patch("easy_mod_bundle.validate_archive_pair", return_value=["x"]):
            bundle = review_legacy_folder(folder, self.stage, game_root=self.root / "Game")
        self.assertTrue(bundle.legacy)
        self.assertEqual(bundle.entries[0].target, "Game/libs.pak")
        with self.assertRaisesRegex(ValueError, "signing stage"):
            review_legacy_folder(self.stage, self.stage)

    def test_receipt_only_marks_staged_after_journal_success(self):
        bundle = self.review()
        ids, _, reused = self.add_bundle(bundle)
        receipt = write_import_receipt(self.catalog, self.pid, bundle, ids,
                                       existing=reused)
        self.assertEqual(list_receipts(self.catalog)[0]["status"],
                         "imported_not_staged")
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            mark_staged(self.catalog, receipt, self.stage, self.root / "swap")
        self.assertEqual(list_receipts(self.catalog)[0]["status"],
                         "recovery_needed")


if __name__ == "__main__":
    unittest.main()
