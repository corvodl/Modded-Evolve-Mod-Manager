"""Regression tests for read-only model families and LOD grouping."""
import unittest
from model_families import group_model_families


class ModelFamilyTests(unittest.TestCase):
    def test_scrap_skin_lods_and_skeleton_grouped(self):
        prefix = 'characters/monsters/scrap/'
        members = [prefix + p for p in (
            'scrap_3p.skinm', 'scrap_3p.skin', 'scrap_3p_lod1.skinm',
            'scrap_3p_lod2.skinm', 'scrap_3p_lod3.skinm',
            'scrap_3p_skeleton.chr', 'scrap_3p_skeleton.chrparams',
            'scrap_3p_mask.skinm', 'scrap_3p_mask_lod1.skinm',
            'scrap_3p.mtl', 'scrap_3p.cdf')]
        groups = group_model_families(members)
        self.assertEqual(len(groups), 1)
        group = next(iter(groups.values()))
        self.assertEqual(group.primary, prefix + 'scrap_3p.skinm')
        self.assertEqual(len(group.members), len(members))
        self.assertEqual(group.lod_count, 4)
        self.assertTrue(group.has_definition)

    def test_fp_separate_and_no_merging_other_character(self):
        models = ['char/scrap_3p.skinm','char/scrap_3p_lod1.skinm',
                  'char/scrap_fp.skinm','char/scrap_fp_lod1.skinm',
                  'char/other_3p.skinm','char/other_3p_lod1.skinm']
        grouped = group_model_families(models)
        self.assertEqual(len(grouped), 3)
        self.assertEqual(sum(len(group.members) for group in grouped.values()), 6)

    def test_no_eligible_family_for_loose_material(self):
        self.assertEqual(group_model_families(['props/stone.mtl']), {})
        self.assertEqual(group_model_families(['props/stone.skinm']), {})
        self.assertEqual(group_model_families(['props/stone.caf']), {})


if __name__ == '__main__':
    unittest.main()
