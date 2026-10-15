"""Reference-only transfer export tests, using synthetic imported-library bytes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from asset_library import _invalidate
from build_collections import register_build, assign_archive, prepare_transfer
from test_build_collections import fixture
from transfer_candidates import export_candidate


class TransferCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "AssetLibrary"
        self.root.mkdir()
        self.parent = Path(self.tmp.name)
        self.source_archive, self.target_archive = "a" * 20, "b" * 20
        fixture(self.root, self.source_archive, b"older character")
        fixture(self.root, self.target_archive, b"new character")
        self.source = register_build("Legacy", "legacy", root=self.root)
        self.target = register_build("Stage2", "stage2", root=self.root)
        assign_archive(self.source, self.source_archive, root=self.root)
        assign_archive(self.target, self.target_archive, root=self.root)
        _invalidate()

    def tearDown(self):
        _invalidate()

    def plan(self):
        return prepare_transfer(self.source, self.target,
                                ["characters/shared.dds"], root=self.root)

    def test_external_candidate_preserves_source_and_marks_noninstallable(self):
        name = "characters/shared.dds"
        src = self.root / ("shared.pak__" + self.source_archive) / "files" / name
        before = src.read_bytes()
        dest = self.parent / "review-bundle"
        manifest_path = export_candidate(self.plan(), dest, root=self.root)
        manifest = json.loads(manifest_path.read_text("utf-8"))
        self.assertFalse(manifest["installable"])
        self.assertFalse(manifest["signed"])
        self.assertFalse(manifest["game_compatibility_verified"])
        self.assertEqual(manifest["kind"], "legacy-to-stage2-review-bundle")
        self.assertEqual(manifest["assets"][0]["action"], "review-replace")
        self.assertEqual((dest / "assets" / name).read_bytes(), before)
        self.assertEqual(src.read_bytes(), before)

    def test_changed_source_refuses_copy(self):
        plan = self.plan()
        source = self.root / ("shared.pak__" + self.source_archive) / "files" / "characters/shared.dds"
        source.write_bytes(b"modified-source")
        destination = self.parent / "bad-bundle"
        with self.assertRaisesRegex(ValueError, "differ"):
            export_candidate(plan, destination, root=self.root)
        self.assertFalse(destination.exists())

    def test_missing_or_conflicting_reference_blocks_export(self):
        plan = self.plan()
        plan["mappings"][0]["action"] = "conflict"
        with self.assertRaisesRegex(ValueError, "conflicting"):
            export_candidate(plan, self.parent / "conflict", root=self.root)

    def test_stale_inventory_and_library_destination_rejected(self):
        plan = self.plan()
        register_build("Another", "legacy", root=self.root)
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            export_candidate(plan, self.parent / "stale", root=self.root)
        fresh = self.plan()
        with self.assertRaisesRegex(ValueError, "external"):
            export_candidate(fresh, self.root / "unsafe", root=self.root)

    def test_cancellation_and_preexisting_destination(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            export_candidate(plan, self.parent / "cancelled", root=self.root,
                             cancelled=lambda: True)
        output = self.parent / "already-there"
        output.mkdir()
        with self.assertRaises(ValueError):
            export_candidate(plan, output, root=self.root)


if __name__ == "__main__":
    unittest.main()
