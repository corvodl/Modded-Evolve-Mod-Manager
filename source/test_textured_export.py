"""Read-only textured Blender GLB export / skin mapping verification."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import struct
import unittest

from PIL import Image
from model_preview import read_preview_mesh
from test_model_preview import test_mesh
from material_preview import PreviewMaterials
from model_blender_export import glb_bytes
from model_textured_export import (
    embed_diffuse_textures, weight_stream_status,
    textured_model_from_workspace, export_extracted_folder, _unpack_glb)


class TexturedModelExportTests(unittest.TestCase):
    def test_embeddings_are_valid_gltf_binary_and_material_slots(self):
        mesh = replace(read_preview_mesh(test_mesh(), 'test.skinm'),
                       uvs=((0., 0.), (1., 0.), (0., 1.), (1., 1.)),
                       subsets=((0, 6, 3), (6, 6, 4)))
        initial = glb_bytes(mesh)
        images = [None] * 5
        images[3] = Image.new('RGB', (4, 4), (230, 10, 20))
        images[4] = Image.new('RGB', (4, 4), (10, 20, 230))
        material = PreviewMaterials(tuple(images), tuple(str(i) for i in range(5)), 'test.mtl')
        result, number = embed_diffuse_textures(initial, material)
        self.assertEqual(number, 2)
        doc, binary = _unpack_glb(result)
        self.assertEqual(doc['asset']['version'], '2.0')
        self.assertEqual(len(doc['images']), 2)
        self.assertEqual(len(doc['textures']), 2)
        self.assertTrue(all(item['mimeType'] == 'image/png' for item in doc['images']))
        self.assertEqual(doc['buffers'][0]['byteLength'], len(binary))
        self.assertEqual(
            len([x for x in doc['materials'] if
                 'baseColorTexture' in x['pbrMetallicRoughness']]), 2)
        self.assertEqual(doc['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0'], 1)

    def test_unavailable_material_does_not_claim_textures(self):
        base = glb_bytes(read_preview_mesh(test_mesh(), 'test.skinm'))
        res, count = embed_diffuse_textures(base, PreviewMaterials(
            (None,), ('none',), 'missing.mtl'))
        self.assertEqual(count, 0)
        doc, unused = _unpack_glb(res)
        self.assertNotIn('images', doc)

    def test_manager_material_resolution_and_missing_texture_status(self):
        mesh = read_preview_mesh(test_mesh(), 'test.skinm')
        with patch('material_preview.load_preview_materials',
                   return_value=PreviewMaterials(
                        (Image.new('RGB', (2, 2), (120, 130, 140)),),
                        ('mat0',), 'test.mtl')):
            glb, report = textured_model_from_workspace(
                mesh, '/workspace', 'test.skinm')
            self.assertEqual(report['texture_count'], 1)
            self.assertIn('textures embedded', report['texture_status'])
            self.assertEqual(len(_unpack_glb(glb)[0]['images']), 1)
        with patch('material_preview.load_preview_materials',
                   side_effect=ValueError('texture archive missing')):
            glb, report = textured_model_from_workspace(
                mesh, '/workspace', 'test.skinm')
            self.assertEqual(report['texture_count'], 0)
            self.assertIn('missing', report['texture_status'])


    def test_lennox_blender_export_bakes_separate_uv_alpha_glow(self):
        """An emissive tile repeated twice cannot ignore diffuse armor alpha."""
        from io import BytesIO
        from texture_transform import IDENTITY
        from model_textured_export import _unpack_glb
        diffuse = Image.new('RGBA', (4, 4), (40, 40, 40, 0))
        for y in range(4):
            for x in (0, 1):
                diffuse.putpixel((x, y), (40, 40, 40, 255))
        emission = Image.new('RGB', (4, 4), (255, 50, 10))
        mesh = replace(read_preview_mesh(test_mesh(), 'test.skinm'),
                       uvs=((0., 0.), (1., 0.), (0., 1.), (1., 1.)),
                       subsets=((0, 6, 3), (6, 6, 4)))
        mats = PreviewMaterials(
            (None, None, None, diffuse, None),
            ('no glow', 'no glow', 'no glow', 'Lennox armor', 'no glow'),
            'lennox.mtl',
            emissive_maps=(None, None, None, emission, None),
            emissive_strengths=(0., 0., 0., 4., 0.),
            emissive_masks=('none', 'none', 'none', 'diffuse_alpha', 'none'),
            map_uv_transforms=(('emissive',
                (IDENTITY, IDENTITY, IDENTITY, (2., 2., 0., 0., 0.), IDENTITY)),))
        data, _ = embed_diffuse_textures(glb_bytes(mesh), mats)
        doc, binary = _unpack_glb(data)
        armor = next(m for m in doc['materials']
                     if m['name'].startswith('CryEngine material slot 3 '))
        reference = armor['emissiveTexture']
        self.assertNotIn('extensions', reference)
        self.assertTrue(armor['extras']['cryEngineEmissionMask']['bakedForBlender'])
        source = doc['textures'][reference['index']]['source']
        view = doc['bufferViews'][doc['images'][source]['bufferView']]
        image = Image.open(BytesIO(binary[
            view['byteOffset']:view['byteOffset'] + view['byteLength']]))
        self.assertEqual(image.size, (8, 8))
        self.assertGreater(image.getpixel((1, 2))[0], 245)
        self.assertEqual(image.getpixel((6, 2)), (0, 0, 0))
        self.assertEqual(
            armor['extensions']['KHR_materials_emissive_strength']['emissiveStrength'],
            4.)

    def test_missing_emission_alpha_fails_closed_not_full_body_glow(self):
        from texture_transform import IDENTITY
        mesh = replace(read_preview_mesh(test_mesh(), 'test.skinm'),
                       subsets=((0, 6, 3), (6, 6, 4)))
        mats = PreviewMaterials(
            (None, None, None, Image.new('RGB', (4, 4), (40, 40, 40)), None),
            ('none', 'none', 'none', 'armor', 'none'), 'lennox.mtl',
            emissive_maps=(None, None, None,
                           Image.new('RGB', (4, 4), (255, 50, 10)), None),
            emissive_strengths=(0., 0., 0., 4., 0.),
            emissive_masks=('none', 'none', 'none', 'diffuse_alpha', 'none'),
            map_uv_transforms=(('emissive',
                (IDENTITY, IDENTITY, IDENTITY, (2., 2., 0., 0., 0.), IDENTITY)),))
        data, _ = embed_diffuse_textures(glb_bytes(mesh), mats)
        doc, _ = _unpack_glb(data)
        armor = next(m for m in doc['materials']
                     if m['name'].startswith('CryEngine material slot 3 '))
        self.assertNotIn('emissiveTexture', armor)
        self.assertIn('emission omitted',
                      armor['extras']['cryEngineEmissionExportWarning'])

    def test_weight_status_does_not_invent_rig(self):
        self.assertEqual(weight_stream_status(test_mesh())['status'],
                         'no bone mapping stream')

    def test_folder_export_preserves_originals(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'models'
            output = folder / 'export'
            source.mkdir(); output.mkdir()
            (source / 'test.skinm').write_bytes(test_mesh())
            with patch('fbx_export_converter.convert_glb_to_fbx',
                       side_effect=lambda _blob, file, **_: Path(file).write_bytes(
                           b'Kaydara FBX Binary  TEST')):
                done, errors = export_extracted_folder(source, output)
            self.assertEqual(len(errors), 0, errors)
            self.assertEqual(len(done), 1)
            self.assertEqual(done[0][3]['texture_count'], 0)
            self.assertTrue((output / 'test.fbx').is_file())
            self.assertEqual((source/'test.skinm').read_bytes(), test_mesh())
            with self.assertRaisesRegex(ValueError, 'separate'):
                export_extracted_folder(source, source)

    def test_readable_archive_descriptions(self):
        from pak_browser import archive_description
        self.assertIn('Skeletons', archive_description(
            'Game/characters_hunters_merc_sunny_data.pak'))
        self.assertEqual(archive_description(
            'Game/characters_hunters_merc_sunny_ts.pak'), 'Sunny Textures')
        self.assertIn('blending definitions', archive_description(
            'Game/animations_hunters.pak'))


if __name__ == '__main__':
    unittest.main()
