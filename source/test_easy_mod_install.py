"""Non-destructive tests for user-supplied PAK and ZIP import safety."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import stat
import unittest
import zipfile

from easy_mod_install import review_package, import_reviewed_mod, _find_mod_member


class EasyModInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "staged"
        self.original = self.stage / "paks" / "Game" / "libs.pak"
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b"the-original-signed-archive")
        key = self.stage / "mykeys" / "public_key.bin"
        key.parent.mkdir()
        key.write_bytes(b"test-only-key")
        self.source = self.root / "download" / "libs.pak"
        self.source.parent.mkdir()
        self.source.write_bytes(b"user-modified-signed-archive")
        self.catalog = self.root / "Profiles"

    def _review(self, path):
        with patch("easy_mod_install.allowed_paks",
                   return_value={"game/libs.pak": "Game/libs.pak"}), \
                patch("easy_mod_install.validate_archive_pair",
                      return_value=("levels/example.xml", "textures/hero.dds.0")) as verify:
            result = review_package(path, self.stage)
            self.assertTrue(verify.called)
            return result

    def test_unsigned_baseline_mismatch_fails_review(self):
        with patch("easy_mod_install.allowed_paks",
                   return_value={"game/libs.pak": "Game/libs.pak"}), \
                patch("easy_mod_install.validate_archive_pair",
                      side_effect=ValueError("RSA verification failed")):
            with self.assertRaisesRegex(ValueError, "RSA"):
                review_package(self.source, self.stage)

    def test_direct_pak_review_does_not_write_staging(self):
        old = self.original.read_bytes()
        plan = self._review(self.source)
        self.assertEqual(plan.target, "Game/libs.pak")
        self.assertEqual(plan.changed_entries,
                         ("levels/example.xml", "textures/hero.dds.0"))
        self.assertEqual(self.original.read_bytes(), old)
        self.assertEqual(plan.pak_filename, "libs.pak")

    def test_zip_review_accepts_one_nested_signed_pak(self):
        archive_path = self.root / "mod.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("ModName/libs.pak", self.source.read_bytes())
            archive.writestr("README.txt", "Included for Evolve Stage 2")
        result = self._review(archive_path)
        self.assertEqual(result.zip_member, "ModName/libs.pak")
        self.assertEqual(result.pak_filename, "libs.pak")
        self.assertEqual(self.original.read_bytes(), b"the-original-signed-archive")

    def test_rejects_traversal_multi_pak_and_zip_symlink(self):
        for entries, reason in (
            ([("../secret/libs.pak", b"x")], "unsafe"),
            ([("a/libs.pak", b"x"), ("b/other.pak", b"x")], "exactly one"),
        ):
            file = self.root / ("unsafe-" + reason + ".zip")
            with zipfile.ZipFile(file, "w") as archive:
                for name, contents in entries:
                    archive.writestr(name, contents)
            with zipfile.ZipFile(file) as archive, self.assertRaisesRegex(ValueError, reason):
                _find_mod_member(archive)
        file = self.root / "symlink.zip"
        with zipfile.ZipFile(file, "w") as archive:
            info = zipfile.ZipInfo("libs.pak")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "other")
        with zipfile.ZipFile(file) as archive, self.assertRaisesRegex(ValueError, "symbolic"):
            _find_mod_member(archive)

    def test_ambiguous_signed_target_is_not_guessed(self):
        with patch("easy_mod_install.allowed_paks", return_value={
            "game/libs.pak": "Game/libs.pak",
            "engine/libs.pak": "Engine/libs.pak"}):
            with self.assertRaisesRegex(ValueError, "Multiple targets"):
                review_package(self.source, self.stage)

    def test_only_enabled_exact_target_collisions_are_flagged(self):
        from easy_mod_install import enabled_target_conflicts
        profile = {"mods": [
            {"id": "active", "relative": "Game/libs.pak", "enabled": True},
            {"id": "disabled", "relative": "Game/libs.pak", "enabled": False},
            {"id": "different", "relative": "Engine/shaders.pak", "enabled": True},
        ]}
        self.assertEqual(
            [mod["id"] for mod in enabled_target_conflicts(profile, "game/LIBS.pak")],
            ["active"])
        self.assertEqual(enabled_target_conflicts(profile, "Game/videos.pak"), ())

    def test_changing_file_after_preview_refuses_import(self):
        reviewed = self._review(self.source)
        self.source.write_bytes(b"tampered-after-review")
        with self.assertRaisesRegex(RuntimeError, "changed since review"):
            import_reviewed_mod(reviewed, self.stage, self.catalog, "profile")
        self.assertFalse((self.catalog / "ImportedMods").exists())

    def test_import_uses_immutable_copy_and_profiles_api_not_installed_game(self):
        reviewed = self._review(self.source)
        with patch("easy_mod_install._locate_target", return_value="Game/libs.pak"), \
                patch("easy_mod_install.validate_archive_pair", return_value=["changed"]), \
                patch("mod_profiles.ModProfiles.add_mod", return_value="abc123") as add_mod:
            result, dest = import_reviewed_mod(reviewed, self.stage, self.catalog, "profile")
        self.assertEqual(result, "abc123")
        self.assertEqual(dest.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.original.read_bytes(), b"the-original-signed-archive")
        add_mod.assert_called_once_with("profile", dest, "Game/libs.pak", self.stage)

    def test_existing_import_path_must_match_verified_sha(self):
        reviewed = self._review(self.source)
        target = self.catalog / "ImportedMods" / reviewed.sha256[:20] / "libs.pak"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"tampered")
        with patch("easy_mod_install._locate_target", return_value="Game/libs.pak"), \
                patch("easy_mod_install.validate_archive_pair", return_value=["changed"]):
            with self.assertRaisesRegex(RuntimeError, "different data"):
                import_reviewed_mod(reviewed, self.stage, self.catalog, "profile")
        self.assertEqual(target.read_bytes(), b"tampered")


if __name__ == "__main__":
    unittest.main()
