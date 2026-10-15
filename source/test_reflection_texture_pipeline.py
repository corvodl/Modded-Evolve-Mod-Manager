"""Synthetic CryEngine environment, glass and animated TexMod integration tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import struct
import unittest

from PIL import Image
from material_preview import load_preview_materials
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb
from texture_transform import parse_motion, transform_for
from cubemap_material import decode_cubemap_dds
from renderer_3 import FRAGMENT


def cube_header():
    raw=bytearray(128)
    raw[:4]=b'DDS '
    struct.pack_into('<I',raw,4,124)
    struct.pack_into('<I',raw,8,0x100f)
    struct.pack_into('<II',raw,12,4,4)
    struct.pack_into('<I',raw,76,32)
    struct.pack_into('<I',raw,80,4)
    raw[84:88]=b'DXT1'
    struct.pack_into('<I',raw,112,0xfe00)
    return bytes(raw)


class ReflectionAndAnimatedUVTests(unittest.TestCase):
    def test_glass_material_resolves_cube_and_preserves_faces_for_blender(self):
        with TemporaryDirectory() as directory:
            base=Path(directory)
            prefix='characters/hunters/merc_test/'
            material=base/'body.mtl'
            material.write_text(
                '<Material Shader="Glass" Opacity="0.4" IOR="1.46" '
                'ReflectionStrength="0.8"><Textures>'
                '<Texture Map="Diffuse" File="'+prefix+'body_diff.tif"/>'
                '<Texture Map="Environment" File="'+prefix+'env.dds"/>'
                '</Textures></Material>',encoding='utf-8')
            diffuse=base/'body_diff.dds'
            Image.new('RGB',(4,4),(80,100,120)).save(diffuse,'DDS')
            cube=base/'env.dds'
            cube.write_bytes(cube_header()+struct.pack('<HHI',0xf800,0,0)*6)
            index={prefix+f.name: [{'file':f,'relative':prefix+f.name,
                                   'workspace':None}]
                   for f in (material,diffuse,cube)}
            mats=load_preview_materials(base,prefix+'body.skinm',index=index)
            self.assertEqual(len(mats.environment_maps[0]),6)
            self.assertAlmostEqual(mats.reflection_strengths[0],0.8)
            self.assertAlmostEqual(mats.refraction_indices[0],1.46)
            self.assertTrue(mats.glass_materials[0])
            mesh=SimpleNamespace(vertices=[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],
                 triangles=[(0,1,2)],uvs=[(0.,0.),(1.,0.),(0.,1.)],subsets=())
            blob,_=embed_diffuse_textures(glb_bytes(mesh),mats)
            doc,_=_unpack_glb(blob)
            self.assertEqual(len(doc['images']),7)
            mat=doc['materials'][0]
            self.assertEqual(len(mat['extras']['cryEngineEnvironmentFaces']),6)
            self.assertAlmostEqual(mat['extensions']['KHR_materials_ior']['ior'],1.46)
            self.assertAlmostEqual(mat['extensions']['KHR_materials_transmission']['transmissionFactor'],0.6)
            self.assertIn('KHR_materials_transmission',doc['extensionsUsed'])

    def test_supported_named_uv_pan_and_rotation(self):
        from xml.etree import ElementTree as ET
        node=ET.fromstring('<Texture><TexMod TileU="2" '
            'TexMod_UOscillatorType="Pan" TexMod_UOscillatorRate="0.25" '
            'TexMod_VOscillatorType="Constant" TexMod_VOscillatorRate="-0.1" '
            'TexMod_RotateType="Constant" TexMod_RotateRate="90"/>'
            '</Texture>')
        motion=parse_motion(node)
        self.assertEqual(motion,(0.25,-0.1,90.))
        material=SimpleNamespace(uv_transforms=((2.,1.,0.,0.,0.),),
                                 uv_motions=(motion,),map_uv_transforms=(),
                                 map_uv_motions=())
        static=transform_for(material,0)
        animated=transform_for(material,0,seconds=2.)
        self.assertEqual(static[:2],animated[:2])
        self.assertAlmostEqual(animated[2],0.5)
        self.assertAlmostEqual(animated[3],0.8)
        self.assertAlmostEqual(animated[4],3.141592653589793)
        # Unknown engine enum IDs must not be interpreted as generic pan.
        node.find('TexMod').set('TexMod_UOscillatorType','3')
        self.assertEqual(parse_motion(node)[0],0.)

    def test_reflective_gpu_shader_uses_environment_texture(self):
        self.assertIn('textureCube(environment_map,direction)',FRAGMENT)
        self.assertIn('has_environment',FRAGMENT)
        self.assertIn('fresnel',FRAGMENT)


if __name__=='__main__':
    unittest.main()
