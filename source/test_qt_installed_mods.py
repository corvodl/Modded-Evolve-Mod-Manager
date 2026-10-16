"""Native Qt table/status smoke tests for unobtrusive Installed Mods library."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from mod_profiles import ModProfiles
from qt_mod_profiles import ModProfilesDialog


class InstalledModsTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.catalog = root / "Profiles"
        self.stage = root / "stage"
        self.stage.mkdir()
        self.mod = root / "download" / "libs.pak"
        self.mod.parent.mkdir()
        self.mod.write_bytes(b"signed-test-file")
        self.store = ModProfiles(self.catalog)
        self.profile_id = self.store.create("My Mods")
        from hashlib import sha256
        self.hash = sha256(self.mod.read_bytes()).hexdigest()
        self.parent = QWidget()
        self.addCleanup(self.parent.close)
        self.allowed_patch = patch("mod_profiles.allowed_paks", return_value={
            "game/libs.pak": "Game/libs.pak"})
        self.allowed_patch.start()
        self.addCleanup(self.allowed_patch.stop)
        self.store.add_verified_bundle(self.profile_id, [
            (self.mod, "Game/libs.pak", self.hash)], self.stage)

    def test_compact_table_reports_compatible_target_and_enabled_state(self):
        with patch("qt_mod_profiles.allowed_paks", return_value={
                "game/libs.pak": "Game/libs.pak"}):
            dialog = ModProfilesDialog(
                self.parent, self.catalog, self.stage, lambda x: None)
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.table.columnCount(), 5)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.table.item(0, 2).text(), "Game/libs.pak")
            self.assertEqual(dialog.table.item(0, 3).text(), "Target recognized")
            self.assertEqual(dialog.table.item(0, 4).text(), "Enabled")
            self.assertEqual(dialog.table.item(0, 0).checkState(), Qt.Checked)
            self.assertIn("SHA", dialog.table.item(0, 1).toolTip().upper())

    def test_matching_and_changed_stage_fingerprint_uses_advisory_status(self):
        from universal_stage import digest
        plan = self.stage / 'rekey_plan.json'
        plan.write_text('{"entries":[]}', encoding="utf-8")
        from mod_profiles import _atomic_json
        data = self.store._catalog()
        record = data["profiles"][0]["mods"][0]
        record["verified_stage_fingerprint"] = digest(plan)
        _atomic_json(self.store.catalog_path, data)
        with patch("qt_mod_profiles.allowed_paks", return_value={
                "game/libs.pak": "Game/libs.pak"}):
            dialog = ModProfilesDialog(
                self.parent, self.catalog, self.stage, lambda x: None)
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.table.item(0, 3).text(), "Verified for stage")
            plan.write_text('{"entries":[{"changed":true}]}', encoding="utf-8")
            dialog.refresh()
            self.assertEqual(dialog.table.item(0, 3).text(), "Review this build")

    def test_missing_source_and_unknown_target_are_visible(self):
        self.mod.unlink()
        with patch("qt_mod_profiles.allowed_paks", return_value={}):
            dialog = ModProfilesDialog(
                self.parent, self.catalog, self.stage, lambda x: None)
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.table.item(0, 3).text(), "Missing source")

    def test_different_stage_displays_incompatible_target(self):
        with patch("qt_mod_profiles.allowed_paks", return_value={
                "engine/shaders.pak": "Engine/shaders.pak"}):
            dialog = ModProfilesDialog(
                self.parent, self.catalog, self.stage, lambda x: None)
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.table.item(0, 3).text(),
                             "Incompatible target")


if __name__ == "__main__":
    unittest.main()
