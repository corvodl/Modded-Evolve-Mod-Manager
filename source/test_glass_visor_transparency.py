"""Refractive visor rendering and Blender handoff regressions (synthetic).

CryEngine Glass can have an opaque orange diffuse DDS and Opacity=1 while
remaining refractive in-game. Test the actual material flag, not filename.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from PIL import Image

from material_preview import (PreviewMaterials, GLASS_PREVIEW_SURFACE_OPACITY,
                              load_preview_materials, preview_material_opacity)
from character_assembly import combine_meshes
from model_preview import PreviewMesh
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb


def mesh_for_slot(slot):
    return PreviewMesh(((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
                       ((0,1,2),),(.5,.5,0.),1.,'visor',
                       ((0.,0.),(1.,0.),(0.,1.)),((0,3,slot),))


class CryEngineGlassVisorTests(TestCase):
    def test_glass_shader_without_alpha_still_transmits_and_opaque_body_does_not(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            virtual = 'characters/hunters/merc_example/'
            material = root / 'visor.mtl'
            material.write_text(
                '<Material><SubMaterials>'
                '<Material Name="Orange Visor" Shader="Glass" Opacity="1" '
                'IOR="1.45" Diffuse="1,1,1"><Textures>'
                '<Texture Map="Diffuse" File="characters/hunters/merc_example/visor_diff.tif"/>'
                '</Textures></Material>'
                '<Material Name="Helmet Shell" Shader="Illum" Opacity="1">'
                '<Textures><Texture Map="Diffuse" '
                'File="characters/hunters/merc_example/shell_diff.tif"/>'
                '</Textures></Material></SubMaterials></Material>', encoding='utf-8')
            visor = root / 'visor_diff.dds'
            shell = root / 'shell_diff.dds'
            Image.new('RGB', (8,8), (235,105,25)).save(visor,'DDS')
            Image.new('RGB', (8,8), (25,40,75)).save(shell,'DDS')
            def record(name, file):
                return [{'file':file,'relative':virtual+name,'workspace':root}]
            index = {
                virtual+'visor.mtl':record('visor.mtl',material),
                virtual+'visor_diff.dds':record('visor_diff.dds',visor),
                virtual+'shell_diff.dds':record('shell_diff.dds',shell),
            }
            mats = load_preview_materials(root, virtual+'visor.skinm', index=index)
        self.assertEqual(mats.opacity, (1.,1.))  # actual MTL untouched
        self.assertEqual(mats.glass_materials, (True,False))
        self.assertEqual(mats.refraction_indices, (1.45,None))
        self.assertEqual(mats.alpha_modes, (False,False))
        self.assertEqual(mats.textures[0].convert('RGB').getpixel((1,1)), (235,105,25))
        self.assertEqual(preview_material_opacity(mats,0),
                         GLASS_PREVIEW_SURFACE_OPACITY)
        self.assertEqual(preview_material_opacity(mats,1),1.)
        self.assertTrue(any('approximate preview transmission' in s for s in mats.issues))

    def test_explicit_transparency_overrides_glass_fallback(self):
        m = PreviewMaterials((Image.new('RGB',(2,2),'orange'),),
                             ('Glass',),'glass.mtl',
                             opacity=(.65,),glass_materials=(True,))
        self.assertAlmostEqual(preview_material_opacity(m,0),.65)
        m2=PreviewMaterials(m.textures,m.names,m.source,opacity=(.99,),
                            glass_materials=(True,))
        self.assertAlmostEqual(preview_material_opacity(m2,0),
                               GLASS_PREVIEW_SURFACE_OPACITY)
        body=PreviewMaterials(m.textures,m.names,m.source,opacity=(1.,),
                              glass_materials=(False,))
        self.assertEqual(preview_material_opacity(body,0),1.)

    def test_cdf_preserves_refractive_material_and_ior_per_original_subset(self):
        slots = (2, 11)
        mesh = PreviewMesh(((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
                          ((0,1,2),(0,1,2)),(.5,.5,0.),1.,'source',
                          ((0.,0.),(1.,0.),(0.,1.)),
                          ((0,3,2),(3,3,11)))
        visor=Image.new('RGB',(4,4),(238,113,22))
        alpha=tuple(visor if i==11 else None for i in range(12))
        materials=PreviewMaterials(alpha,tuple('Visor' if i==11 else 'Armor'
                                               for i in range(12)),'visor.mtl',
             opacity=(1.,)*12,alpha_modes=(False,)*12,
             glass_materials=tuple(i==11 for i in range(12)),
             refraction_indices=tuple(1.4 if i==11 else None for i in range(12)),
             reflection_strengths=tuple(.7 if i==11 else 0. for i in range(12)))
        combined, result=combine_meshes([('helmet',mesh,materials)])
        self.assertEqual(tuple(s for _,_,s in combined.subsets),(0,1))
        self.assertEqual(result.glass_materials,(False,True))
        self.assertEqual(result.refraction_indices,(None,1.4))
        self.assertEqual(result.reflection_strengths,(0.,.7))
        self.assertAlmostEqual(preview_material_opacity(result,1),
                               GLASS_PREVIEW_SURFACE_OPACITY)
        self.assertEqual(preview_material_opacity(result,0),1.)

    def test_blender_glb_exports_transmission_without_double_alpha_blending(self):
        visor=Image.new('RGB',(4,4),(236,100,22))
        glass=PreviewMaterials((visor,),('Visor',),'visor.mtl',
            opacity=(1.,),glass_materials=(True,),
            refraction_indices=(1.45,),alpha_modes=(False,))
        data, count=embed_diffuse_textures(glb_bytes(mesh_for_slot(0)),glass)
        self.assertEqual(count,1)
        doc,_=_unpack_glb(data)
        material=doc['materials'][0]
        self.assertEqual(material['alphaMode'],'OPAQUE')
        self.assertEqual(material['pbrMetallicRoughness']['baseColorFactor'][3],1.)
        self.assertAlmostEqual(material['extensions']['KHR_materials_transmission']
                               ['transmissionFactor'],1.-GLASS_PREVIEW_SURFACE_OPACITY)
        self.assertEqual(material['extensions']['KHR_materials_ior']['ior'],1.45)
        self.assertIn('KHR_materials_transmission',doc['extensionsUsed'])
        self.assertEqual(material['extras']['cryEngineAuthoredOpacity'],1.)
        # A regular helmet painted orange does not become transparent.
        opaque=PreviewMaterials((visor,),('Helmet',),'helm.mtl',
            opacity=(1.,),glass_materials=(False,))
        data,_=embed_diffuse_textures(glb_bytes(mesh_for_slot(0)),opaque)
        helmet=_unpack_glb(data)[0]['materials'][0]
        self.assertEqual(helmet['alphaMode'],'OPAQUE')
        self.assertNotIn('KHR_materials_transmission',helmet.get('extensions',{}))
