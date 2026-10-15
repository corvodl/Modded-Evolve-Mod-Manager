"""Version labels use configured read-only PE metadata, never run EXEs."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installed_game_versions import (
    client_executable, installed_versions, windows_file_version,
)


class InstalledVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "EvolveGame"
        game_exe = self.game / "bin64_SteamRetail" / "Evolve.exe"
        game_exe.parent.mkdir(parents=True)
        game_exe.write_bytes(b"offline test fixture")
        self.swap = self.root / "swap"
        self.swap.mkdir()
        self.client = self.root / "ModdedEvolveLauncher.exe"
        self.client.write_bytes(b"offline test fixture")
        self.config = self.swap / "launcher_config.json"

    def test_resolves_only_installer_selected_client(self):
        self.assertIsNone(client_executable(self.swap))
        self.config.write_text(json.dumps({"launcher": str(self.client)}),
                               encoding="utf-8")
        self.assertEqual(client_executable(self.swap), self.client)
        self.config.write_text(json.dumps({"launcher": str(
            self.game / "bin64_SteamRetail" / "Evolve.exe")}), encoding="utf-8")
        self.assertIsNone(client_executable(self.swap))

    def test_separately_reports_game_and_launcher_versions(self):
        self.config.write_text(json.dumps({"launcher": str(self.client)}),
                               encoding="utf-8")
        def version(path):
            return "3.6.1.2538" if Path(path).name == "Evolve.exe" else "0.1.54.0"
        with patch("installed_game_versions.windows_file_version",
                   side_effect=version) as read:
            result = installed_versions(self.game, self.swap)
        self.assertEqual(result["game"], "3.6.1.2538")
        self.assertEqual(result["client"], "0.1.54.0")
        self.assertEqual(result["client_name"], "ModdedEvolveLauncher.exe")
        self.assertTrue(result["client_found"])
        self.assertEqual(read.call_count, 2)

    def test_missing_or_unversioned_exe_is_explicit(self):
        report = installed_versions(None, self.swap)
        self.assertIsNone(report["game"])
        self.assertFalse(report["client_found"])
        with patch("installed_game_versions.windows_file_version",
                   return_value=None):
            report = installed_versions(self.game, self.swap)
        self.assertIsNone(report["game"])
        self.assertTrue(report["game_found"])

    def test_rejects_oversized_or_malformed_client_config(self):
        self.config.write_text("not json", encoding="utf-8")
        self.assertIsNone(client_executable(self.swap))
        self.config.write_text(" " * 20000, encoding="utf-8")
        self.assertIsNone(client_executable(self.swap))


if __name__ == "__main__":
    unittest.main()
