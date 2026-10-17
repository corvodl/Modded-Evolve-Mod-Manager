"""Data-driven, display-only classification of CryEngine CDF attachments."""
import unittest

from character_equipment_menu import equipment_menu_groups


class CharacterEquipmentMenuTests(unittest.TestCase):
    def test_kala_weapons_tools_and_attached_arm_are_not_duplicates(self):
        root = 'characters/hunters/merc_tech/'
        options = (
            ('ArmorReducer', root + 'techarmorreducer_3p.skin'),
            ('BansheeGun', root + 'techbansheegun_3p.skin'),
            ('RiftDevice', root + 'techriftdevice_3p.skin'),
            ('RiftDeviceArm', root + 'techriftdevice_3p_arm.skin'),
        )
        groups = equipment_menu_groups(options)
        self.assertEqual(tuple(name for name, _ in groups),
                         ('Weapons', 'Equipment / Abilities'))
        self.assertEqual(
            [label for _, entries in groups for _, label, _ in entries],
            ['ArmorReducer', 'BansheeGun', 'RiftDevice'])
        self.assertEqual(groups[0][1][0][0], 'equipment')
        self.assertEqual(groups[1][1][0][0], 'equipment')

    def test_emet_face_variants_and_cannon_have_distinct_selections(self):
        root = 'characters/hunters/merc_emet/'
        faces = tuple((name, root + 'emet_3p_face_' + name + '.skin')
                      for name in ('normal', 'oh', 'evil', 'healing', 'broken'))
        groups = equipment_menu_groups(
            (('remotecannon', root + 'emetremotecannon_3p.skin'),), faces)
        self.assertEqual([title for title, _ in groups],
                         ['Weapons', 'Face / Head Variants'])
        self.assertEqual(len(groups[1][1]), 5)
        self.assertTrue(all(kind == 'faceplate'
                            for kind, _, _ in groups[1][1]))
        self.assertEqual(groups[1][1][0][1], 'normal')

    def test_empty_categories_are_not_rendered(self):
        self.assertEqual(equipment_menu_groups((), ()), ())
        self.assertEqual([group for group, _ in equipment_menu_groups(
            (('mystery', 'characters/hunters/merc_abe/part.skin'),))],
            ['Other Attachments'])

    def test_accessories_not_misclassified_as_weapons(self):
        groups = equipment_menu_groups((
            ('flashlight', 'characters/hunters/maggie/flashlight.skin'),
            ('helmet', 'characters/hunters/maggie/helmet.skin'),
        ))
        self.assertEqual([name for name, _ in groups],
                         ['Face / Head Variants', 'Accessories'])

    def test_menu_grouping_does_not_modify_original_paths(self):
        original = ('BansheeGun', 'characters/hunters/merc_tech/TECH_3P_BANSHEEGUN.skin')
        grouped = equipment_menu_groups((original,))
        self.assertEqual(grouped[0][1][0][1:], original)


if __name__ == '__main__':
    unittest.main()
