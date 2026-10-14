"""Synthetic beta.45 Legacy material regression: NoDraw skeleton ≠ body.

No game assets are committed. Tests model exact virtual ownership and cooked
DDS priority even when historical .tif references or TIFF records exist.
"""
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from material_preview import load_preview_materials


PREFIX = 'objects/characters/monsters/parasitehost/'
OTHER = 'objects/characters/monsters/unrelated/'


def legacy_mtl():
    hidden = ''.join(
        '<Material Name="Skeleton%d" Shader="NoDraw"><Textures/></Material>' % i
        for i in range(10))
    return ('<Material><SubMaterials>' + hidden +
            '<Material Name="Host_Body" Shader="Illum" Diffuse="1,1,1">'
            '<Textures><Texture Map="Diffuse" File="' + PREFIX +
            'parasitehost.tif"/></Textures></Material>'
            '<Material Name="Host_Saliva" Shader="Illum" Opacity="0.11">'
            '<Textures><Texture Map="Diffuse" '
            'File="materials/universal/default/128grey.tif"/></Textures></Material>'
            '</SubMaterials></Material>')


class LegacyMaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = {}
        self.add(PREFIX + 'ParasiteHost3.mtl',
                 legacy_mtl().encode('utf-8'))

    def add(self, virtual, data):
        file = self.root / ('asset-' + str(len(self.index)))
        file.write_bytes(data)
        self.index[virtual.casefold()] = [{
            'file': file, 'relative': virtual, 'workspace': None,
            'archive': 'Synthetic source PAK'}]
        return file

    def dds(self, rgb):
        file = self.root / 'generated.dds'
        Image.new('RGB', (8, 8), rgb).save(file, format='DDS')
        result = file.read_bytes()
        file.unlink()
        return result

    def load(self):
        return load_preview_materials(
            self.root, PREFIX + 'ParasiteHost3.chr', index=self.index)

    def test_cooked_dds_wins_over_tif_and_maps_visible_slot_10(self):
        self.add(PREFIX + 'parasitehost.tif', b'TIFF source is not a DDS')
        self.add(PREFIX + 'parasitehost.dds', self.dds((144, 70, 20)))
        result = self.load()
        self.assertEqual(result.names[10], 'Host_Body')
        self.assertEqual(len(result.hidden), 12)
        self.assertTrue(all(result.hidden[:10]))
        self.assertFalse(result.hidden[10])
        self.assertIsNotNone(result.textures[10])
        self.assertEqual(result.textures[10].getpixel((0, 0)), (144, 70, 20))
        self.assertTrue(result.diffuse_sources[10][0].endswith('parasitehost.dds'))
        self.assertIsNone(result.textures[0])

    def test_complete_streaming_dds0_is_preferred(self):
        self.add(PREFIX + 'parasitehost.dds', self.dds((10, 30, 50)))
        self.add(PREFIX + 'parasitehost.dds.0', self.dds((220, 150, 75)))
        mats = self.load()
        self.assertEqual(mats.textures[10].getpixel((0, 0)), (220, 150, 75))
        self.assertTrue(mats.diffuse_sources[10][0].endswith('.dds.0'))

    def test_missing_visible_cooked_texture_is_reported_despite_nodraw(self):
        self.add(PREFIX + 'parasitehost.tif', b'legacy TIFF input')
        with self.assertRaisesRegex(ValueError, 'No visible diffuse DDS'):
            self.load()

    def test_unique_wrong_character_basename_is_not_borrowed(self):
        self.add(OTHER + 'parasitehost.dds', self.dds((255, 0, 0)))
        with self.assertRaisesRegex(ValueError, 'No visible diffuse DDS'):
            self.load()


if __name__ == '__main__':
    unittest.main()
