"""Synthetic build-inventory tests; no proprietary archives required."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path

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

    def test_invalid_contexts(self):
        with self.assertRaises(ValueError):
            build_index("all", root=self.root)
        stage = register_build("Stage", "stage2", root=self.root)
        with self.assertRaises(ValueError):
            prepare_transfer("unassigned", stage, [], root=self.root)


if __name__ == "__main__":
    unittest.main()
