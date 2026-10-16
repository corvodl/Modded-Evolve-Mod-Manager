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

    def test_preserves_canonical_complete_body_and_friendly_name(self):
        from character_export_command import export_character_directory
        rig = (b'glTF-rig', {'cdf': 'characters/hunters/merc_emet/full.cdf',
                            'bone_count': 27, 'embedded_animations': 2,
                            'part_count': 4})
        other = (b'glTF-unrigged', {'cdf': '', 'bone_count': 0,
                                  'preferred_full_character': True})
        with (patch('character_export_command.build_cdf_folder_glbs',
                    return_value=({'Assemblies/full.glb': rig,
                                   'Assemblies/cannon.glb': other}, ())),
              patch('character_export_command.export_character_asset_folder',
                    return_value={'folder': str(self.destination / 'Emet')}) as export):
            report = export_character_directory(
                self.base, self.destination, index={})
        self.assertEqual(Path(report['folder']).name, 'Emet')
        self.assertIs(export.call_args.kwargs['full_model'], other)
        self.assertEqual(export.call_args.kwargs['output_name'], 'Emet')
        self.assertEqual(len(export.call_args.kwargs['assemblies']), 1)
        self.assertIn('Assemblies/full.glb',
                      export.call_args.kwargs['assemblies'])
        self.assertNotIn('Assemblies/cannon.glb',
                         export.call_args.kwargs['assemblies'])

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
