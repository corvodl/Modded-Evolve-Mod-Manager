"""Character visibility, material tint, and permanent attachment regressions."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from material_preview import PreviewMaterials, material_color, load_preview_materials
from character_assembly import assemble_character, combine_meshes
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb
from test_model_rigging import tiny_mesh

class CharacterAppearanceTests(unittest.TestCase):
    def test_tint_preserves_alpha_and_source(self):
        image=Image.new('RGBA',(1,1),(220,220,220,37))
        tinted=material_color(image,'0.55834,0.35640,0.20508')
        r,g,b,a=tinted.getpixel((0,0))
        self.assertGreater(r,g);self.assertGreater(g,b)
        self.assertEqual(a,37)
        self.assertEqual(image.getpixel((0,0)),(220,220,220,37))
        self.assertEqual(material_color(image,'1,1,1').tobytes(),image.tobytes())

    def test_hidden_draws_removed_without_changing_vertex_accessors(self):
        mesh=replace(read_preview_mesh(tiny_mesh()),subsets=((0,6,0),(6,6,1)))
        materials=PreviewMaterials((None,Image.new('RGB',(1,1),'red')),('hidden','body'),'test',hidden=(True,False))
        combined,mat=combine_meshes([('body',mesh,materials)])
        self.assertEqual(mat.hidden,(True,False))
        initial=glb_bytes(combined)
        original,_=_unpack_glb(initial)
        result,_=embed_diffuse_textures(initial,mat)
        doc,_=_unpack_glb(result)
        self.assertEqual(len(doc['meshes'][0]['primitives']),1)
        p=doc['meshes'][0]['primitives'][0]
        self.assertEqual(p['material'],1)
        self.assertEqual(doc['accessors'][p['attributes']['POSITION']],original['accessors'][p['attributes']['POSITION']])

    def test_maggie_body_is_not_equipment_when_flashlight_is_first(self):
        prefix='characters/hunters/merc_maggiesavage/maggiesavage_3p'
        skins=(('flashlight',prefix+'_flashlight.skin'),('maggiebody',prefix+'.skin'),('headgear',prefix+'_headwear.skin'),('rifle','characters/hunters/rifle.skin'))
        with TemporaryDirectory() as folder:
            mesh=Path(folder)/'mesh';mesh.write_bytes(tiny_mesh())
            with patch('character_assembly._character_definition',return_value=(prefix+'.cdf','m.chr',skins,{})), patch('character_assembly._resolve_file',return_value=mesh), patch('character_assembly.load_preview_materials',return_value=None):
                for selection in ('body','characters/hunters/rifle.skin'):
                    c=assemble_character(None,prefix+'.skinm',{},equipment=selection)
                    self.assertEqual(c.included[:3],('maggiebody','flashlight','headgear'))
                    self.assertEqual(c.equipment_options,(skins[3],))

    def test_glass_without_map_is_colored_but_nodraw_stays_hidden(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.mtl'
            path.write_text('<Material><SubMaterials><Material Name="glass" Shader="Glass" Diffuse="0.57,0.10,0.007" Opacity="0.99"/><Material Name="tail" Shader="NoDraw"/></SubMaterials></Material>')
            entry={'file':path,'relative':'test.mtl','workspace':None}
            with patch('model_material_resolve.find_model_material',return_value=(entry,'test',())):
                m=load_preview_materials(None,'characters/test.skinm',index={})
            self.assertEqual(m.hidden,(False,True))
            self.assertIsNotNone(m.textures[0]);self.assertIsNone(m.textures[1])
            r,g,b,a=m.textures[0].getpixel((0,0))
            self.assertGreater(r,g);self.assertGreater(g,b)
            self.assertEqual(m.opacity[0],.99)
            self.assertFalse(any('missing' in x for x in m.issues))

if __name__=='__main__':unittest.main()

