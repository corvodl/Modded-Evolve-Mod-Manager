"""Synthetic dependency graph tests for historical transfer planning."""
import tempfile
import unittest
from pathlib import Path

from build_transfer_dependencies import discover_dependencies


class TransferDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.material = self.root / "unit.mtl"
        self.material.write_text(
            '<Material><Textures><Texture File="characters/hunters/unit_diff.dds"/></Textures></Material>',
            encoding="utf-8")
        self.texture = self.root / "unit_diff.dds"
        self.texture.write_bytes(b"synthetic-not-real-dds")
        self.path = "characters/hunters/unit.mtl"
        self.diffuse = "characters/hunters/unit_diff.dds"
        self.index = {
            self.path: [{"file": self.material, "relative": self.path, "build_id": "source"}],
            self.diffuse: [{"file": self.texture, "relative": self.diffuse, "build_id": "source"}]}

    def tearDown(self):
        self.temp.cleanup()

    def test_build_local_reference_discovery(self):
        result = discover_dependencies(self.index, [self.path], build_id="source")
        self.assertEqual(result["missing"], ())
        self.assertIn(self.path, result["found"])
        self.assertIn(self.diffuse, result["found"])
        self.assertTrue(any(edge["to"] == self.diffuse for edge in result["edges"]))
        self.assertFalse(result["closure_complete"])  # Binary format unverified.

    def test_foreign_texture_never_used(self):
        self.index[self.diffuse][0]["build_id"] = "other"
        result = discover_dependencies(self.index, [self.path], build_id="source")
        self.assertIn(self.diffuse, result["missing"])
        self.assertNotIn(self.diffuse, result["found"])

    def test_cancellation(self):
        result = discover_dependencies(self.index, [self.path], build_id="source",
                                       cancelled=lambda: True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["found"], ())

    def test_rejects_all_builds_and_forbidden_xml(self):
        with self.assertRaises(ValueError):
            discover_dependencies(self.index, [self.path], build_id="all")
        self.material.write_text("<!DOCTYPE x><Material/>", encoding="utf-8")
        result = discover_dependencies(self.index, [self.path], build_id="source")
        self.assertTrue(any(item["reason"] == "forbidden-xml" for item in result["incomplete"]))


if __name__ == "__main__":
    unittest.main()
