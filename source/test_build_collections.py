"""Synthetic build-inventory tests; no proprietary archives required."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_library import MANIFEST, extracted_index, _invalidate
from build_collections import (catalog, register_build, assign_archive, remove_membership,
                               build_index, compare_builds, prepare_transfer, update_location)


def fixture(root, identifier, content, name="shared.pak"):
    folder = root / (name + "__" + identifier)
    target = folder / "files" / "characters" / "shared.dds"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    (folder / MANIFEST).write_text(json.dumps({
        "version": 1, "name": name, "source": "/synthetic/" + identifier,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "extracted_bytes": len(content), "entries": [{
            "path": "characters/shared.dds", "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest()}]}), encoding="utf-8")


class BuildCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.one = "a" * 20
        self.two = "b" * 20
        fixture(self.root, self.one, b"legacy")
        fixture(self.root, self.two, b"stage2")
        _invalidate()

    def tearDown(self):
        self.tmp.cleanup()
        _invalidate()

    def test_migration_and_isolation(self):
        self.assertEqual(len(catalog(self.root)["builds"]["unassigned"]["members"]), 2)
        old = register_build("Legacy", "legacy", root=self.root)
        new = register_build("Stage 2", "stage2", root=self.root)
        assign_archive(old, self.one, root=self.root)
        assign_archive(new, self.two, root=self.root)
        self.assertEqual(len(build_index(old, root=self.root)["characters/shared.dds"]), 1)
        self.assertEqual(build_index(old, root=self.root)["characters/shared.dds"][0]["_asset_library"], self.one)
        self.assertEqual(build_index(new, root=self.root)["characters/shared.dds"][0]["_asset_library"], self.two)
        self.assertEqual(compare_builds(old, new, root=self.root)[0]["status"], "changed")
        plan = prepare_transfer(old, new, ["characters/shared.dds"], root=self.root,
                                target_baseline_sha256="a" * 64)
        self.assertEqual(plan["staging"], "none")
        self.assertFalse(plan["mappings"][0]["validated_in_game"])

    def test_shared_membership_remove_and_relocation(self):
        first = register_build("First", "early", root=self.root, location="/old")
        second = register_build("Second", "legacy", root=self.root)
        assign_archive(first, self.one, root=self.root)
        assign_archive(second, self.one, root=self.root)
        update_location(first, "/new", root=self.root)
        self.assertEqual(len(build_index(first, root=self.root)), 1)
        remove_membership(first, self.one, root=self.root)
        self.assertEqual(build_index(first, root=self.root), {})
        self.assertEqual(len(build_index(second, root=self.root)), 1)
        self.assertTrue((self.root / ("shared.pak__" + self.one)).exists())

    def test_foreign_dependency_rejected(self):
        from multi_pak_assets import _resolve
        source = register_build("Historical", "legacy", root=self.root)
        target = register_build("Target", "stage2", root=self.root)
        assign_archive(source, self.one, root=self.root)
        indexed = build_index(source, root=self.root)
        self.assertEqual(_resolve(indexed, "characters/shared.dds", build_id=target)[1], "missing")
        self.assertEqual(_resolve(indexed, "characters/shared.dds", build_id=source)[1], "found")
        with self.assertRaises(ValueError):
            _resolve(indexed, "characters/shared.dds", build_id="all")

    def test_inventory_change_requires_review(self):
        build = register_build("Snapshot", "legacy", root=self.root)
        assign_archive(build, self.one, root=self.root)
        with self.assertRaisesRegex(ValueError, "create a new snapshot"):
            assign_archive(build, self.two, root=self.root)

    def test_shared_contents_cannot_be_removed_while_assigned(self):
        from asset_library import remove_archive
        first = register_build("One", "legacy", root=self.root)
        second = register_build("Two", "legacy", root=self.root)
        assign_archive(first, self.one, root=self.root)
        assign_archive(second, self.one, root=self.root)
        with self.assertRaises(ValueError):
            remove_archive(self.one, self.root)
        remove_membership(first, self.one, root=self.root)
        self.assertIn("characters/shared.dds", build_index(second, root=self.root))
        with self.assertRaises(ValueError):
            remove_archive(self.one, self.root)

    def test_multiple_preview_memberships_require_context(self):
        from asset_library_preview import library_model_index
        first = register_build("One", "legacy", root=self.root)
        second = register_build("Two", "stage2", root=self.root)
        assign_archive(first, self.one, root=self.root)
        assign_archive(second, self.one, root=self.root)
        with self.assertRaisesRegex(ValueError, "multiple build memberships"):
            library_model_index(self.one, root=self.root)
        self.assertEqual(len(library_model_index(
            self.one, root=self.root, build_id=second)["characters/shared.dds"]), 1)

    def test_readable_folder_stage_source(self):
        from asset_library_bulk import stage_source_for_import
        folder = self.root / ("shared.pak__" + self.one)
        manifest_path = folder / MANIFEST
        record = json.loads(manifest_path.read_text("utf-8"))
        source_dir = tempfile.TemporaryDirectory()
        outside = Path(source_dir.name) / "shared.pak"
        stage = self.root / "stage"
        try:
            outside.write_bytes(b"synthetic-stage-archive")
            record["source"] = str(outside)
            manifest_path.write_text(json.dumps(record), encoding="utf-8")
            (stage / "paks").mkdir(parents=True)
            (stage / "paks" / "shared.pak").write_bytes(b"synthetic-staged")
            (stage / "mykeys").mkdir()
            (stage / "mykeys" / "public_key.bin").write_bytes(b"placeholder")
            (stage / "mykeys" / "private_key.pem").write_bytes(b"placeholder")
            with patch("universal_stage.allowed_paks", return_value={"file": "shared.pak"}):
                rel, staged = stage_source_for_import(
                    self.one, stage, library_root=self.root)
            self.assertEqual(rel, "shared.pak")
            self.assertEqual(staged, stage / "paks" / "shared.pak")
        finally:
            source_dir.cleanup()

    def test_target_baseline_guard_and_transfer_cancel(self):
        from asset_library import ImportCanceled
        first = register_build("Legacy", "legacy", root=self.root)
        second = register_build("Stage 2", "stage2", root=self.root)
        assign_archive(first, self.one, root=self.root)
        assign_archive(second, self.two, root=self.root)
        with self.assertRaises(ValueError):
            prepare_transfer(first, second, ["characters/shared.dds"],
                             root=self.root, target_baseline_sha256="invalid")
        with self.assertRaises(ImportCanceled):
            prepare_transfer(first, second, ["characters/shared.dds"], root=self.root,
                             cancelled=lambda: True)

    def test_invalid_contexts(self):
        with self.assertRaises(ValueError):
            build_index("all", root=self.root)
        stage = register_build("Stage", "stage2", root=self.root)
        with self.assertRaises(ValueError):
            prepare_transfer("unassigned", stage, [], root=self.root)


if __name__ == "__main__":
    unittest.main()
