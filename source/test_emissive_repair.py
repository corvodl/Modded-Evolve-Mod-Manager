"""Regression tests: CryEngine glow maps, Blender emission and safe mesh repair."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from xml.etree import ElementTree as ET

from PIL import Image

from emissive_materials import bake_emission_map, emission_settings, emission_mode
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


class KalaLegacyGlowTests(unittest.TestCase):
    """Matches the material declarations from the local Kala investigation."""

    def test_kala_legacy_black_emissive_default_is_not_a_multiplier(self):
        mtl = ET.fromstring('''<Material Name="Body" Shader="Illum"
            Emissive="0,0,0" StringGenMask="%ALPHAGLOW%" Opacity="1">
            <PublicParams GlowAmount="3"/></Material>''')
        self.assertEqual(emission_mode(mtl), 'legacy_alpha')
        amount, color, ref = emission_settings(mtl)
        self.assertEqual((amount, color, ref), (3., None, None))
        diffuse = Image.new('RGBA', (2, 1))
        diffuse.putdata([(200, 100, 50, 128), (200, 100, 50, 0)])
        baked = bake_emission_map(diffuse, color=color)
        self.assertEqual(baked.getpixel((0, 0)), (100, 50, 25))
        self.assertEqual(baked.getpixel((1, 0)), (0, 0, 0))
        self.assertEqual(diffuse.getpixel((0, 0)), (200, 100, 50, 128))

    def test_kala_illum_decal_respects_diffuse_alpha_mask(self):
        mtl = ET.fromstring('''<Material Name="Glow" Shader="Illum"
            Emissive="0,0,0"><PublicParams GlowAmount="3"/>
            <Textures><Texture Map="Diffuse" File="body.tif"/>
            <Texture Map="Decal" File="chroma_fan.tif"/></Textures></Material>''')
        self.assertEqual(emission_mode(mtl), 'legacy_decal')
        amount, color, ref = emission_settings(mtl)
        self.assertEqual((amount, color, ref), (3., None, 'chroma_fan.tif'))
        diffuse = Image.new('RGBA', (2, 1))
        diffuse.putdata([(100, 0, 0, 0), (100, 0, 0, 255)])
        decal = Image.new('RGB', (2, 1), (90, 180, 240))
        baked = bake_emission_map(diffuse, color=color, emission=decal,
                                  diffuse_alpha_mask=True)
        self.assertEqual(baked.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(baked.getpixel((1, 0)), (90, 180, 240))

    def test_modern_emittance_does_not_inherit_diffuse_alpha(self):
        mtl = ET.fromstring('''<Material Shader="PBR" EmissiveIntensity="2">
            <Textures><Texture Map="Emittance" File="neon.tif"/></Textures>
            </Material>''')
        self.assertEqual(emission_mode(mtl), 'modern_map')
        diffuse = Image.new('RGBA', (1, 1), (2, 2, 2, 0))
        emission = Image.new('RGB', (1, 1), (20, 80, 160))
        result = bake_emission_map(diffuse, emission=emission,
                                   diffuse_alpha_mask=False)
        self.assertEqual(result.getpixel((0, 0)), (20, 80, 160))

    def test_explicit_modern_black_color_remains_intentional(self):
        mtl = ET.fromstring('<Material Shader="PBR" EmissiveIntensity="2" EmissiveColor="0,0,0" />')
        self.assertEqual(emission_mode(mtl), 'modern_color')
        strength, color, _ = emission_settings(mtl)
        self.assertEqual((strength, color), (2., (0., 0., 0.)))
        self.assertIsNone(bake_emission_map(None, color=color))
        black = bake_emission_map(Image.new('RGB', (1,1), 'white'),
                                  color=color, diffuse_alpha_mask=False)
        self.assertIsNone(black.getbbox())

    def test_disabled_and_non_emissive_alpha_stay_off(self):
        self.assertEqual(emission_settings(ET.fromstring(
            '<Material Shader="Illum" GlowAmount="0" Emissive="0,0,0" />')),
            (0., None, None))
        self.assertEqual(emission_mode(ET.fromstring(
            '<Material Shader="Glass" Opacity="0.4" />')), 'none')

    def test_explicit_parameter_alias_priority_is_stable(self):
        from emissive_materials import _parameter
        mtl = ET.fromstring('''<Material Emissive="0,0,0" EmissiveColor="0.25,0.5,1">
            <PublicParams EmissiveColor="0,1,0"/></Material>''')
        self.assertEqual(_parameter(mtl, 'EmissiveColor', 'Emissive'),
                         '0.25,0.5,1')


class EmissiveRendererInitializationTests(unittest.TestCase):
    def test_mocked_renderer_binds_emissive_unit_eight(self):
        from OpenGL import GL
        from OpenGL.GL import shaders
        from renderer_3 import Renderer3, SAMPLER_UNITS
        locations = {name: i+1 for i, (name, _) in enumerate(SAMPLER_UNITS)}
        with (patch.object(shaders,'compileShader',return_value=2),
              patch.object(shaders,'compileProgram',return_value=5),
              patch.object(GL,'glGetUniformLocation',
                           side_effect=lambda program,name: locations.get(name, 99)) as get_loc,
              patch.object(GL,'glUseProgram') as use,
              patch.object(GL,'glUniform1i') as bind,
              patch.object(GL,'glDeleteProgram')):
            shader = Renderer3()
            self.assertIn('emissive_map', shader.uniforms)
            get_loc.assert_any_call(5,'emissive_map')
            bind.assert_any_call(shader.uniforms['emissive_map'],8)
            self.assertEqual(use.call_args.args, (0,))
            shader.close()

    def test_failed_uniform_registration_releases_program_and_unbinds(self):
        from OpenGL import GL
        from OpenGL.GL import shaders
        from renderer_3 import Renderer3
        with (patch.object(shaders,'compileShader',return_value=2),
              patch.object(shaders,'compileProgram',return_value=5),
              patch.object(GL,'glGetUniformLocation',return_value=3),
              patch.object(GL,'glUseProgram') as use,
              patch.object(GL,'glUniform1i',
                           side_effect=RuntimeError('bind failed')),
              patch.object(GL,'glDeleteProgram') as delete):
            with self.assertRaisesRegex(RuntimeError,'bind failed'):
                Renderer3()
            delete.assert_called_once_with(5)
            self.assertEqual(use.call_args.args,(0,))


class EmissiveDiagnosticsTests(unittest.TestCase):
    def test_shader_failure_and_black_bake_are_visible_to_user(self):
        from qt_gpu_viewer import QtGPUModelPanel
        label = SimpleNamespace(setText=Mock())
        canvas = SimpleNamespace(
            materials=PreviewMaterials(
                (Image.new('RGBA',(1,1),'white'),),('Kala Body',),'tech_3p.mtl',
                emissive_maps=(Image.new('RGB',(1,1),'black'),),
                emissive_strengths=(3.,),emissive_black=(True,)),
            mesh=SimpleNamespace(subsets=((0,3,0),)),
            gl_error="Enhanced material shader unavailable: 'emissive_map'",
            _renderer3=None, emission_enabled=True, emissive_textures=[0],
            _pending_texture_uploads=(), bloom_error=None)
        panel=SimpleNamespace(
            canvas=canvas, emissive_source_info=label,
            part_select=SimpleNamespace(currentData=lambda:0))
        QtGPUModelPanel._update_emissive_source_status(panel)
        message=label.setText.call_args.args[0]
        self.assertIn('entirely black',message)
        self.assertIn('Renderer failed',message)
        self.assertIn('emissive_map',message)


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
