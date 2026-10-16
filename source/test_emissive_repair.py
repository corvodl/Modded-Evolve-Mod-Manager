"""Regression tests: CryEngine glow maps, Blender emission and safe mesh repair."""
from __future__ import annotations

from dataclasses import replace
import unittest
from xml.etree import ElementTree as ET

from PIL import Image

from emissive_materials import bake_emission_map, emission_settings
from material_preview import PreviewMaterials
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_export_repair import prepare_export_mesh
from model_textured_export import embed_diffuse_textures, _unpack_glb
from test_model_preview import test_mesh


class EmissiveMaterialTests(unittest.TestCase):
    def test_glow_amount_and_named_shader_params(self):
        sample = ET.fromstring('''<Material Shader="Illum"><PublicParams GlowAmount="2.0" />
            <Textures><Texture Map="Diffuse" File="tubes_diffuse.tif" />
                      <Texture Map="Decal" File="tubes_glow.tif" /></Textures></Material>''')
        self.assertEqual(emission_settings(sample),
                         (2.0, None, 'tubes_glow.tif'))
        second = ET.fromstring('''<Material Shader="HumanSkin"><ShaderParams>
            <Param Name="GlowAmount" Value="0.75" />
            </ShaderParams></Material>''')
        self.assertEqual(emission_settings(second)[0], 0.75)

    def test_plain_alpha_does_not_automatically_emit(self):
        material = ET.fromstring('''<Material Shader="Glass"><Textures>
            <Texture Map="Diffuse" File="glass.tif"/></Textures></Material>''')
        self.assertEqual(emission_settings(material), (0.0, None, None))
        no_alpha = ET.fromstring('<Material Shader="Illum" GlowAmount="0" />')
        self.assertEqual(emission_settings(no_alpha), (0.0, None, None))

    def test_explicit_emissive_texture(self):
        material = ET.fromstring('''<Material><Textures>
            <Texture Map="Emittance" File="emissive.tif" />
            </Textures></Material>''')
        self.assertEqual(emission_settings(material), (1., None, 'emissive.tif'))

    def test_diffuse_alpha_bakes_emission_not_transparency(self):
        surface = Image.new('RGBA', (2, 1))
        surface.putdata(((0, 100, 255, 255), (0, 100, 255, 0)))
        emission = bake_emission_map(surface)
        self.assertEqual(emission.getpixel((0, 0)), (0, 100, 255))
        self.assertEqual(emission.getpixel((1, 0)), (0, 0, 0))
        self.assertEqual(surface.getpixel((1, 0)), (0, 100, 255, 0))

    def test_glb_emissive_uses_pbr_slots_and_preserves_alpha_mode(self):
        mesh = replace(read_preview_mesh(test_mesh(), 'tubes.skinm'),
                       uvs=((0., 0.), (1., 0.), (0., 1.), (1., 1.)))
        base = glb_bytes(mesh)
        diffuse = Image.new('RGBA', (2, 2), (20, 30, 220, 128))
        glow = Image.new('RGB', (2, 2), (0, 100, 255))
        materials = PreviewMaterials(
            (diffuse,), ('Tubes',), 'tubes.mtl',
            alpha_modes=(False,), opacity=(1.,),
            emissive_maps=(glow,), emissive_strengths=(2.5,))
        output, diffuse_count = embed_diffuse_textures(base, materials)
        self.assertEqual(diffuse_count, 1)
        doc, binary = _unpack_glb(output)
        material = doc['materials'][0]
        self.assertEqual(material['alphaMode'], 'OPAQUE')
        self.assertEqual(material['emissiveFactor'], [1., 1., 1.])
        self.assertEqual(material['emissiveTexture']['index'], 1)
        self.assertEqual(material['extensions']['KHR_materials_emissive_strength']['emissiveStrength'], 2.5)
        self.assertIn('KHR_materials_emissive_strength', doc['extensionsUsed'])
        self.assertEqual(len(doc['images']), 2)

    def test_transparent_texture_remains_transparent_without_glow(self):
        mesh = read_preview_mesh(test_mesh(), 'glass.skinm')
        base = glb_bytes(mesh)
        materials = PreviewMaterials(
            (Image.new('RGBA', (2, 2), (20, 30, 220, 120)),),
            ('Glass',), 'glass.mtl', alpha_modes=(True,), opacity=(.6,))
        output, _ = embed_diffuse_textures(base, materials)
        material = _unpack_glb(output)[0]['materials'][0]
        self.assertEqual(material['alphaMode'], 'BLEND')
        self.assertNotIn('emissiveTexture', material)


class SafeModelRepairTests(unittest.TestCase):
    def test_repair_drops_degenerate_faces_but_keeps_vertex_ids_and_uvs(self):
        base = read_preview_mesh(test_mesh())
        uv = ((0.,0.), (1.,0.), (0.,1.), (1.,1.))
        mesh = replace(base, triangles=base.triangles+((1,1,2),),
                       subsets=((0, 15, 0),), uvs=uv)
        repaired, report = prepare_export_mesh(mesh)
        self.assertEqual(report.degenerate_faces, 1)
        self.assertEqual(len(repaired.vertices), len(base.vertices))
        self.assertEqual(repaired.triangles, base.triangles)
        self.assertEqual(repaired.uvs, uv)

    def test_nearly_equal_uvs_snap_without_welding_seams(self):
        base = read_preview_mesh(test_mesh())
        uv = ((0.,0.), (1.,0.), (0.,1.), (0.000000003,0.))
        repaired, report = prepare_export_mesh(replace(base,uvs=uv))
        self.assertEqual(repaired.uvs[0], repaired.uvs[3])
        self.assertEqual(report.uv_coordinates_snapped, 1)
        self.assertEqual(len(repaired.vertices), len(base.vertices))


if __name__ == '__main__':
    unittest.main()
