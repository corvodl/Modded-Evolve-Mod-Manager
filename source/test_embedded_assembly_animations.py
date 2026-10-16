"""Assembly GLBs embed only validated Actions for each exact CDF skeleton.

Synthetic data and converter stubs: no Evolve binaries, private PAKs or keys.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from character_animation_catalog import AnimationReference
from character_animation_export import embed_verified_assembly_animations
from character_asset_export import build_cdf_folder_glbs
from model_textured_export import _pack_glb, _unpack_glb
from test_character_export_folders import test_glb, synthetic_caf


def rigged_glb():
    doc, data = _unpack_glb(test_glb())
    doc["skins"] = [{"joints": [0]}]
    return _pack_glb(doc, data)


def with_action(source, name):
    doc, data = _unpack_glb(source)
    doc.setdefault("animations", []).append({
        "name": name,
        "channels": [{"sampler": 0, "target": {
            "node": 0, "path": "translation"}}],
        "samplers": [{"input": 0, "output": 0}]
    })
    return _pack_glb(doc, data)


class EmbeddedAssemblyAnimationTests(TestCase):
    CDF = "characters/monsters/example/first.cdf"

    def character(self, name=None, historical=False):
        name = name or self.CDF
        parts = (("objects/example/body.chr", Path("body.chr"), None, 1),) if historical else ()
        return SimpleNamespace(cdf=name, skeleton="objects/example/skeleton.chr",
                               rig_parts=parts, included=())

    def test_verified_native_caf_actions_embedded_in_exact_rig(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "idle.caf"
            source.write_bytes(synthetic_caf())
            reference = AnimationReference("idle", "animations/example/idle.caf",
                                           "caf", True, source)
            base = rigged_glb()
            def validated(blob, tracks):
                return with_action(blob, tracks[0][0]), {
                    "clips": 1, "bone_channels": 1}
            with patch("native_caf.append_caf_animations",
                       side_effect=validated):
                animated, result = embed_verified_assembly_animations(
                    base, self.character(), {}, root, references=(reference,))
            doc, _ = _unpack_glb(animated)
            self.assertEqual([a["name"] for a in doc["animations"]], ["idle"])
            self.assertEqual(doc["skins"], _unpack_glb(base)[0]["skins"])
            self.assertEqual(result["animations"], 1)
            self.assertEqual(result["converted_clips"], ["idle"])
            self.assertTrue(result["animated_glb_embedded"])

    def test_2012_dba_reuses_manager_historical_converter(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dba = root / "test.dba"
            dba.write_bytes(b"DBA payload" * 3)
            reference = AnimationReference(
                "DBA: example", "animations/example/example.dba", "dba",
                True, dba)
            base = rigged_glb()
            character = self.character(historical=True)
            with patch("character_animation_catalog.native_clip_playability",
                       return_value=("converter", "Historical DBA")), patch(
                    "character_animation_catalog.preferred_third_person_dba",
                    return_value=reference), patch(
                    "database_animation.convert_historical_database_motion",
                    return_value=with_action(base, "historical_idle")) as legacy:
                animated, result = embed_verified_assembly_animations(
                    base, character, {}, root, references=(reference,))
            self.assertEqual(result["converted_clips"], ["historical_idle"])
            self.assertEqual(_unpack_glb(animated)[0]["animations"][0]["name"],
                             "historical_idle")
            legacy.assert_called_once()

    def test_unrigged_cdf_never_invents_animation(self):
        with patch("character_animation_export.export_character_animation_folder"
                   ) as exporter:
            unrigged = test_glb()
            actual, info = embed_verified_assembly_animations(
                unrigged, self.character(), {}, Path("."))
        self.assertEqual(actual, unrigged)
        self.assertEqual(info["animations"], 0)
        self.assertIn("no single verified armature", info["note"])
        exporter.assert_not_called()

    def test_incorrect_character_cdf_rejected(self):
        with self.assertRaisesRegex(ValueError, "do not match"):
            embed_verified_assembly_animations(
                rigged_glb(), self.character(), {}, Path("."),
                relative="characters/monsters/other.cdf")

    def test_each_assemblies_glb_receives_only_its_own_actions(self):
        prefix = "characters/monsters/example"
        names = [prefix + "/first.cdf", prefix + "/second.cdf"]
        index = {name.casefold(): [{"file": "/unused"}] for name in names}
        def assemble(workspace, cdf, idx, **kwargs):
            return self.character(name=cdf)
        def embed(blob, character, index, workspace, **kwargs):
            return with_action(blob, Path(character.cdf).stem + "_idle"), {
                "animations": 1,
                "converted_clips": [Path(character.cdf).stem + "_idle"],
                "note": "Verified actions embedded"}
        with patch("character_assembly.assemble_character",
                   side_effect=assemble), patch(
                    "multi_pak_assets._resolve",
                    return_value=([{"file": "/unused"}], "found")), patch(
                    "character_asset_export.assembled_character_glb",
                    return_value=(rigged_glb(), {"rig_status": "verified"})), patch(
                    "character_animation_export.embed_verified_assembly_animations",
                    side_effect=embed) as embedded:
            output, skipped = build_cdf_folder_glbs(
                Path("."), prefix, index)
        self.assertFalse(skipped)
        self.assertEqual(len(output), 2)
        self.assertEqual(embedded.call_count, 2)
        self.assertEqual(
            _unpack_glb(output["Assemblies/first.glb"][0])[0]["animations"][0]["name"],
            "first_idle")
        self.assertEqual(
            _unpack_glb(output["Assemblies/second.glb"][0])[0]["animations"][0]["name"],
            "second_idle")
        self.assertEqual(
            output["Assemblies/second.glb"][1]["embedded_animation_clips"],
            ("second_idle",))

    def test_converter_failure_preserves_assembly_without_false_success(self):
        prefix = "characters/monsters/example"
        index = {(prefix + "/first.cdf").casefold(): [{"file": "/unused"}]}
        original = rigged_glb()
        with patch("character_assembly.assemble_character",
                   return_value=self.character()), patch(
                    "multi_pak_assets._resolve",
                    return_value=([{"file": "/unused"}], "found")), patch(
                    "character_asset_export.assembled_character_glb",
                    return_value=(original, {"rig_status": "verified"})), patch(
                    "character_animation_export.embed_verified_assembly_animations",
                    side_effect=ValueError("incompatible master skeleton")):
            output, skipped = build_cdf_folder_glbs(
                Path("."), prefix, index)
        self.assertFalse(skipped)
        blob, info = output["Assemblies/first.glb"]
        self.assertEqual(blob, original)
        self.assertEqual(info["embedded_animations"], 0)
        self.assertIn("incompatible master skeleton", info["animation_note"])

    def test_cancellation_propagates_without_partial_assumptions(self):
        prefix = "characters/monsters/example"
        index = {(prefix + "/first.cdf").casefold(): [{"file": "/unused"}]}
        with patch("character_assembly.assemble_character",
                   return_value=self.character()), patch(
                    "multi_pak_assets._resolve",
                    return_value=([{"file": "/unused"}], "found")), patch(
                    "character_asset_export.assembled_character_glb",
                    return_value=(rigged_glb(), {})), patch(
                    "character_animation_export.embed_verified_assembly_animations",
                    side_effect=InterruptedError("cancelled")):
            with self.assertRaises(InterruptedError):
                build_cdf_folder_glbs(Path("."), prefix, index)


if __name__ == "__main__":
    import unittest
    unittest.main()
