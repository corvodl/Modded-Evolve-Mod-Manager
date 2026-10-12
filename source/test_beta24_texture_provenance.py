"""Synthetic Lennox CDF assemblies and cross-PAK DDS provenance regressions.

No commercial game files or decoded texture pixels are committed.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from PIL import Image

from character_assembly import assemble_character
from material_preview import PreviewMaterials, load_preview_materials
from model_preview import PreviewMesh


class CharacterAssetCoverageTests(unittest.TestCase):
    def test_lennox_cockpit_is_permanent_not_equipment(self):
        # Mirrors the binding names and order from the supplied Lennox .cdf
        # with synthetic contents; the actual game file is never stored here.
        base = 'characters/hunters/merc_lennox/'
        cdf = '<CharacterDefinition><Model File="' + base + 'lennox_3p_skeleton.chr" />'
        cdf += '<AttachmentList>'
        for name,skin in (
                ('heavyassaultrifle','lennoxrifle_3p.skin'),
                ('body','lennox_3p.skin'),
                ('energywhip','lennoxenergywhip_3p.skin'),
                ('cockpit','lennox_3p_cockpit.skin')):
            cdf += ('<Attachment Type="CA_SKIN" AName="%s" Binding="%s" />'
                    % (name,base+skin))
        cdf += '</AttachmentList></CharacterDefinition>'
        with TemporaryDirectory() as folder:
            root=Path(folder)
            index={}
            def add(name,bytes_):
                path=root/name.replace('/','__')
                path.write_bytes(bytes_)
                index[name.casefold()]=[{
                    'file':path,'workspace':root,'relative':name,'archive':'test.pak'}]
            add(base+'lennox_3p.cdf',cdf.encode())
            for stem in ('lennox_3p','lennoxrifle_3p','lennoxenergywhip_3p',
                         'lennox_3p_cockpit'):
                add(base+stem+'.skinm',b'CrChF placeholder')
            mesh=PreviewMesh(((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
                             ((0,1,2),),(.5,.5,0.),1.,'synthetic',
                             ((0.,0.),(1.,0.),(0.,1.)),((0,3,0),))
            def fake_materials(_workspace,selected,*args,**kwargs):
                return PreviewMaterials((None,),('original slot',),'test.mtl',
                                        diffuse_sources=((selected+'.dds','texture PAK'),),
                                        diffuse_references=(selected+'.tif',))
            with (patch('character_assembly.read_preview_mesh',return_value=mesh),
                  patch('character_assembly.load_preview_materials',
                        side_effect=fake_materials)):
                body=base+'lennox_3p.skinm'
                default=assemble_character(root,body,index)
                self.assertEqual(default.included,(
                    'body','cockpit','heavyassaultrifle'))
                self.assertEqual(len(default.materials.diffuse_sources),3)
                self.assertIn('cockpit',default.materials.names[1])
                whip=assemble_character(
                    root,body,index,equipment=base+'lennoxenergywhip_3p.skin')
                self.assertEqual(whip.included,('body','cockpit','energywhip'))
                bare=assemble_character(root,body,index,equipment='body')
                self.assertEqual(bare.included,('body','cockpit'))

    def test_cdf_without_uv_does_not_sample_single_black_texel(self):
        from dataclasses import replace
        from character_assembly import combine_meshes
        texture = Image.new('RGB',(4,4),(220,160,115))
        mesh = PreviewMesh(((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
                           ((0,1,2),),(.5,.5,0.),1.,'part',
                           ((0.,0.),(1.,0.),(0.,1.)),((0,3,0),))
        mats = PreviewMaterials(
            (texture,),('armor',),'armor.mtl',
            diffuse_sources=(('characters/hunters/example/armor.dds','shared.pak'),),
            diffuse_references=('characters/hunters/example/armor.tif',))
        combined, result = combine_meshes([
            ('body', mesh, mats),
            ('cockpit', replace(mesh, uvs=()), mats)])
        self.assertIsNotNone(result.textures[0])
        self.assertIsNone(result.textures[1])
        self.assertEqual(result.uv_available, (True, False))
        self.assertEqual(result.diffuse_sources[1][1], 'shared.pak')
        self.assertTrue(any('no decoded UV' in row for row in result.issues))
        self.assertEqual(len(combined.uvs),len(combined.vertices))

    def test_resolved_dds_and_missing_diffuse_have_distinct_sources(self):
        base='characters/hunters/merc_lennox/'
        with TemporaryDirectory() as folder:
            root=Path(folder)
            material=root/'lennox_3p.mtl'
            material.write_text('''
                <Material><SubMaterials>
                <Material Name="torso" Shader="Illum"><Textures>
                <Texture Map="Diffuse" File="'''+base+'''textures/torso_diff.tif"/>
                </Textures></Material>
                <Material Name="head" Shader="Illum"><Textures>
                <Texture Map="Diffuse" File="'''+base+'''textures/head_diff.tif"/>
                </Textures></Material>
                </SubMaterials></Material>''',encoding='utf-8')
            diffuse=root/'torso_diff.dds'
            Image.new('RGB',(8,8),(160,95,75)).save(diffuse,format='DDS')
            def rec(path,relative,archive):
                return {'file':path,'relative':relative,
                        'workspace':root,'archive':archive}
            idx={
              (base+'lennox_3p.mtl').casefold():[
                  rec(material,base+'lennox_3p.mtl','character_data.pak')],
              (base+'textures/torso_diff.dds').casefold():[
                  rec(diffuse,base+'textures/torso_diff.dds','character_textures.pak')],
            }
            resolved=load_preview_materials(
                root,base+'lennox_3p.skinm',index=idx)
            self.assertIsNotNone(resolved.textures[0])
            self.assertIsNone(resolved.textures[1])
            self.assertEqual(resolved.diffuse_sources[0][1],
                             'character_textures.pak')
            self.assertEqual(resolved.diffuse_sources[1],None)
            self.assertEqual(len(resolved.diffuse_references),2)
            self.assertIn('head_diff.tif',resolved.diffuse_references[1])
            self.assertTrue(any('head_diff.dds' in x for x in resolved.issues))


if __name__ == '__main__':
    unittest.main()
