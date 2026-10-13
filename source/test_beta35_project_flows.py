"""Synthetic beta.35 library source and project-ownership regressions.

No game executable, historical data, private keys or original archives needed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_library_bulk import discover_collection_paks
from project_catalog import register_project, project_rows, verified_target


class SelectedCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.source = self.root / "legacy_source"
        (self.source / "nested").mkdir(parents=True)
        (self.source / "nested" / "level.pak").write_bytes(b"synthetic")
        (self.source / "readme.txt").write_text("not an archive", encoding="utf-8")

    def test_no_exe_is_needed_and_source_is_contained(self):
        with patch("asset_library.imported_archives", return_value=()):
            scan = discover_collection_paks(self.source, library_root=self.library)
        self.assertEqual(len(scan.files), 1)
        self.assertEqual(scan.files[0], (self.source / "nested" / "level.pak").resolve())
        self.assertEqual(scan.packed_bytes, len(b"synthetic"))
        self.assertEqual(scan.already_imported, 0)

    def test_reused_archives_are_counted(self):
        file = self.source / "nested" / "level.pak"
        with patch("asset_library.imported_archives",
                   return_value=({"source": str(file.resolve())},)):
            scan = discover_collection_paks(self.source, library_root=self.library)
        self.assertEqual(scan.already_imported, 1)

    def test_symlink_is_skipped(self):
        external = self.root / "other"
        external.mkdir()
        (external / "outsider.pak").write_bytes(b"outside")
        link = self.source / "linked"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("Platform does not permit directory symlinks")
        with patch("asset_library.imported_archives", return_value=()):
            scan = discover_collection_paks(self.source, library_root=self.library)
        self.assertEqual(len(scan.files), 1)


class ProjectOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = self.root / "Projects"
        self.workspace = self.projects / "sample"
        self.workspace.mkdir(parents=True)
        (self.workspace / ".evolve-pak-workspace.json").write_text("{}", encoding="utf-8")
        self.stage = self.root / "stage"
        self.target = self.stage / "paks" / "Game" / "my_mod.pak"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"synthetic staging baseline")
        self.manifest = {"source_pak": str(self.target)}
        self.patchers = [
            patch("evolve_pak_workspace.load_workspace", return_value=self.manifest),
            patch("universal_stage.allowed_paks",
                  return_value={"Game/my_mod.pak": "Game/my_mod.pak"}),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)

    def test_exact_target_does_not_depend_on_table_selection(self):
        rel, pak = verified_target(self.workspace, self.stage)
        self.assertEqual(rel, "Game/my_mod.pak")
        self.assertEqual(pak, self.target.resolve())

    def test_project_catalog_roundtrip_and_changes(self):
        project_id = register_project(
            self.projects, self.workspace, self.stage,
            source_build_id="source", archive_id="a" * 20, label="Map edits")
        with patch("asset_intelligence.validate_project", return_value=(("foo.xml",), ())):
            rows = project_rows(self.projects, self.stage)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], project_id)
        self.assertEqual(rows[0]["source_build_id"], "source")
        self.assertEqual(rows[0]["status"], "modified")
        self.assertEqual(rows[0]["changed"], 1)

    def test_missing_target_never_falls_back_to_same_basename(self):
        moved = self.root / "external" / "my_mod.pak"
        moved.parent.mkdir()
        moved.write_bytes(b"foreign PAK")
        with patch("evolve_pak_workspace.load_workspace",
                   return_value={"source_pak": str(moved)}):
            with self.assertRaises(ValueError):
                verified_target(self.workspace, self.stage)


if __name__ == "__main__":
    unittest.main()
