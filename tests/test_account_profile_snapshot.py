"""Profile snapshot isolation and validation tests with synthetic fixtures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from account_profile_snapshot import (
    build_copy, classification, compare_snapshots, export_new_copy,
    load_snapshot, read_catalog
)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.fixture = {
            "Major": 1, "Ownables": ["Abe", "AbeDefaultSkin"],
            "XPProgression": [{"Category": "Abe", "XP": 120, "Level": 2}],
            "UnknownPayload": {"keep": [5, 6, 7]},
        }
        self.path = self.dir / "snapshot.json"
        self.path.write_text(json.dumps(self.fixture), encoding="utf-8")

    def test_load_and_classification(self):
        actual = load_snapshot(self.path)
        self.assertEqual(actual["XPProgression"][0]["Level"], 2)
        self.assertEqual(classification("AbeDefaultSkin", {"AbeDefaultSkin"}, ()), "Skin / outfit")

    def test_copy_is_independent_preserves_unknown(self):
        candidate = build_copy(self.fixture, ["Abe", "NewSkin"], [("Abe", 40, 1200)])
        self.assertEqual(candidate["UnknownPayload"], self.fixture["UnknownPayload"])
        self.assertEqual(self.fixture["XPProgression"][0]["Level"], 2)
        output = self.dir / "offline-copy.json"
        export_new_copy(candidate, output)
        self.assertEqual(load_snapshot(output)["XPProgression"][0]["Level"], 40)
        with self.assertRaises(ValueError):
            export_new_copy(candidate, output)
        self.assertEqual(load_snapshot(self.path), self.fixture)

    def test_reject_encoded_profile(self):
        binary = self.dir / "encoded.json"
        binary.write_bytes(b"TRSEVLVPC\x00\xff\xee")
        with self.assertRaisesRegex(ValueError, "encoded"):
            load_snapshot(binary)

    def test_comparison(self):
        newer = build_copy(self.fixture, ["Abe", "NewSkin"], [("Abe", 3, 190)])
        diff = compare_snapshots(self.fixture, newer)
        self.assertEqual(diff["added"], ("NewSkin",))
        self.assertEqual(diff["removed"], ("AbeDefaultSkin",))
        self.assertEqual(diff["progression"][0][0], "Abe")

    def test_catalog_safety(self):
        catalog = self.dir / "catalog.txt"
        catalog.write_text("Perk1\nPerk2\nPerk1\n", encoding="utf-8")
        self.assertEqual(read_catalog(catalog), ("Perk1", "Perk2"))


if __name__ == "__main__":
    unittest.main()
