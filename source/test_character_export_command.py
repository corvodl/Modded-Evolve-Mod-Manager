"""Regression tests for the shared character folder export entry point."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class CharacterExportCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'workspace'
        self.base = self.workspace / 'files' / 'characters' / 'hunters' / 'merc_emet'
        (self.base / 'equipment').mkdir(parents=True)
        (self.workspace / '.evolve-pak-workspace.json').write_text(
            json.dumps({'version': 1, 'entries': []}), encoding='utf-8')
        self.destination = self.root / 'output'
        self.destination.mkdir()

    def test_resolves_nested_equipment_folder_to_character(self):
        from character_export_command import character_export_source
        workspace, virtual = character_export_source(self.base / 'equipment')
        self.assertEqual(workspace, self.workspace)
        self.assertEqual(virtual, 'characters/hunters/merc_emet')

    def test_emet_root_glb_uses_body_and_cannon_composite(self):
        from character_export_command import export_character_directory
        base = 'characters/hunters/merc_emet/'
        complete = (b'glTF-body-cannon', {
            'cdf': base + 'emet_3p.cdf',
            'bone_count': 27, 'part_count': 3, 'embedded_animations': 2,
            'parts': ('body', 'normal', 'remotecannon'),
            'rendered_parts': (
                base + 'emet_3p.skinm',
                base + 'emet_3p_face_normal.skinm',
                base + 'emetremotecannon_3p.skinm'),
            'geometry_verification': {
                'status': 'verified_body_and_remote_cannon',
                'part_drawn_triangles': (2, 1, 1)}})
        # Even an explicitly preferred, rigged cannon-only export cannot
        # override a verified CDF that actually contains the character body.
        cannon = (b'glTF-arm-only', {
            'cdf': '', 'bone_count': 50, 'part_count': 1,
            'preferred_full_character': True,
            'original_3p_model': base + 'emetremotecannon_3p.skinm',
            'rendered_parts': (base + 'emetremotecannon_3p.skinm',)})
        with (patch('character_export_command.build_cdf_folder_glbs',
                    return_value=({'Assemblies/emet_3p.glb': complete,
                                   'Assemblies/cannon.glb': cannon}, ())),
              patch('character_export_command.export_character_asset_folder',
                    return_value={'folder': str(self.destination / 'Emet')}) as export):
            result = export_character_directory(
                self.base, self.destination, index={},
                selected_cdf=base + 'emetremotecannon_3p.cdf')
        self.assertEqual(Path(result['folder']).name, 'Emet')
        self.assertIs(export.call_args.kwargs['full_model'], complete)
        self.assertEqual(export.call_args.kwargs['output_name'], 'Emet')
        self.assertIn('Assemblies/cannon.glb',
                      export.call_args.kwargs['assemblies'])
        self.assertNotIn('Assemblies/emet_3p.glb',
                         export.call_args.kwargs['assemblies'])

    def test_emet_cannon_only_never_exports_character_glb(self):
        from character_export_command import export_character_directory
        arm = (b'glTF-arm-only', {
            'cdf': '', 'part_count': 1, 'bone_count': 50,
            'preferred_full_character': True,
            'original_3p_model': 'characters/hunters/merc_emet/emetremotecannon_3p.skinm',
            'rendered_parts': ('characters/hunters/merc_emet/emetremotecannon_3p.skinm',)})
        with patch('character_export_command.build_cdf_folder_glbs',
                   return_value=({'Assemblies/cannon.glb': arm}, ())):
            with self.assertRaisesRegex(ValueError, 'both emet_3p.skinm body'):
                export_character_directory(self.base, self.destination, index={})
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_emet_other_character_cdf_cannot_be_primary(self):
        from character_export_command import export_character_directory
        base = 'characters/hunters/merc_emet/'
        deployable = (b'glTF-deployable', {
            'cdf': base + 'emethealingtotemworld_3p.cdf',
            'part_count': 4, 'bone_count': 32,
            'rendered_parts': (base + 'emethealingtotemworld_3p.skinm',)})
        with patch('character_export_command.build_cdf_folder_glbs',
                   return_value=({'Assemblies/deployable.glb': deployable}, ())):
            with self.assertRaisesRegex(ValueError, 'both emet_3p.skinm body'):
                export_character_directory(self.base, self.destination, index={})
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_sunny_full_character_prefers_third_person_over_larger_fp(self):
        from character_export_command import _preferred_full_assembly
        first_person = (b'glTF-fp', {
            'cdf': 'characters/hunters/merc_sunny/sunny_fp.cdf',
            'skeleton': 'characters/hunters/merc_sunny/sunny_fp.chr',
            'part_count': 9, 'bone_count': 80,
            'embedded_animations': 20})
        third_person = (b'glTF-3p', {
            'cdf': 'characters/hunters/merc_sunny/sunny_3p.cdf',
            'skeleton': 'characters/hunters/merc_sunny/sunny_3p.chr',
            'part_count': 3, 'bone_count': 32})
        result = _preferred_full_assembly({
            'Assemblies/sunny_fp.glb': first_person,
            'Assemblies/sunny_3p.glb': third_person,
        }, selected_cdf=first_person[1]['cdf'])
        self.assertIs(result, third_person)

    def test_explicit_sunny_3p_beats_untagged_multi_part_variant(self):
        from character_export_command import _preferred_full_assembly
        neutral = (b'glTF-neutral', {
            'cdf': 'characters/hunters/merc_sunny/sunny_equipment.cdf',
            'bone_count': 64, 'part_count': 6})
        third_person = (b'glTF-3p', {
            'cdf': 'characters/hunters/merc_sunny/sunny_3p.cdf',
            'part_count': 1, 'bone_count': 28})
        self.assertIs(_preferred_full_assembly({
            'Assemblies/neutral.glb': neutral,
            'Assemblies/sunny_3p.glb': third_person,
        }), third_person)

    def test_first_person_only_never_becomes_main_character(self):
        from character_export_command import _preferred_full_assembly
        fp = (b'glTF', {'cdf': 'characters/hunters/merc_sunny/sunny_1p.cdf',
                         'bone_count': 40, 'part_count': 5,
                         'preferred_full_character': True})
        self.assertIsNone(_preferred_full_assembly({'Assemblies/fp.glb': fp}))

    def test_fp_skeleton_does_not_hide_behind_untagged_cdf(self):
        from character_export_command import _preferred_full_assembly
        fp = (b'glTF-fp', {
            'cdf': 'characters/hunters/merc_sunny/sunny.cdf',
            'skeleton': 'characters/hunters/merc_sunny/sunny_first_person.chr',
            'part_count': 6, 'bone_count': 60})
        body = (b'glTF-body', {
            'cdf': 'characters/hunters/merc_sunny/sunny_body.cdf',
            'skeleton': 'characters/hunters/merc_sunny/sunny_3p.chr',
            'part_count': 2, 'bone_count': 28})
        self.assertIs(_preferred_full_assembly({
            'Assemblies/sunny.glb': fp, 'Assemblies/sunny_body.glb': body
        }), body)

    def test_only_fp_assemblies_raise_instead_of_creating_wrong_root(self):
        from character_export_command import export_character_directory
        first_person = (b'glTF-fp', {
            'cdf': 'characters/hunters/merc_emet/emet_fp.cdf',
            'skeleton': 'characters/hunters/merc_emet/emet_fp.chr',
            'part_count': 7, 'bone_count': 48})
        with patch('character_export_command.build_cdf_folder_glbs',
                   return_value=({'Assemblies/fp.glb': first_person}, ())):
            with self.assertRaisesRegex(ValueError, 'Only first-person'):
                export_character_directory(self.base, self.destination, index={})
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_legacy_merc_emet_export_requires_parent_and_preserves_files(self):
        from character_export_command import validate_character_export_destination
        old = self.destination / 'merc_emet'
        old.mkdir()
        (old / 'Assemblies').mkdir()
        (old / 'emet_3p.glb').write_bytes(b'unchanged')
        with self.assertRaisesRegex(ValueError, 'Select its PARENT'):
            validate_character_export_destination(self.base, old)
        self.assertEqual(validate_character_export_destination(
            self.base, self.destination), 'Emet')
        self.assertEqual((old / 'emet_3p.glb').read_bytes(), b'unchanged')
        friendly = self.destination / 'Emet'
        friendly.mkdir()
        with self.assertRaisesRegex(ValueError, 'Select its PARENT'):
            validate_character_export_destination(self.base, friendly)
        self.assertEqual((old / 'emet_3p.glb').read_bytes(), b'unchanged')

    def test_unrelated_loose_folder_never_silently_uses_legacy_export(self):
        from character_export_command import character_export_source
        with self.assertRaisesRegex(ValueError, 'extracted PAK'):
            character_export_source(self.root)

    def test_unverified_character_reports_instead_of_emitting_rootless_folder(self):
        from character_export_command import export_character_directory
        with patch('character_export_command.build_cdf_folder_glbs',
                   return_value=({}, (('missing.cdf', 'no skeleton'),))):
            with self.assertRaisesRegex(ValueError, 'No verified character'):
                export_character_directory(self.base, self.destination, index={})
        self.assertEqual(list(self.destination.iterdir()), [])


class ArchivePlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'files' / 'characters').mkdir(parents=True)

    def _write(self, entries):
        (self.root / '.evolve-pak-workspace.json').write_text(
            json.dumps({'version': 1, 'entries': entries}), encoding='utf-8')

    def test_zero_byte_empty_placeholder_ignored_only_for_read_only_index(self):
        from evolve_pak_workspace import safe_index_entries, load_workspace
        entries = [{'path': '', 'bytes': 0},
                   {'path': 'characters/emet.cdf', 'bytes': 8}]
        self._write(entries)
        with self.assertRaisesRegex(ValueError, 'Unsafe archive entry name'):
            load_workspace(self.root)
        self.assertEqual(safe_index_entries(self.root), [entries[1]])

    def test_empty_entry_with_payload_is_rejected(self):
        from evolve_pak_workspace import safe_index_entries
        self._write([{'path': '', 'bytes': 42}])
        with self.assertRaisesRegex(ValueError, 'contains data'):
            safe_index_entries(self.root)

    def test_path_traversal_remains_rejected(self):
        from evolve_pak_workspace import safe_index_entries
        self._write([{'path': '', 'bytes': 0},
                     {'path': '../outside.txt', 'bytes': 1}])
        with self.assertRaises(ValueError):
            safe_index_entries(self.root)


if __name__ == '__main__':
    unittest.main()
