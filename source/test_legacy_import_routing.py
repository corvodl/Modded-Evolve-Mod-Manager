"""Legacy PAK importer routing and source integrity regression tests."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from legacy_pak_import import legacy_project_root, try_import_legacy
from legacy_pak_workspace import load_project, stage_text, read_member, sha_file


class LegacyImportRoutingTests(unittest.TestCase):
    def test_unsigned_stored_and_deflated_paks_route_to_isolated_editor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "Engine.pak"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("Game/engine.cfg", "r_DisplayInfo=1\n",
                           compress_type=zipfile.ZIP_DEFLATED)
                z.writestr("Game/defaults.xml", "<Settings/>",
                           compress_type=zipfile.ZIP_STORED)
            before = sha_file(archive)
            project = try_import_legacy(archive, root / "Projects")
            self.assertEqual(project["status"], "legacy-project")
            self.assertEqual(project["kind"], "unsigned-ZIP")
            self.assertEqual(project["count"], 2)
            folder = Path(project["folder"])
            self.assertEqual(folder, legacy_project_root(archive, root / "Projects"))
            manifest = load_project(folder)
            self.assertEqual(read_member(manifest, "Game/engine.cfg"), b"r_DisplayInfo=1\n")
            stage_text(manifest, "Game/engine.cfg", "r_DisplayInfo=0\n", "utf-8")
            self.assertEqual(sha_file(archive), before)
            again = try_import_legacy(archive, root / "Projects")
            self.assertEqual(project["folder"], again["folder"])
            self.assertEqual(read_member(load_project(folder), "Game/engine.cfg"),
                             b"r_DisplayInfo=0\n")

    def test_invalid_and_signed_placeholder_are_not_mislabeled_legacy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "Shaders.pak"
            candidate.write_bytes(b"not a ZIP archive")
            self.assertIsNone(try_import_legacy(candidate, root / "Projects"))
            self.assertFalse((root / "Projects").exists())
            with zipfile.ZipFile(candidate, "w") as z:
                z.writestr("../unsafe.lua", "malicious")
            with self.assertRaises(ValueError):
                try_import_legacy(candidate, root / "Projects")
            self.assertFalse((root / "Projects").exists())


if __name__ == "__main__":
    unittest.main()
