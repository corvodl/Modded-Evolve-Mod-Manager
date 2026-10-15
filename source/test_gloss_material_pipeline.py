"""Explicit CryEngine gloss and normal maps in GPU and Blender GLB."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image

from material_gloss import gloss_to_metallic_roughness
from material_preview import PreviewMaterials, load_preview_materials
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb
from renderer_3 import FRAGMENT, Renderer3
from blender_texture_folder import write_texture_folder


class GlossMaterialTests(unittest.TestCase):
    def test_explicit_gloss_inverts_to_linear_roughness_without_resizing(self):
        image = Image.new('L', (2, 2))
        image.putdata([0, 64, 192, 255])
        converted = gloss_to_metallic_roughness(image)
        self.assertEqual(converted.size, (2, 2))
        self.assertEqual(list(converted.getdata()), [
            (255, 255, 0), (255, 191, 0), (255, 63, 0), (255, 0, 0),
        ])
        with self.assertRaises(ValueError):
            gloss_to_metallic_roughness(None)

    def test_gloss_map_requires_explicit_material_mapping(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            virtual = 'characters/hunters/merc_test/'
            material = base / 'body.mtl'
            material.write_text(
                '<Material><Textures>'
                '<Texture Map="Diffuse" File="' + virtual + 'body_diff.tif"/>'
                '<Texture Map="Gloss" File="' + virtual + 'body_gloss.tif"/>'
                '<Texture Map="Normal" File="' + virtual + 'body_ddn.tif"/>'
                '</Textures></Material>', encoding='utf-8')
            files = [material]
            for stem, gray in [('body_diff', 100), ('body_gloss', 224),
                               ('body_ddn', 128)]:
                file = base / (stem + '.dds')
                Image.new('RGB', (4, 4), (gray, gray, gray)).save(file, 'DDS')
                files.append(file)
            index = {virtual + file.name: [
                {'file': file, 'relative': virtual + file.name, 'workspace': None}]
                for file in files}
            materials = load_preview_materials(base, virtual+'body.skinm',
                                               index=index)
            self.assertIsNotNone(materials.normal_maps[0])
            self.assertIsNotNone(materials.gloss_maps[0])
            self.assertIsNone(materials.specular_maps[0])
            self.assertEqual(materials.gloss_maps[0].size, (4, 4))

    def test_glb_binds_gloss_as_roughness_and_keeps_normals(self):
        mesh = SimpleNamespace(
            vertices=[(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)],
            triangles=[(0, 1, 2)], uvs=[(0., 0.), (1., 0.), (0., 1.)],
            subsets=())
        diffuse = Image.new('RGB', (4, 4), (120, 100, 80))
        gloss = Image.new('L', (4, 4), 220)
        normal = Image.new('RGB', (4, 4), (128, 128, 255))
        specular = Image.new('RGB', (4, 4), (80, 80, 80))
        mats = PreviewMaterials((diffuse,), ('body',), 'body.mtl',
                                normal_maps=(normal,), specular_maps=(specular,),
                                gloss_maps=(gloss,))
        exported, count = embed_diffuse_textures(glb_bytes(mesh), mats)
        self.assertEqual(count, 1)
        doc, buffer = _unpack_glb(exported)
        self.assertEqual(len(doc['images']), 4)
        self.assertEqual(len(buffer), doc['buffers'][0]['byteLength'])
        mat = doc['materials'][0]
        self.assertIn('normalTexture', mat)
        pbr = mat['pbrMetallicRoughness']
        self.assertEqual(pbr['roughnessFactor'], 1.0)
        self.assertEqual(pbr['metallicFactor'], 0.0)
        tex = doc['textures'][pbr['metallicRoughnessTexture']['index']]
        img = doc['images'][tex['source']]
        self.assertEqual(img['name'], 'Roughness from gloss slot 0')
        view = doc['bufferViews'][img['bufferView']]
        bytes_png = bytes(buffer[view['byteOffset']:
                                 view['byteOffset'] + view['byteLength']])
        with Image.open(BytesIO(bytes_png)) as surface:
            self.assertEqual(surface.getpixel((0,0)), (255, 35, 0))
        with TemporaryDirectory() as temp:
            saved = write_texture_folder(exported, Path(temp) / 'body.glb')
            self.assertEqual(len(saved), 4)
            self.assertTrue(any('Roughness' in p.name for p in saved))

    def test_shader_binds_separate_gloss_and_normal_maps(self):
        self.assertIn('texture2D(normal_map,diffuse_coord)', FRAGMENT)
        self.assertIn('texture2D(gloss_map,diffuse_coord)', FRAGMENT)
        self.assertIn('mix(8.0,128.0,smoothness)', FRAGMENT)
        self.assertIn('has_gloss', FRAGMENT)
        self.assertIn('gloss_map', str(Renderer3.__init__.__code__.co_consts))
