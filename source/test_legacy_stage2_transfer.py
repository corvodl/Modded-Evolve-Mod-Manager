"""Synthetic Legacy -> Stage 2 map and model transfer tests.

No original game archives, keys, live Evolve folders or network are involved.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_library import _invalidate
from build_collections import register_build, assign_archive
from legacy_stage2_transfer import plan_compiled_map, export_map_review_bundle
from legacy_model_transfer import review_model_replacement, apply_reviewed_model
from test_crchf_models import fixture as model_fixture


class LegacyTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.lib = self.base / "AssetLibrary"
        self.lib.mkdir()
        self.legacy = register_build("Legacy 1.0", "legacy", root=self.lib)
        self.stage2 = register_build("Stage 2", "stage2", root=self.lib)
        self.stage = self.base / "signed-stage"
        self.stage.mkdir()
        _invalidate()
        self.addCleanup(_invalidate)

    def archive(self, bid, aid, relative_pak, entries):
        folder = self.lib / (Path(relative_pak).name + "__" + aid)
        files = folder / "files"
        files.mkdir(parents=True)
        manifest_entries = []
        for virtual, content in entries.items():
            target = files.joinpath(*virtual.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            manifest_entries.append({
                "path": virtual, "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content)})
        (folder / "asset-library.json").write_text(json.dumps({
            "version": 1, "name": Path(relative_pak).name,
            "signed_name": Path(relative_pak).name,
            "source": str(self.base / "synthetic-original" / relative_pak),
            "source_sha256": "0" * 64,
            "entries": manifest_entries,
            "extracted_bytes": sum(row["bytes"] for row in manifest_entries),
        }), encoding="utf-8")
        assign_archive(bid, aid, root=self.lib, relative_path=relative_pak)
        _invalidate()
        return files

    def map_setup(self):
        core = self.archive(self.legacy, "a" * 20,
            "Game/Levels/Desert_Harsh/level.pak", {
                "levelinfo.xml": b"<LevelInfo/>",
                "terrain/terrain.dat": b"compiled terrain",
                "brush/rocks.cgf": b"synthetic geometry"})
        companion = self.archive(self.legacy, "b" * 20,
            "Game/Levels/Desert_Harsh/Level_Desert_Harsh_data.pak", {
                "objects/cactus.cgf": b"synthetic cactus"})
        self.archive(self.legacy, "c" * 20,
            "Game/Levels/Forest_Ruins/level.pak", {
                "terrain/terrain.dat": b"unrelated forest"})
        self.archive(self.stage2, "d" * 20,
            "Game/Levels/Desert_Canyon/level.pak", {
                "terrain/terrain.dat": b"Stage 2 canyon"})
        return core, companion

    def test_compiled_map_inventory_is_build_and_map_scoped(self):
        self.map_setup()
        result = plan_compiled_map(self.legacy, self.stage2, "Desert_Harsh",
                                   root=self.lib, selected_archive="a" * 20)
        self.assertEqual(len(result["archives"]), 2)
        self.assertEqual(set(a["role"] for a in result["archives"]),
                         {"compiled-level", "map-data"})
        self.assertTrue(result["requires_new_archive_support"])
        self.assertFalse(result["target_has_compiled_level"])
        self.assertTrue(result["compiled_terrain_present"])
        self.assertFalse(result["source_heightmap_present"])
        self.assertEqual(result["runtime_compatibility"], "unverified")
        self.assertFalse(result["installable"])
        self.assertEqual(result["extracted_file_count"], 4)
        self.assertEqual(result["virtual_path_conflicts"], [])

    def test_map_bundle_only_copies_local_verified_payloads(self):
        self.map_setup()
        output = self.base / "map-research-export"
        result = export_map_review_bundle(
            self.legacy, self.stage2, "Desert_Harsh", output, root=self.lib)
        self.assertEqual(len(result["exported_files"]), 4)
        self.assertEqual((output / "archives" / ("a" * 20) /
                          "terrain" / "terrain.dat").read_bytes(), b"compiled terrain")
        self.assertFalse((output / "archives" / ("c" * 20)).exists())
        self.assertTrue((output / "transfer-review.json").is_file())
        self.assertFalse((output / "INCOMPLETE.txt").exists())
        self.assertIn("NOT A WORKING", (output / "READ-ME-FIRST.txt").read_text())

    def test_hash_mismatch_keeps_incomplete_marker(self):
        core, _ = self.map_setup()
        (core / "terrain" / "terrain.dat").write_bytes(b"not the same")
        destination = self.base / "tampered"
        with self.assertRaisesRegex(ValueError, "size|hash"):
            export_map_review_bundle(self.legacy, self.stage2, "Desert_Harsh",
                                     destination, root=self.lib)
        self.assertTrue((destination / "INCOMPLETE.txt").is_file())

    def test_wrong_map_build_and_output_paths_rejected(self):
        self.map_setup()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            plan_compiled_map(self.legacy, self.stage2, "Desert_Harsh",
                              root=self.lib, selected_archive="c" * 20)
        with self.assertRaisesRegex(ValueError, "Source"):
            plan_compiled_map(self.stage2, self.legacy, "Desert_Harsh", root=self.lib)
        with self.assertRaisesRegex(ValueError, "outside the Asset Library"):
            export_map_review_bundle(self.legacy, self.stage2, "Desert_Harsh",
                                     self.lib / "unsafe", root=self.lib)

    def test_map_collision_is_reported_not_silently_resolved(self):
        self.map_setup()
        self.archive(self.legacy, "e" * 20,
             "Game/Levels/Desert_Harsh/Level_Desert_Harsh_ts.pak",
             {"terrain/terrain.dat": b"conflicting compiled terrain"})
        plan = plan_compiled_map(self.legacy, self.stage2, "Desert_Harsh",
                                 root=self.lib)
        self.assertEqual(len(plan["virtual_path_conflicts"]), 1)
        self.assertFalse(plan["installable"])

    def model_setup(self):
        path = "objects/effects/mesh.cgf"
        old, new = model_fixture(b"X"), model_fixture(b"Y")
        self.archive(self.legacy, "f" * 20, "Game/Objects/effects.pak", {path: new})
        self.archive(self.stage2, "0" * 20, "Game/Objects/effects.pak", {path: old})
        target_pak = self.stage / "paks" / "Game" / "Objects" / "effects.pak"
        target_pak.parent.mkdir(parents=True)
        target_pak.write_bytes(b"synthetic signed Stage 2 pak")
        (self.stage / "rekey_plan.json").write_text(json.dumps({
            "entries": [{"kind": "signed-encrypted",
                         "path": "Game/Objects/effects.pak"}],
            "blocked": False}))
        project = self.base / "Projects" / "existing-stage2-project"
        file = project / "files" / "objects" / "effects" / "mesh.cgf"
        file.parent.mkdir(parents=True)
        file.write_bytes(old)
        (project / ".evolve-pak-workspace.json").write_text(json.dumps({
            "version": 1, "source_pak": str(target_pak),
            "source_sha256": hashlib.sha256(target_pak.read_bytes()).hexdigest(),
            "entries": [{
                "path": path, "format": "raw",
                "sha256": hashlib.sha256(old).hexdigest()}],
        }))
        return path, project, file, old, new, target_pak

    def test_review_and_apply_replaces_only_isolated_workspace_model(self):
        name, project, file, before, after, staged = self.model_setup()
        review = review_model_replacement(
            self.legacy, self.stage2, name, project, self.stage,
            root=self.lib, source_archive="f" * 20)
        self.assertFalse(review["validated_in_game"])
        self.assertFalse(review["signed_pak_written"])
        self.assertEqual(file.read_bytes(), before)
        result = apply_reviewed_model(review, self.stage, root=self.lib)
        self.assertTrue(result["changed"])
        self.assertEqual(file.read_bytes(), after)
        self.assertEqual(len(list((project / "EditorBackups").rglob("mesh.cgf"))), 1)
        self.assertEqual(staged.read_bytes(), b"synthetic signed Stage 2 pak")

    def test_model_transfer_refuses_changed_workspace_and_staging(self):
        name, project, file, before, after, staged = self.model_setup()
        plan = review_model_replacement(
            self.legacy, self.stage2, name, project, self.stage, root=self.lib)
        file.write_bytes(b"modified separately")
        with self.assertRaisesRegex(ValueError, "edited"):
            apply_reviewed_model(plan, self.stage, root=self.lib)
        file.write_bytes(before)
        staged.write_bytes(b"changed signed baseline")
        with self.assertRaisesRegex(ValueError, "staged signing baseline"):
            apply_reviewed_model(plan, self.stage, root=self.lib)

    def test_model_transfer_refuses_missing_target_or_incompatible_payload(self):
        name, project, file, before, after, _ = self.model_setup()
        with self.assertRaisesRegex(ValueError, "not own"):
            review_model_replacement(self.legacy, self.stage2, name,
                                     project, self.stage, root=self.lib,
                                     source_archive="a" * 20)
        source = self.lib / ("effects.pak__" + "f" * 20) / "files" / "objects" / "effects" / "mesh.cgf"
        source.write_bytes(after + b"more data")
        with self.assertRaisesRegex(ValueError, "provenance"):
            review_model_replacement(self.legacy, self.stage2, name,
                                     project, self.stage, root=self.lib)


if __name__ == "__main__":
    unittest.main()
