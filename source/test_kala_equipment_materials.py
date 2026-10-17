"""Source-derived Kala equipment glow fixtures (no copyrighted game files).

Actual tech_3p.cdf binds Banshee and Rift Device to separate CA_SKIN
attachments; their MTLs independently author different Illum Decal colors.
"""
from unittest import TestCase
from xml.etree import ElementTree as ET

from PIL import Image

from emissive_materials import emission_settings, bake_emission_map


class KalaEquipmentMaterialTests(TestCase):
    @staticmethod
    def glow(name, authored_diffuse):
        return ET.fromstring(
            '<Material Name="' + name + '" Shader="Illum" '
            'GlowAmount="3" Emissive="0,0,0" Diffuse="' +
            authored_diffuse + '"><Textures>'
            '<Texture Map="Diffuse" File="characters/hunters/merc_tech/'
            'textures/tech_3p_body_diff.tif"/>'
            '<Texture Map="Decal" '
            'File="textures/particle/light/chroma_fan320tiled_01b.tif"/>'
            '</Textures></Material>')

    def test_banshee_blue_and_rift_purple_come_from_separate_authored_mtls(self):
        # Values are taken from techbansheegun_3p.mtl and
        # techriftdevice_3p.mtl, not inferred from reference screenshots.
        banshee = self.glow('bansheegun glow',
                           '0.072271854,0.1470273,0.42326769')
        rift = self.glow('monster arm rift device glow',
                         '0.47353151,0.17788844,0.94730669')
        amount_b, tint_b, map_b = emission_settings(banshee)
        amount_r, tint_r, map_r = emission_settings(rift)
        self.assertEqual((amount_b, amount_r), (3., 3.))
        self.assertEqual(map_b, map_r)
        self.assertEqual(map_b, 'textures/particle/light/chroma_fan320tiled_01b.tif')
        self.assertEqual(tint_b, (0.072271854,0.1470273,0.42326769))
        self.assertEqual(tint_r, (0.47353151,0.17788844,0.94730669))
        source = Image.new('RGB', (1,1), (255,255,255))
        blue = bake_emission_map(None, color=tint_b, emission=source,
                                  diffuse_alpha_mask=False).getpixel((0,0))
        purple = bake_emission_map(None, color=tint_r, emission=source,
                                    diffuse_alpha_mask=False).getpixel((0,0))
        self.assertGreater(blue[2], blue[1])
        self.assertGreater(blue[1], blue[0])
        self.assertGreater(purple[0], blue[0])
        self.assertGreater(purple[2], blue[2])
        self.assertNotEqual(blue, purple)

    def test_opaque_monster_arm_uses_banshee_source_textures_without_glow(self):
        arm = ET.fromstring(
            '<Material Name="monster arm" Shader="Illum" Diffuse="1,1,1" '
            'Emissive="0,0,0" StringGenMask="%BUMP_MAP%GLOSS_MAP%">'
            '<Textures>'
            '<Texture Map="Diffuse" File="characters/hunters/merc_tech/'
            'weapons/bansheegun/techbansheegun_3p_diff.tif"/>'
            '<Texture Map="Specular" File="characters/hunters/merc_tech/'
            'weapons/bansheegun/techbansheegun_3p_spec.tif"/>'
            '<Texture Map="Bumpmap" File="characters/hunters/merc_tech/'
            'weapons/bansheegun/techbansheegun_3p_ddna.tif"/>'
            '</Textures></Material>')
        self.assertEqual(emission_settings(arm), (0., None, None))
        self.assertEqual([t.get('Map') for t in arm.findall('./Textures/Texture')],
                         ['Diffuse', 'Specular', 'Bumpmap'])
