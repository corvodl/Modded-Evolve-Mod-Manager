"""Read-only map identity and multi-PAK selection regressions."""
import unittest

from library_map_actions import library_map_identity, validated_map_selection


def record(ident, name, source):
    return {"id": ident, "name": name, "source": source}


class MapSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = r"C:\Games\ModdedEvolve\EvolveGame\Game"
        self.level = record("level", "level.pak",
                            directory + r"\levels\Tutorial\level.pak")
        self.terrain = record("terrain", "terraintexture.pak",
                              directory + r"\levels\Tutorial\terraintexture.pak")
        self.packed = record("packed", "levels_tutorial.pak",
                             directory + r"\levels_tutorial.pak")

    def test_all_three_archive_variants_identify_tutorial(self):
        for rec in (self.level, self.terrain, self.packed):
            self.assertEqual(library_map_identity(rec), "tutorial")

    def test_mixed_filename_position_still_picks_primary(self):
        primary, level_id = validated_map_selection(
            [self.terrain, self.packed, self.level])
        self.assertEqual(primary["id"], "level")
        self.assertEqual(level_id, "tutorial")
        single, _ = validated_map_selection([self.terrain])
        self.assertEqual(single["id"], "terrain")

    def test_reject_other_maps_and_unverified_sources(self):
        other = record("other", "level.pak",
                       r"C:\Games\ModdedEvolve\EvolveGame\Game\levels\DesertCanyon\level.pak")
        with self.assertRaisesRegex(ValueError, "different maps"):
            validated_map_selection([self.level, other])
        untrusted = record("bad", "level.pak", r"C:\Temp\level.pak")
        self.assertIsNone(library_map_identity(untrusted))
        with self.assertRaisesRegex(ValueError, "not in a recognized"):
            validated_map_selection([self.level, untrusted])
        with self.assertRaises(ValueError):
            validated_map_selection([])
        with self.assertRaises(ValueError):
            validated_map_selection([self.level] * 31)

    def test_source_basename_cannot_claim_other_archive(self):
        renamed = record("fake", "level.pak",
                         r"C:\Games\Evolve\Game\levels\Tutorial\terraintexture.pak")
        self.assertIsNone(library_map_identity(renamed))


if __name__ == "__main__":
    unittest.main()
