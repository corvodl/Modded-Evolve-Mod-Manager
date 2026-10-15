"""Full-game archive discovery and signed Asset Library project handoff."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asset_library as lib
from asset_library_bulk import (
    discover_game_paks, stage_source_for_import, project_command)


class BulkGamePAKTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.game = self.base / "EvolveGame"
        (self.game / "bin64_SteamRetail").mkdir(parents=True)
        (self.game / "bin64_SteamRetail" / "Evolve.exe").write_bytes(b"game")
        self.directory = self.game / "Game" / "characters"
        self.directory.mkdir(parents=True)
        self.original = self.directory / "characters_hunters_shared_ts.pak"
        self.original.write_bytes(b"signed source")
        self.other = self.game / "Game" / "animationscaf.pak"
        self.other.write_bytes(b"another source")
        self.library = self.base / "AssetLibrary"
        self.stage = self.base / "stage"
        stage_keys = self.stage / "mykeys"
        stage_keys.mkdir(parents=True)
        (stage_keys / "public_key.bin").write_bytes(b"fake stage public")
        (stage_keys / "private_key.pem").write_bytes(b"fake stage private")
        self.relative = "Game/characters/characters_hunters_shared_ts.pak"
        destination = self.stage / "paks" / "Game" / "characters"
        destination.mkdir(parents=True)
        (destination / self.original.name).write_bytes(b"custom signed source")
        (self.stage / "rekey_plan.json").write_text(json.dumps({
            "blocked": False, "entries": [
                {"kind": "signed-encrypted", "path": self.relative}
            ]
        }))

    def _manifest(self):
        import hashlib
        archive_hash = hashlib.sha256(
            (self.original.name.casefold() + "|" +
             lib._sha_file(self.original)).encode()).hexdigest()[:20]
        folder = self.library / archive_hash
        folder.mkdir(parents=True)
        entry = "characters/hunters/shared/textures/test.dds"
        child = folder / "files" / entry
        child.parent.mkdir(parents=True)
        child.write_bytes(b"xyz")
        (folder / "asset-library.json").write_text(json.dumps({
            "version": 1, "name": self.original.name,
            "source": str(self.original), "signed_name": self.original.name,
            "source_sha256": lib._sha_file(self.original),
            "entries": [{"path": entry, "bytes": 3,
                         "sha256": lib._sha_file(child)}],
            "extracted_bytes": 3
        }))
        return archive_hash

    def test_recursive_game_scan_only_reads_pak_files(self):
        (self.game / "another.zip").write_bytes(b"zip")
        (self.game / "Game" / "ignore.pak.modbackup").write_bytes(b"backup")
        with patch("asset_library.imported_archives", return_value=[]):
            result = discover_game_paks(self.game, library_root=self.library)
        self.assertEqual(len(result.files), 2)
        self.assertEqual(set(result.files), {self.original.resolve(), self.other.resolve()})
        self.assertEqual(result.packed_bytes,
                         self.original.stat().st_size + self.other.stat().st_size)
        self.assertGreater(result.available_bytes, 0)

    def test_import_scan_reuses_owned_imports_on_retry(self):
        ident = self._manifest()
        with patch("asset_library.imported_archives", return_value=[
            {"source": str(self.original)}]):
            scan = discover_game_paks(self.game, library_root=self.library)
        self.assertEqual(scan.already_imported, 1)
        self.assertEqual(len(scan.files), 2)

    def test_full_game_scan_rejects_invalid_game_root(self):
        with self.assertRaisesRegex(ValueError, "EvolveGame"):
            discover_game_paks(self.base, library_root=self.library)

    def test_stage_match_uses_original_relative_path(self):
        ident = self._manifest()
        name, staged = stage_source_for_import(
            ident, self.stage, self.game, library_root=self.library)
        self.assertEqual(name, self.relative)
        self.assertTrue(staged.is_file())
        project = self.base / "Projects" / "New Mod"
        name, command = project_command(
            ident, self.stage, project, self.game, library_root=self.library)
        self.assertEqual(name, self.relative)
        self.assertIn("extract", command)
        self.assertEqual(command[command.index("--pak") + 1],
                         str(staged))
        self.assertEqual(command[command.index("--workspace") + 1],
                         str(project.resolve()))

    def test_reference_only_if_no_stage_plan_match(self):
        ident = self._manifest()
        (self.stage / "rekey_plan.json").write_text(json.dumps({
            "blocked": False, "entries": [{
                "kind": "signed-encrypted",
                "path": "Game/other.pak"}]}))
        with self.assertRaisesRegex(ValueError, "staged PAK"):
            stage_source_for_import(
                ident, self.stage, self.game, library_root=self.library)

    def test_no_unsafe_project_destination_or_missing_key(self):
        ident = self._manifest()
        with self.assertRaisesRegex(ValueError, "inside signed staging"):
            project_command(ident, self.stage, self.stage / "Projects" / "New",
                            self.game, library_root=self.library)
        (self.stage / "mykeys" / "private_key.pem").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "signing"):
            stage_source_for_import(
                ident, self.stage, self.game, library_root=self.library)


if __name__ == "__main__":
    unittest.main()
