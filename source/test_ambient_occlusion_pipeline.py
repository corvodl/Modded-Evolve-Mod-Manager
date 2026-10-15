"""CryEngine AO discovery, packed channels, GLSL and Blender glTF regression.

Fixtures are synthetic DDS/MTL data; never modify the user's game archives.
"""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image
from material_preview import PreviewMaterials, load_preview_materials
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb
from blender_texture_folder import write_texture_folder
from level_blender_export import SceneAssembler
from renderer_3 import FRAGMENT, Renderer3
from texture_transform import transform_for, IDENTITY


PREFIX = 'characters/hunters/merc_test/'


def triangle():
    return SimpleNamespace(vertices=[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],
                           triangles=[(0,1,2)],
                           uvs=[(0.,0.),(1.,0.),(0.,1.)], subsets=())


def material_fixture(folder, xml, images):
    directory=Path(folder)
    material=directory/'body.mtl'
    material.write_text(xml,encoding='utf-8')
    index={PREFIX+'body.mtl': [{
        'file':material,'relative':PREFIX+'body.mtl','workspace':None}]}
    for name, mode, rgba in images:
        source=directory/(name+'.dds')
        Image.new(mode,(8,8),rgba).save(source,'DDS')
        index[PREFIX+source.name]=[{
            'file':source,'relative':PREFIX+source.name,'workspace':None}]
    return load_preview_materials(directory,PREFIX+'body.skinm',index=index)


class AmbientOcclusionTests(unittest.TestCase):
    def test_explicit_ao_material_texture_resolves_and_exports(self):
        with TemporaryDirectory() as temp:
            xml=('<Material AOStrength="0.35"><Textures>'
                 '<Texture Map="Diffuse" File="'+PREFIX+'body_d.tif"/>'
                 '<Texture Map="AmbientOcclusion" File="'+PREFIX+'body_ao.tif">'
                 '<TexMod TileU="2" TileV="3" OffsetV="0.125"/>'
                 '</Texture></Textures></Material>')
            materials=material_fixture(temp,xml,[
                ('body_d','RGB',(180,150,140)),
                ('body_ao','RGB',(64,64,64))])
            self.assertIsNotNone(materials.ao_maps[0])
            self.assertEqual(materials.ao_maps[0].size,(8,8))
            self.assertEqual(materials.ao_maps[0].getpixel((0,0)),(64,64,64))
            self.assertAlmostEqual(materials.ao_strengths[0],0.35)
            self.assertEqual(transform_for(materials,0,'ao')[:2],(2.,3.))
            self.assertEqual(transform_for(materials,0,'normal'),IDENTITY)
            payload,count=embed_diffuse_textures(glb_bytes(triangle()),materials)
            self.assertEqual(count,1)
            doc,binary=_unpack_glb(payload)
            mat=doc['materials'][0]
            info=mat['occlusionTexture']
            self.assertEqual(info['strength'],0.35)
            self.assertEqual(info['extensions']['KHR_texture_transform']['scale'],[2.,3.])
            self.assertIn('KHR_texture_transform',doc['extensionsUsed'])
            pic=doc['images'][doc['textures'][info['index']]['source']]
            self.assertEqual(pic['name'],'Ao slot 0')
            view=doc['bufferViews'][pic['bufferView']]
            with Image.open(BytesIO(binary[view['byteOffset']:
                                           view['byteOffset']+view['byteLength']])) as ao:
                self.assertEqual(ao.getpixel((0,0)),(64,64,64))
            saved=write_texture_folder(payload,Path(temp)/'body.glb')
            self.assertEqual(len(saved),2)
            self.assertTrue(any('Ao' in p.name for p in saved))

    def test_explicit_packed_ao_channel_is_extracted_from_specular_alpha(self):
        with TemporaryDirectory() as temp:
            xml=('<Material><Textures>'
                 '<Texture Map="Diffuse" File="'+PREFIX+'body_d.tif"/>'
                 '<Texture Map="Specular" AOChannel="A" File="'
                 +PREFIX+'body_s.tif"/>'
                 '</Textures></Material>')
            materials=material_fixture(temp,xml,[
                ('body_d','RGB',(100,120,160)),
                ('body_s','RGBA',(24,50,100,201))])
            self.assertEqual(materials.ao_maps[0].getpixel((0,0)),
                             (201,201,201))
            self.assertEqual(materials.ao_strengths[0],1.)
            exported,_=embed_diffuse_textures(glb_bytes(triangle()),materials)
            doc,binary=_unpack_glb(exported)
            info=doc['materials'][0]['occlusionTexture']
            view=doc['bufferViews'][doc['images'][
                doc['textures'][info['index']]['source']]['bufferView']]
            with Image.open(BytesIO(binary[view['byteOffset']:
                                           view['byteOffset']+view['byteLength']])) as ao:
                self.assertEqual(ao.getpixel((0,0)),(201,201,201))

    def test_unmarked_alpha_channel_does_not_become_ao(self):
        with TemporaryDirectory() as temp:
            xml=('<Material><Textures>'
                 '<Texture Map="Diffuse" File="'+PREFIX+'body_d.tif"/>'
                 '<Texture Map="Specular" File="'+PREFIX+'body_s.tif"/>'
                 '</Textures></Material>')
            materials=material_fixture(temp,xml,[
                ('body_d','RGB',(160,130,100)),
                ('body_s','RGBA',(100,100,100,15))])
            self.assertIsNone(materials.ao_maps[0])
            exported,_=embed_diffuse_textures(glb_bytes(triangle()),materials)
            doc,_=_unpack_glb(exported)
            self.assertNotIn('occlusionTexture',doc['materials'][0])

    def test_invalid_channel_or_missing_ao_is_visible_not_silently_assigned(self):
        with TemporaryDirectory() as temp:
            for ao in (
                '<Texture Map="AO" Channel="Q" File="'+PREFIX+'body_s.tif"/>',
                '<Texture Map="AO" File="'+PREFIX+'missing_ao.tif"/>'
            ):
                xml=('<Material><Textures>'
                     '<Texture Map="Diffuse" File="'+PREFIX+'body_d.tif"/>'
                     +ao+'</Textures></Material>')
                materials=material_fixture(temp,xml,[
                    ('body_d','RGB',(140,140,140)),
                    ('body_s','RGB',(10,10,10))])
                self.assertIsNone(materials.ao_maps[0])
                self.assertTrue(any('AO' in issue or 'packed channel' in issue
                                    for issue in materials.issues))

    def test_level_scene_relocates_ao_texture_index(self):
        materials=PreviewMaterials(
            (Image.new('RGB',(4,4),(200,200,200)),),('body',),'body.mtl',
            ao_maps=(Image.new('RGB',(4,4),(75,75,75)),),
            ao_strengths=(0.8,))
        blob,_=embed_diffuse_textures(glb_bytes(triangle()),materials)
        builder=SceneAssembler('test AO relocation')
        builder.append_glb('first',blob)
        builder.append_glb('second',blob)
        doc,_=_unpack_glb(builder.finish())
        self.assertEqual(len(doc['textures']),4)
        self.assertEqual([mat['occlusionTexture']['index']
                          for mat in doc['materials']],[1,3])

    def test_gpu_uses_linear_red_channel_on_indirect_only(self):
        self.assertIn('texture2D(ao_map,ao_coord).r',FRAGMENT)
        self.assertIn('ambientOcclusion',FRAGMENT)
        self.assertIn('indirect+diffuse*0.76*illumination',FRAGMENT)
        self.assertIn("'ao_map'",str(Renderer3.__init__.__code__.co_consts))
        self.assertIn('ao_uv',FRAGMENT)
        self.assertIn('has_ao',FRAGMENT)


if __name__=='__main__':
    unittest.main()
