"""Asset Library read-only preview/export regression tests (synthetic data only)."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from asset_library_preview import (
    archive_folder, archive_entries, read_asset, export_file,
    export_archive_layout, texture_image, export_png, library_model_index,
)
from test_editor_tabs_png import fixture_dds


class LibraryPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'AssetLibrary'
        self.identifier = 'a' * 20
        self.archive = self.root / self.identifier
        (self.archive / 'files').mkdir(parents=True)
        self.data = {
            'characters/hunters/merc_maggie/maggie_3p.mtl':
                b'<Material><SubMaterials><Material Name="body"/></SubMaterials></Material>',
            'characters/hunters/merc_maggie/textures/body_diff.dds':
                fixture_dds('DXT5', (16, 16), 1),
            'animations/config/character.xml': b'<Params><AnimationList/></Params>',
        }
        for name, contents in self.data.items():
            target = self.archive / 'files' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        self.manifest = self.archive / 'asset-library.json'
        self.manifest.write_text(json.dumps({
            'version': 1, 'name': 'characters_hunters_merc_maggie_data.pak',
            'entries': [{'path': name, 'bytes': len(data)}
                        for name, data in self.data.items()],
        }))

    def test_all_listed_assets_keep_virtual_paths(self):
        records = archive_entries(self.identifier, self.root)
        self.assertEqual(set(records), set(self.data))
        self.assertEqual(read_asset(
            self.identifier, 'animations/config/character.xml', root=self.root),
            self.data['animations/config/character.xml'])

    def test_copy_native_file_without_editing_original(self):
        original = 'characters/hunters/merc_maggie/maggie_3p.mtl'
        target = self.base / 'material.mtl'
        export_file(self.identifier, original, target, root=self.root)
        self.assertEqual(target.read_bytes(), self.data[original])
        self.assertEqual((self.archive / 'files' / original).read_bytes(),
                         self.data[original])
        with self.assertRaisesRegex(ValueError, 'outside'):
            export_file(self.identifier, original,
                        self.archive / 'files' / 'copied.mtl', root=self.root)

    def test_complete_export_preserves_virtual_directory_layout(self):
        folder = self.base / 'exports'
        folder.mkdir()
        result, count = export_archive_layout(
            self.identifier, folder, root=self.root)
        self.assertEqual(count, len(self.data))
        for virtual, contents in self.data.items():
            self.assertEqual((result / virtual).read_bytes(), contents)
        with self.assertRaises(FileExistsError):
            export_archive_layout(self.identifier, folder, root=self.root)

    def test_valid_dds_exports_png_as_decoded_image(self):
        reference = 'characters/hunters/merc_maggie/textures/body_diff.dds'
        image = texture_image(self.identifier, reference, root=self.root)
        self.assertEqual(image.size, (16, 16))
        output = self.base / 'body.png'
        export_png(self.identifier, reference, output, root=self.root)
        from PIL import Image
        with Image.open(output) as parsed:
            self.assertEqual(parsed.size, (16, 16))

    def test_invalid_archive_id_and_traversal_not_accepted(self):
        with self.assertRaises(ValueError):
            archive_folder('../evil', self.root)
        with self.assertRaises(ValueError):
            read_asset(self.identifier, '../etc/passwd', root=self.root)
        with self.assertRaises(FileNotFoundError):
            read_asset(self.identifier, 'not_an_entry.dds', root=self.root)

    def test_damaged_files_are_not_previewable_or_exportable(self):
        virtual = 'animations/config/character.xml'
        (self.archive / 'files' / virtual).unlink()
        self.assertNotIn(virtual, archive_entries(self.identifier, self.root))
        with self.assertRaises(FileNotFoundError):
            read_asset(self.identifier, virtual, root=self.root)

    def test_model_index_uses_selected_imported_archive(self):
        with patch('asset_library_preview.extracted_index', return_value={}):
            index = library_model_index(self.identifier, self.root)
        self.assertIn('characters/hunters/merc_maggie/maggie_3p.mtl', index)
        self.assertTrue(index['characters/hunters/merc_maggie/maggie_3p.mtl'][0]
                        ['file'].is_file())


if __name__ == '__main__':
    unittest.main()
