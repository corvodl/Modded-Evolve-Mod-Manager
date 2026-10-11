"""Archive dependency families must never include another map/character."""
import unittest
from pak_dependencies import (level_identity, group_identity, group_title,
                              is_level_pak, plan_dependencies, plan_report)

ARCHIVES = [
    'Game/levels/Lava_Rivers/level.pak',
    'Game/levels/Lava_Rivers/terraintexture.pak',
    'Game/levels_lava_rivers.pak',
    'Game/levels/Forest_Cliffs/level.pak',
    'Game/levels/Forest_Cliffs/terraintexture.pak',
    'Game/levels_forest_cliffs.pak',
    'Game/characters_hunters_merc_tech_data.pak',
    'Game/characters_hunters_merc_tech_ts.pak',
    'Game/characters_hunters_merc_sunny_data.pak',
    'Game/characters_hunters_shared_data.pak',
    'Game/characters_hunters_shared_ts.pak',
    'Game/AnimationsCAF.pak',
    'Game/AnimationsConfig.pak',
    'Game/AnimationsDBA.pak',
    'Game/libs.pak',
]

class DependencyGrouping(unittest.TestCase):
    def test_level_family_groups_archive_and_both_map_paks(self):
        for main in ARCHIVES[:3]:
            plan=plan_dependencies(main,ARCHIVES)
            self.assertEqual(len(plan.all_paks),3)
            self.assertEqual({p.lower() for p in plan.all_paks},
                             {p.lower() for p in ARCHIVES[:3]})
            self.assertTrue(is_level_pak(main))
            self.assertIn('Lava Rivers',plan.group)
            self.assertIn('known companion',plan_report(plan).lower())
            self.assertFalse(plan.missing)

    def test_no_cross_level_extraction(self):
        plan=plan_dependencies(ARCHIVES[0],ARCHIVES)
        self.assertFalse(any('forest' in p.lower() for p in plan.all_paks))
        self.assertNotIn('Game/libs.pak',plan.all_paks)

    def test_character_family_includes_shared_and_animation_but_not_others(self):
        plan=plan_dependencies(ARCHIVES[6],ARCHIVES)
        for name in (ARCHIVES[7],ARCHIVES[9],ARCHIVES[10],ARCHIVES[11],
                     ARCHIVES[12],ARCHIVES[13]):
            self.assertIn(name,plan.companions)
        self.assertNotIn(ARCHIVES[8],plan.companions)

    def test_partial_family_explicitly_reports_missing(self):
        partial=[ARCHIVES[0]]
        plan=plan_dependencies(partial[0],partial)
        self.assertEqual(len(plan.all_paks),1)
        self.assertEqual(len(plan.missing),2)
        self.assertIn('terraintexture.pak',plan_report(plan))

    def test_unrelated_pak_unmodified(self):
        plan=plan_dependencies('Game/libs.pak',ARCHIVES)
        self.assertEqual(plan.companions,())
        self.assertFalse(is_level_pak('Game/libs.pak'))
        with self.assertRaises(ValueError):
            plan_dependencies('Game/unverified.pak',ARCHIVES)

if __name__=='__main__':
    unittest.main()
