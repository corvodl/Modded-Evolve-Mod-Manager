"""Non-destructive regressions for storage analysis and setup cleanup."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from game_storage import (
    CATEGORIES, cleanup_candidates, data_location, delete_old_setup,
    measure_storage, reusable_setup, validate_storage_root
)


class GameStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "Data"
        self.root = self.data / "GameSets"
        self.root.mkdir(parents=True)
        self.game = Path(self.temp.name) / "EvolveGame"
        self.game.mkdir()
        self.projects = self.data / "Projects"
        self.projects.mkdir()
        self.profiles = self.data / "Profiles"
        self.profiles.mkdir()
        self.active = self.setup("Setup_Active")
        self.old = self.setup("Setup_Old")
        self.stage, self.swap = self.active / "staged", self.active / "swap"

    def setup(self, name):
        folder = self.root / name
        (folder / "staged").mkdir(parents=True)
        (folder / "swap").mkdir()
        (folder / "staged" / "rekey_plan.json").write_text(json.dumps({
            "source_root": str(self.game), "entries": [], "blocked": []}))
        (folder / "refresh_report.json").write_text(json.dumps({
            "status": "complete", "game": str(self.game),
            "stage": str(folder / "staged"), "swap": str(folder / "swap")}))
        (folder / "swap" / "controlled_swap_state.json").write_text(json.dumps({
            "version": 1, "phase": "restored", "stage": str(folder / "staged"),
            "game": str(self.game), "files": [], "count": 0}))
        return folder

    def candidates(self):
        return {path.name: reason for path, _, reason in cleanup_candidates(
            self.root, self.stage, self.swap, self.projects, self.profiles,
            game_root=self.game)}

    def test_breakdown_measures_without_duplicate_categories(self):
        (self.active / "staged" / "large.pak").write_bytes(b"A" * 4096)
        (self.old / "staged" / "large.pak").write_bytes(b"B" * 2048)
        (self.projects / "project").write_bytes(b"C" * 1000)
        (self.data / "AssetLibrary").mkdir()
        (self.data / "AssetLibrary" / "texture").write_bytes(b"T" * 256)
        result = measure_storage(self.data, self.root, self.stage, self.swap,
                                 self.projects)
        self.assertEqual(tuple(result["bytes"]), CATEGORIES)
        self.assertGreaterEqual(result["bytes"]["Active setup"], 4096)
        self.assertGreaterEqual(result["bytes"]["Old setups"], 2048)
        self.assertEqual(result["bytes"]["Projects"], 1000)
        self.assertEqual(result["bytes"]["Asset Library"], 256)
        self.assertEqual(result["total"], sum(result["bytes"].values()))

    def test_active_root_display_and_game_separation(self):
        self.assertEqual(data_location(self.stage, self.swap, self.root), self.active.resolve())
        self.assertEqual(validate_storage_root(self.root, game=self.game), self.root.resolve())
        with self.assertRaisesRegex(ValueError, "separate"):
            validate_storage_root(self.game / "Mods", game=self.game)

    def test_active_and_project_referenced_setup_cannot_be_removed(self):
        (self.projects / ".evolve-pak-workspace.json").write_text(json.dumps({
            "source_pak": str(self.old / "staged" / "paks" / "Game" / "libs.pak")}))
        reasons = self.candidates()
        self.assertIn("Active signing", reasons["Setup_Active"])
        self.assertIn("project", reasons["Setup_Old"])
        with patch("universal_stage.ensure_closed"):
            with self.assertRaisesRegex(RuntimeError, "no longer safe"):
                delete_old_setup(self.old, self.root, self.stage, self.swap,
                                 self.projects, self.profiles, game_root=self.game)
        self.assertTrue(self.old.exists())

    def test_profile_reference_and_unsafe_swap_block_cleanup(self):
        (self.profiles / "profiles.json").write_text(json.dumps({
            "profiles": [{"mods": [{"source": str(self.old / "staged" / "paks" / "file.pak")}]}]}))
        self.assertIn("profile", self.candidates()["Setup_Old"].casefold())
        (self.profiles / "profiles.json").unlink()
        journal = self.old / "swap" / "controlled_swap_state.json"
        doc = json.loads(journal.read_text())
        doc["phase"] = "swapped"
        journal.write_text(json.dumps(doc))
        self.assertIn("swap", self.candidates()["Setup_Old"].lower())

    def test_deleting_verified_old_setup_preserves_active_and_game(self):
        game_file = self.game / "Evolve.exe"
        game_file.write_bytes(b"original")
        with patch("universal_stage.ensure_closed"), \
                patch("orphaned_swap_recovery.plan_recovery", return_value=[]), \
                patch("old_prepared.scan_prepared", return_value={
                    "backups": [], "partial": [], "ready": []}):
            delete_old_setup(self.old, self.root, self.stage,
                             self.swap, self.projects, self.profiles,
                             game_root=self.game)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.active.exists())
        self.assertEqual(game_file.read_bytes(), b"original")

    def test_reuse_exact_game_launcher_and_clean_swap_only(self):
        (self.stage / "stage_status.json").write_text(
            '{"status":"fully-staged-offline","count":1}')
        (self.stage / "mykeys").mkdir()
        (self.stage / "mykeys" / "private_key.pem").write_bytes(b"key")
        (self.stage / "mykeys" / "public_key.bin").write_bytes(b"key")
        launcher = Path(self.temp.name) / "Launcher.exe"
        launcher.write_bytes(b"EXE")
        (self.swap / "launcher_config.json").write_text(json.dumps({
            "launcher": str(launcher)}))
        pak = self.stage / "paks" / "Game" / "libs.pak"
        pak.parent.mkdir(parents=True)
        pak.write_bytes(b"pak")
        with patch("launch_integration.missing_scripts", return_value=[]), \
                patch("orphaned_swap_recovery.plan_recovery", return_value=[]), \
                patch("universal_stage.allowed_paks", return_value={
                    "game/libs.pak": "Game/libs.pak"}):
            self.assertTrue(reusable_setup(self.stage, self.swap, self.game, launcher))
            # Setup initially ships core helpers; the extra watchdog is
            # installed by the launch path, not by first-run signing.
            with patch("launch_integration.missing_scripts",
                       return_value=["auto_restore_watch.py"]):
                self.assertTrue(reusable_setup(self.stage, self.swap, self.game, launcher))
            self.assertFalse(reusable_setup(self.stage, self.swap, self.game,
                                            Path(self.temp.name) / "wrong.exe"))
            journal = self.swap / "controlled_swap_state.json"
            doc = json.loads(journal.read_text())
            doc["phase"] = "swapped"
            journal.write_text(json.dumps(doc))
            self.assertFalse(reusable_setup(self.stage, self.swap, self.game, launcher))


if __name__ == "__main__":
    unittest.main()
