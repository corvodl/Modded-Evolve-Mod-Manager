"""Build-wide Legacy->Stage 2 transfer tests use only tiny fabricated archives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from asset_library import _invalidate
from build_collections import register_build, assign_archive
from legacy_bulk_transfer import (
    plan_legacy_build_export, export_legacy_build_bundle,
)


class LegacyBulkTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.lib = self.home / "AssetLibrary"
        self.lib.mkdir()
        self.legacy_game = self.home / "Legacy" / "EvolveGame"
        self.stage2_game = self.home / "Stage2" / "EvolveGame"
        self.legacy_game.mkdir(parents=True)
        self.stage2_game.mkdir(parents=True)
        self.stage = self.home / "signed-stage"
        self.stage.mkdir()
        self.legacy = register_build("Legacy", "legacy", root=self.lib,
                                     location=self.legacy_game)
        self.stage2 = register_build("Stage 2", "stage2", root=self.lib,
                                     location=self.stage2_game)
        _invalidate()
        self.addCleanup(_invalidate)

    def archive(self, build, aid, archive_path, payloads, *, source_root=None):
        folder = self.lib / ("archive__" + aid)
        files = folder / "files"
        files.mkdir(parents=True)
        records = []
        for name, data in payloads.items():
            dest = files.joinpath(*name.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            records.append({
                "path": name, "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            })
        root = source_root or (self.legacy_game if build == self.legacy
                               else self.stage2_game)
        (folder / "asset-library.json").write_text(json.dumps({
            "version": 1,
            "source": str(root / archive_path),
            "name": Path(archive_path).name,
            "source_sha256": hashlib.sha256(aid.encode()).hexdigest(),
            "entries": records,
            "extracted_bytes": sum(x["bytes"] for x in records),
        }))
        assign_archive(build, aid, root=self.lib, relative_path=archive_path)
        _invalidate()
        return files

    def assets(self):
        self.archive(self.legacy, "a" * 20, "Game/Shared/assets.pak", {
            "textures/foo.dds": b"same-DDS",
            "textures/foo.dds.0": b"highest-raw-stream",
            "objects/effects/plate.cgf": b"legacy-model",
            "materials/plate.mtl": b"<Material/>",
            "scripts/Legacy/Defend.lua": b"\x1bLuaQ",
        })
        self.archive(self.stage2, "b" * 20, "Game/Shared/assets.pak", {
            "textures/foo.dds": b"same-DDS",
            "objects/effects/plate.cgf": b"stage2-model",
            "materials/plate.mtl": b"<Material/>",
        })
        self.archive(self.legacy, "c" * 20,
                     "Game/Levels/Desert_Harsh/level.pak", {
                         "levelinfo.xml": b"<LevelInfo/>",
                         "terrain/terrain.dat": b"legacy desert",
                     })
        self.archive(self.legacy, "d" * 20,
                     "Game/Levels/Forest_Rapids/level.pak", {
                         "levelinfo.xml": b"<LevelInfo/>",
                         "terrain/terrain.dat": b"legacy forest",
                     })
        self.archive(self.stage2, "e" * 20,
                     "Game/Levels/Desert_Canyon/level.pak", {
                         "levelinfo.xml": b"<LevelInfo/>",
                         "terrain/terrain.dat": b"stage2 canyon",
                     })

    def test_whole_imported_build_has_target_aware_statuses_and_map_scope(self):
        self.assets()
        plan = plan_legacy_build_export(self.legacy, self.stage2, root=self.lib)
        self.assertEqual(plan["type"], "legacy-stage2-build-transfer-review")
        self.assertEqual(plan["source_imported_archive_count"], 4)
        self.assertEqual(plan["target_imported_archive_count"], 2)
        self.assertEqual(plan["file_count"], 9)  # five global, two per map
        self.assertFalse(plan["game_files_modified"])
        self.assertFalse(plan["installer_written"])
        by_key = {(row["scope"], row["path"]): row for row in plan["files"]}
        same = by_key["global", "textures/foo.dds"]
        self.assertEqual(same["action"], "same-payload-candidate")
        self.assertFalse(same["copy_for_review"])
        self.assertEqual(by_key["global", "objects/effects/plate.cgf"]["action"],
                         "existing-path-review")
        self.assertEqual(by_key["global", "textures/foo.dds.0"]["action"],
                         "not-in-imported-stage2-catalog")
        self.assertEqual(by_key["global", "scripts/Legacy/Defend.lua"]["asset_type"],
                         "binary-script-review")
        self.assertIn(("map:desert_harsh", "terrain/terrain.dat"), by_key)
        self.assertIn(("map:forest_rapids", "terrain/terrain.dat"), by_key)
        self.assertEqual(by_key["map:desert_harsh", "terrain/terrain.dat"]["action"],
                         "not-in-imported-stage2-catalog")
        self.assertNotIn(("map:desert_canyon", "terrain/terrain.dat"), by_key)
        self.assertEqual(plan["files_to_copy"], 7)

    def test_full_build_review_export_preserves_sidecars_and_map_owners(self):
        self.assets()
        destination = self.home / "legacy-to-stage2-export"
        result = export_legacy_build_bundle(
            self.legacy, self.stage2, destination,
            root=self.lib, protected_roots=(self.stage,))
        self.assertFalse(result["installed"])
        self.assertEqual(result["files_exported"], 7)
        self.assertTrue((destination / "stage2-transfer-review.json").is_file())
        self.assertFalse((destination / "INCOMPLETE.txt").exists())
        self.assertEqual(
            (destination / "assets" / ("a" * 20) /
             "textures" / "foo.dds.0").read_bytes(), b"highest-raw-stream")
        self.assertEqual(
            (destination / "assets" / ("c" * 20) /
             "terrain" / "terrain.dat").read_bytes(), b"legacy desert")
        self.assertEqual(
            (destination / "assets" / ("d" * 20) /
             "terrain" / "terrain.dat").read_bytes(), b"legacy forest")
        self.assertFalse((destination / "assets" / ("e" * 20)).exists())
        self.assertFalse((self.stage / "paks").exists())
        self.assertFalse((self.stage2_game / "Game").exists())
        self.assertFalse((self.legacy_game / "Game").exists())

    def test_export_scope_selected_archive_prefix_and_include_identical(self):
        self.assets()
        single = plan_legacy_build_export(self.legacy, self.stage2, root=self.lib,
                                           source_archive="a" * 20, prefix="textures")
        self.assertEqual(single["file_count"], 2)
        self.assertEqual(single["files_to_copy"], 1)
        all_texture = plan_legacy_build_export(
            self.legacy, self.stage2, root=self.lib, source_archive="a" * 20,
            prefix="textures", include_identical=True)
        self.assertEqual(all_texture["files_to_copy"], 2)
        self.assertGreater(all_texture["bytes_to_copy"], single["bytes_to_copy"])
        with self.assertRaisesRegex(ValueError, "does not belong"):
            plan_legacy_build_export(
                self.legacy, self.stage2, root=self.lib,
                source_archive="e" * 20)
        with self.assertRaisesRegex(ValueError, "No imported"):
            plan_legacy_build_export(self.legacy, self.stage2, root=self.lib,
                                     prefix="does/not/exist")

    def test_plan_only_has_no_payload_files(self):
        self.assets()
        destination = self.home / "inventory-only"
        report = export_legacy_build_bundle(
            self.legacy, self.stage2, destination, root=self.lib,
            plan_only=True, protected_roots=(self.stage,))
        self.assertEqual(report["files_exported"], 0)
        self.assertTrue(report["inventory_only"])
        self.assertFalse((destination / "assets").exists())
        plan = json.loads((destination / "stage2-transfer-review.json").read_text())
        self.assertEqual(plan["export_mode"], "inventory-only")

    def test_source_conflict_does_not_select_arbitrary_provider(self):
        self.assets()
        self.archive(self.legacy, "f" * 20, "Game/Shared/collision.pak",
                     {"objects/effects/plate.cgf": b"different legacy geometry"})
        plan = plan_legacy_build_export(self.legacy, self.stage2, root=self.lib)
        item = next(row for row in plan["files"]
                    if row["path"] == "objects/effects/plate.cgf")
        self.assertEqual(item["action"], "ambiguous-legacy-source")
        self.assertFalse(item["copy_for_review"])
        self.assertEqual(len(item["source_providers"]), 2)

    def test_rejects_changed_source_and_preserves_incomplete_marker(self):
        self.assets()
        self.lib.joinpath("archive__" + "a" * 20, "files", "textures",
                          "foo.dds.0").write_bytes(b"tampered-bytes")
        destination = self.home / "bad-bundle"
        with self.assertRaisesRegex(ValueError, "hash|size"):
            export_legacy_build_bundle(
                self.legacy, self.stage2, destination, root=self.lib)
        self.assertTrue((destination / "INCOMPLETE.txt").is_file())

    def test_output_does_not_touch_game_library_stage_or_existing_project(self):
        self.assets()
        for target in (self.lib / "no", self.legacy_game / "bundle",
                       self.stage2_game / "bundle", self.stage / "no"):
            with self.assertRaisesRegex(ValueError, "outside game"):
                export_legacy_build_bundle(
                    self.legacy, self.stage2, target, root=self.lib,
                    protected_roots=(self.stage,))
            self.assertFalse(target.exists())
        self.assertFalse((self.legacy_game / "Game").exists())

    def test_cancel_before_copy_and_changed_inventory(self):
        self.assets()
        cancelled = lambda: True
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            export_legacy_build_bundle(
                self.legacy, self.stage2, self.home / "cancelled",
                root=self.lib, cancelled=cancelled)
        self.assertFalse((self.home / "cancelled").exists())
        plan = plan_legacy_build_export(self.legacy, self.stage2, root=self.lib)
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            export_legacy_build_bundle(
                self.legacy, self.stage2, self.home / "stale",
                root=self.lib, expected_inventory_sha256="0" * 64)
        self.assertFalse((self.home / "stale").exists())
        self.assertEqual(len(plan["inventory_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
