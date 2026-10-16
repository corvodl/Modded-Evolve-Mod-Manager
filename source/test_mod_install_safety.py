"""Disk preview, source repair, and post-stage journal integrity safeguards."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from easy_mod_bundle import BundleReview
from easy_mod_install import ModReview
from mod_install_safety import (
    estimate_bundle_space, verify_collection, repair_mod_source)
from mod_install_receipts import (
    mark_staged, write_import_receipt, list_receipts, verify_staged_profile)
from mod_profiles import ModProfiles, _atomic_json
from universal_stage import digest


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.swap = self.root / "swap"
        self.catalog = self.root / "Profiles"
        self.swap.mkdir()
        pak = self.stage / "paks" / "Game" / "libs.pak"
        pak.parent.mkdir(parents=True)
        pak.write_bytes(b"before" * 50)
        self.old = pak.read_bytes()
        self.pak = pak
        (self.stage / "mykeys").mkdir()
        (self.stage / "mykeys" / "public_key.bin").write_bytes(b"public")
        (self.stage / "rekey_plan.json").write_text('{"source_root":"x"}')
        self.source = self.root / "download" / "libs.pak"
        self.source.parent.mkdir()
        self.source.write_bytes(b"modded" * 90)
        self.sha = digest(self.source)
        self.entry = ModReview(self.source, "Game/libs.pak", "libs.pak",
                               self.source.stat().st_size, self.sha,
                               ("characters/example.dds",))
        self.bundle = BundleReview(self.source, (self.entry,))
        self.store = ModProfiles(self.catalog)
        self.pid = self.store.create("My Mods")
        with patch("mod_profiles.allowed_paks", return_value={
                "game/libs.pak": "Game/libs.pak"}):
            self.mod_id = self.store.add_mod(
                self.pid, self.source, "Game/libs.pak", self.stage)

    def test_preflight_counts_library_and_recovery_without_double_counting_volumes(self):
        with patch("mod_install_safety.state_status", create=True):
            pass
        with patch("launch_integration.state_status",
                   return_value=SimpleNamespace(phase="restored")):
            preview = estimate_bundle_space(
                self.bundle, self.stage, self.swap, self.catalog,
                disk_usage=lambda path: SimpleNamespace(free=10**12))
        self.assertEqual(preview.library_bytes, self.source.stat().st_size)
        self.assertEqual(preview.stage_temp_bytes, self.source.stat().st_size)
        self.assertEqual(preview.recovery_bytes, self.pak.stat().st_size)
        self.assertEqual(len(preview.volumes), 1)
        self.assertTrue(preview.enough)

    def test_preflight_reuses_library_files_and_blocks_insufficient_disk(self):
        library = self.catalog / "ImportedMods" / self.sha / "libs.pak"
        library.parent.mkdir(parents=True)
        library.write_bytes(self.source.read_bytes())
        with patch("launch_integration.state_status",
                   return_value=SimpleNamespace(phase="prepared")):
            preview = estimate_bundle_space(
                self.bundle, self.stage, self.swap, self.catalog,
                disk_usage=lambda path: SimpleNamespace(free=100))
        self.assertEqual(preview.library_bytes, 0)
        self.assertEqual(preview.stage_temp_bytes, self.source.stat().st_size * 2)
        self.assertFalse(preview.enough)
        self.assertIn("Not enough free space", preview.warnings[0])

    def test_verify_collection_reports_missing_tampered_and_conflicting_sources(self):
        with patch("mod_install_safety.allowed_paks",
                   return_value={"game/libs.pak": "Game/libs.pak"}):
            self.assertEqual(verify_collection(self.catalog, self.pid, self.stage), ())
            self.source.write_bytes(b"altered")
            problems = verify_collection(self.catalog, self.pid, self.stage)
            self.assertIn("SHA256 mismatch", problems[0].problem)
            self.source.unlink()
            problems = verify_collection(self.catalog, self.pid, self.stage)
            self.assertIn("Missing source", problems[0].problem)
        with patch("mod_profiles.allowed_paks",
                   return_value={"game/libs.pak": "Game/libs.pak"}):
            second = self.root / "second" / "libs.pak"
            second.parent.mkdir()
            second.write_bytes(b"different")
            self.store.add_mod(self.pid, second, "Game/libs.pak", self.stage)
        with patch("mod_install_safety.allowed_paks",
                   return_value={"game/libs.pak": "Game/libs.pak"}):
            issues = verify_collection(self.catalog, self.pid, self.stage)
        self.assertTrue(any("Two enabled mods" in issue.problem for issue in issues))

    def test_repair_relinks_exact_hash_only_without_modifying_stage(self):
        self.source.unlink()
        replacement = self.root / "recovered" / "libs.pak"
        replacement.parent.mkdir()
        replacement.write_bytes(b"modded" * 90)
        with patch("universal_stage.allowed_paks", return_value={
                "game/libs.pak": "Game/libs.pak"}), patch(
                    "mod_install_safety.validate_archive_pair", return_value=["asset"]
                ):
            repaired = repair_mod_source(self.catalog, self.pid, self.mod_id,
                                         replacement, self.stage)
        self.assertEqual(digest(repaired), self.sha)
        self.assertEqual(self.pak.read_bytes(), self.old)
        self.assertEqual(self.store.list_profiles()[0]["mods"][0]["source"], str(repaired))

    def test_repair_refuses_different_hash_and_keeps_catalog_unchanged(self):
        replacement = self.root / "bad" / "libs.pak"
        replacement.parent.mkdir()
        replacement.write_bytes(b"not original")
        old_catalog = self.store.catalog_path.read_bytes()
        with patch("universal_stage.allowed_paks", return_value={
                "game/libs.pak": "Game/libs.pak"}):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                repair_mod_source(self.catalog, self.pid, self.mod_id,
                                  replacement, self.stage)
        self.assertEqual(self.store.catalog_path.read_bytes(), old_catalog)

    def _stage_journal(self):
        self.pak.write_bytes(self.source.read_bytes())
        backup = self.catalog / "Backups" / "operation" / "libs"
        backup.mkdir(parents=True)
        before = backup / "before.pak"
        before.write_bytes(self.old)
        _atomic_json(backup / "metadata.json", {
            "relative": "Game/libs.pak", "stage": str(self.stage),
            "swap_dir": str(self.swap), "new_sha256": self.sha,
            "old_sha256": digest(before), "status": "installed",
            "has_ready": False
        })
        _atomic_json(self.store.applied_path, {
            "schema": 1, "stage": str(self.stage), "swap": str(self.swap),
            "profile_id": self.pid, "in_progress": False, "pending": None,
            "entries": [{"mod_id": self.mod_id, "relative": "Game/libs.pak",
                         "backup": str(backup)}]
        })
        return backup

    def test_post_stage_verifies_staged_hash_and_original_backups(self):
        backup = self._stage_journal()
        receipt = write_import_receipt(self.catalog, self.pid, self.bundle,
                                       [self.mod_id])
        with patch("launch_integration.state_status",
                   return_value=SimpleNamespace(phase="restored")):
            self.assertEqual(mark_staged(self.catalog, receipt,
                                         self.stage, self.swap), 1)
        self.assertEqual(list_receipts(self.catalog)[0]["status"], "staged_verified")

    def test_missing_backup_creates_recovery_needed_receipt(self):
        backup = self._stage_journal()
        (backup / "before.pak").unlink()
        receipt = write_import_receipt(self.catalog, self.pid, self.bundle,
                                       [self.mod_id])
        with patch("launch_integration.state_status",
                   return_value=SimpleNamespace(phase="restored")):
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                mark_staged(self.catalog, receipt, self.stage, self.swap)
        self.assertEqual(list_receipts(self.catalog)[0]["status"], "recovery_needed")

    def test_staged_archive_tampering_is_detected(self):
        self._stage_journal()
        self.pak.write_bytes(b"tampered-after-stage")
        with patch("launch_integration.state_status",
                   return_value=SimpleNamespace(phase="restored")):
            with self.assertRaisesRegex(RuntimeError, "hash verification failed"):
                verify_staged_profile(self.catalog, self.pid, self.stage, self.swap,
                                      (self.mod_id,))


if __name__ == "__main__":
    unittest.main()
