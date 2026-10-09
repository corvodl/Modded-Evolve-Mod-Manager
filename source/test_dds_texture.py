"""DDS preview, export and safe atomic replacement tests with real DDS fixtures."""
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from dds_texture import (parse_dds, preview_dds, export_png, validate_replacement,
                         replace_dds, is_split_dds, is_dds)


def dds(color=(10, 20, 30, 255), size=(8, 8)):
    data = BytesIO()
    Image.new('RGBA', size, color).save(data, format='DDS')
    return data.getvalue()


class DDSTextureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ws = self.base / 'project'
        self.file = self.ws / 'files' / 'textures' / 'skin.dds'
        self.file.parent.mkdir(parents=True)
        self.original = dds()
        self.file.write_bytes(self.original)

    def test_parse_preview_png(self):
        info = parse_dds(self.original)
        self.assertEqual((info.width, info.height, info.mipmaps), (8, 8, 1))
        self.assertEqual(preview_dds(self.original).getpixel((0, 0)), (10, 20, 30, 255))
        dest = self.base / 'export.png'
        export_png(self.original, dest)
        with Image.open(dest) as image:
            self.assertEqual(image.size, (8, 8))
            self.assertEqual(image.getpixel((0, 0)), (10, 20, 30, 255))

    def test_import_same_layout_and_backup(self):
        change = dds((30, 40, 50, 255))
        import_file = self.base / 'replacement.dds'
        import_file.write_bytes(change)
        out = replace_dds(self.ws, 'textures/skin.dds', import_file, hashlib.sha256(self.original).hexdigest())
        self.assertEqual(out, change)
        self.assertEqual(self.file.read_bytes(), change)
        backups = list((self.ws / 'EditorBackups').rglob('skin.dds'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertEqual(validate_replacement(self.original, change).mipmaps, 1)

    def test_reject_different_dimensions_and_format(self):
        with self.assertRaisesRegex(ValueError, 'width differs'):
            validate_replacement(self.original, dds(size=(16, 8)))
        modified = bytearray(self.original)
        modified[88] = 24
        with self.assertRaisesRegex(ValueError, 'pixel_signature differs'):
            validate_replacement(self.original, bytes(modified))

    def test_reject_truncated_and_invalid_dds(self):
        for bad in (b'DDS ', b'junk', self.original[:128]):
            with self.assertRaises(ValueError): parse_dds(bad)
        with self.assertRaises(ValueError): validate_replacement(self.original, b'not dds')

    def test_import_is_atomic_when_invalid_or_stale(self):
        invalid = self.base / 'bad.dds'
        invalid.write_bytes(dds(size=(16, 8)))
        with self.assertRaises(ValueError): replace_dds(self.ws, 'textures/skin.dds', invalid, hashlib.sha256(self.original).hexdigest())
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertFalse((self.ws / 'EditorBackups').exists())
        invalid.write_bytes(dds((1, 2, 3, 255)))
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            replace_dds(self.ws, 'textures/skin.dds', invalid, 'wrong hash')
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_split_streaming_parts_not_supported(self):
        for name in ('rock_spec.dds.0', 'rock_ddn.dds.5', 'ROCK.DDS.10'):
            self.assertTrue(is_split_dds(name))
            self.assertFalse(is_dds(name))
        self.assertTrue(is_dds('rock.dds'))
        split = self.ws / 'files' / 'textures' / 'skin.dds.0'
        split.write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, 'Only whole'):
            replace_dds(self.ws, 'textures/skin.dds.0', self.base / 'test.dds', '')

    def test_replacement_keeps_full_mipmap_payload(self):
        original = bytearray(self.original)
        replacement = bytearray(self.original)
        replacement[148:164] = bytes([64]) * 16
        validate_replacement(bytes(original), bytes(replacement))
        self.assertEqual(len(original), len(replacement))


if __name__ == '__main__': unittest.main()
