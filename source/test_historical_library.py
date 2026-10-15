import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from historical_zip import HistoricalZipFile
from legacy_pak_workspace import inspect_archive
import asset_library as lib


class HistoricalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pak = self.root / 'level.pak'

    def make(self, method=zipfile.ZIP_DEFLATED):
        with zipfile.ZipFile(self.pak, 'w') as z:
            z.writestr('brush/0.cgf', b'asset payload', compress_type=method)
        data = bytearray(self.pak.read_bytes())
        data[30:41] = b'brush\\0.cgf'
        self.pak.write_bytes(data)

    def test_slash_variant_crc_and_default_library(self):
        for method in (0, 8):
            self.make(method)
            original = self.pak.read_bytes()
            with zipfile.ZipFile(self.pak) as z:
                with self.assertRaises(zipfile.BadZipFile):
                    z.read('brush/0.cgf')
            self.assertEqual(inspect_archive(self.pak, verify_crc=True)['count'], 1)
            root = self.root / ('library' + str(method))
            result = lib.import_pak(self.pak, root=root)
            folder = lib._folder_for_identifier(result['id'], root)
            manifest = json.loads((folder / lib.MANIFEST).read_text())
            self.assertEqual(manifest['archive_format'], 'ordinary-zip')
            self.assertIsNone(manifest['public_key_sha256'])
            self.assertEqual(lib.extracted_index(root)['brush/0.cgf'][0]['file'].read_bytes(), b'asset payload')
            self.assertEqual(lib.import_pak(self.pak, root=root)['status'], 'already-imported')
            self.assertEqual(original, self.pak.read_bytes())

    def test_actual_corruption_still_rejected_atomically(self):
        self.make(0)
        data = bytearray(self.pak.read_bytes()); data[41] ^= 1
        self.pak.write_bytes(data)
        with self.assertRaises(ValueError):
            inspect_archive(self.pak, verify_crc=True)
        root = self.root / 'library'
        with self.assertRaises(zipfile.BadZipFile):
            lib.import_pak(self.pak, root=root)
        self.assertEqual(lib.imported_archives(root), ())
        self.assertFalse(list(root.glob('*.importing')))

    def test_non_separator_mismatch_rejected(self):
        self.make()
        data = bytearray(self.pak.read_bytes());data[36] = ord('1')
        self.pak.write_bytes(data)
        with HistoricalZipFile(self.pak) as z:
            with self.assertRaises(zipfile.BadZipFile):z.read('brush/0.cgf')

    def test_unsafe_paths_rejected(self):
        with zipfile.ZipFile(self.pak, 'w') as z:z.writestr('../evil', b'bad')
        with self.assertRaises(ValueError):lib.import_pak(self.pak, root=self.root/'lib')

    def test_empty_archive_is_browsable_but_not_an_editing_project(self):
        with zipfile.ZipFile(self.pak, 'w'):
            pass
        with self.assertRaises(ValueError):
            inspect_archive(self.pak)
        root = self.root / 'library'
        result = lib.import_pak(self.pak, root=root)
        self.assertEqual(result['entries'], 0)
        self.assertEqual(len(lib.imported_archives(root)), 1)

    def test_historical_import_cannot_target_stage2(self):
        from asset_library_bulk import stage_source_for_import
        self.make()
        root = self.root / 'library'
        result = lib.import_pak(self.pak, root=root)
        with self.assertRaisesRegex(ValueError, 'Historical ZIP'):
            stage_source_for_import(result['id'], self.root, library_root=root)

    def test_identical_level_archives_keep_both_build_locations(self):
        from build_collections import register_build, catalog
        self.make()
        root = self.root / 'library'
        build = register_build('2012', 'early', root=root)
        other = self.root / 'other' / 'level.pak'
        other.parent.mkdir()
        other.write_bytes(self.pak.read_bytes())
        first = lib.import_pak(self.pak, root=root, build_id=build,
                               relative_archive_path='one/level.pak')
        second = lib.import_pak(other, root=root, build_id=build,
                                relative_archive_path='two/level.pak')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(catalog(root)['builds'][build]['members']), 2)
        again = lib.import_pak(other, root=root, build_id=build,
                               relative_archive_path='two/level.pak')
        self.assertEqual(again['id'], second['id'])
        self.assertEqual(again['status'], 'already-imported')


if __name__ == '__main__':unittest.main()

