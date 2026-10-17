"""Character material handoff: independent glow resolution and exact CDF slot identity.

All fixtures are synthetic. Never depends on an installed Evolve PAK or GPU.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PIL import Image
from character_assembly import combine_meshes
from material_preview import PreviewMaterials, load_preview_materials
from model_preview import PreviewMesh


class AssemblyEmissivePipelineTests(TestCase):
    def test_decal_only_material_loads_without_diffuse(self):
        """A verified emissive-only material must not fail diffuse preflight."""
        prefix='characters/hunters/merc_example/'
        with TemporaryDirectory() as tmp:
            workspace=Path(tmp)
            mtl=workspace/'material.xml'
            dds=workspace/'glow.dds'
            Image.new('RGB',(8,8),(55,120,190)).save(dds,format='DDS')
            mtl.write_text(
                '<Material><SubMaterials><Material Name="Glow" Shader="Illum" '
                'GlowAmount="3" Emissive="0,0,0" Opacity="1">'
                '<Textures><Texture Map="Decal" File="textures/particle/light/glow.tif"/>'
                '</Textures></Material></SubMaterials></Material>')
            material_path=prefix+'model.mtl'
            glow_path='textures/particle/light/glow.dds'
            index={
                material_path:[{'file':mtl,'relative':material_path,
                                'workspace':workspace,'archive':'data.pak'}],
                glow_path:[{'file':dds,'relative':glow_path,
                            'workspace':workspace,'archive':'textures.pak'}],
            }
            loaded=load_preview_materials(
                workspace,prefix+'model.skinm',index=index)
            self.assertIsNone(loaded.textures[0])
            self.assertIsNotNone(loaded.emissive_maps[0])
            self.assertEqual(loaded.emissive_maps[0].getpixel((0,0)),(55,120,190))
            self.assertEqual(loaded.emissive_strengths,(3.,))
            self.assertEqual(loaded.original_slot_ids,(0,))
            self.assertEqual(loaded.original_materials,(material_path,))
            self.assertEqual(loaded.emissive_references,
                             ('textures/particle/light/glow.tif',))
            self.assertIn('decoded emission',loaded.emissive_status[0])

    def test_without_diffuse_or_emission_still_fails_clearly(self):
        prefix='characters/creatures/shared/'
        with TemporaryDirectory() as tmp:
            workspace=Path(tmp)
            mtl=workspace/'material.xml'
            mtl.write_text('<Material Shader="Illum" Name="Untextured"/>')
            mtl_path=prefix+'material.mtl'
            index={mtl_path:[{'file':mtl,'relative':mtl_path,
                              'workspace':workspace}]}
            with self.assertRaisesRegex(ValueError,'No diffuse or emissive DDS'):
                load_preview_materials(workspace,prefix+'material.skinm',index=index)

    def test_kala_like_original_slot_eleven_is_composite_slot_seven(self):
        """No off-by-one or implicit diffuse lookup during CDF compaction."""
        slots=tuple(range(7))+(11,)
        vertices=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.))
        mesh=PreviewMesh(vertices,((0,1,2),)*len(slots),
            (.5,.5,0.),1.,'source body',
            ((0.,0.),(1.,0.),(0.,1.)),
            tuple((i*3,3,slot) for i,slot in enumerate(slots)))
        glow=Image.new('RGB',(4,4),(36,95,180))
        src='characters/hunters/merc_tech/tech_3p.mtl'
        mats=PreviewMaterials(
            (None,)*12,tuple('Glow' if i==11 else 'NoDraw' for i in range(12)),
            src,hidden=tuple(i!=11 for i in range(12)),
            emissive_maps=tuple(glow if i==11 else None for i in range(12)),
            emissive_strengths=tuple(3. if i==11 else 0. for i in range(12)),
            emissive_references=tuple(
                'textures/particle/light/chroma_fan.tif' if i==11 else ''
                for i in range(12)),
            emissive_status=tuple('decoded emission: chroma_fan' if i==11
                                  else 'NoDraw' for i in range(12)),
            original_slot_ids=tuple(range(12)),
            original_materials=(src,)*12)
        # Like a Kala arm DDNA alpha sidecar, gloss must follow the
        # exact MTL leaf 11 rather than its composite slot number 7.
        gloss = Image.new('L', (4,4), 93)
        from dataclasses import replace
        mats = replace(mats, gloss_maps=tuple(
            gloss if i == 11 else None for i in range(12)))
        combined, materials=combine_meshes([('body',mesh,mats)])
        self.assertIs(materials.gloss_maps[7], gloss)
        self.assertIsNone(materials.gloss_maps[6])
        self.assertEqual([entry[2] for entry in combined.subsets],list(range(8)))
        self.assertEqual(materials.names[7], 'body: Glow')
        self.assertEqual(materials.original_slot_ids[7],11)
        self.assertEqual(materials.original_materials[7],src)
        self.assertIs(materials.emissive_maps[7],glow)
        self.assertEqual(materials.emissive_strengths[7],3.)
        self.assertEqual(materials.emissive_references[7],
                         'textures/particle/light/chroma_fan.tif')
        self.assertIn('decoded emission', materials.emissive_status[7])
        self.assertIsNone(materials.emissive_maps[6])

    def test_multipart_assemblies_keep_owning_materials_separate(self):
        """Applies to hunter, monster and wildlife assembly parts identically."""
        vertices=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.))
        mesh=PreviewMesh(vertices,((0,1,2),),(.5,.5,0.),1.,
            'rigged mesh',((0.,0.),(1.,0.),(0.,1.)),((0,3,2),))
        colors=('purple','red','blue')
        for family, color in zip(('hunters','monsters','creatures'),colors):
            with self.subTest(family=family):
                source='characters/%s/example/body.mtl' % family
                glow=Image.new('RGB',(3,3),color)
                mat=PreviewMaterials((None,None,None),('none','none','Glow'),source,
                    emissive_maps=(None,None,glow),
                    emissive_strengths=(0.,0.,2.5),
                    original_slot_ids=(0,1,2),
                    original_materials=(source,)*3,
                    emissive_references=('','','glow_source.tif'),
                    emissive_status=('no emission','no emission','decoded emission'))
                combined,out=combine_meshes([('body',mesh,mat),
                                            ('attachment',mesh,mat)])
                self.assertEqual(tuple(slot for _,_,slot in combined.subsets),(0,1))
                self.assertEqual(out.original_slot_ids,(2,2))
                self.assertEqual(out.original_materials,(source,source))
                self.assertEqual(out.emissive_strengths,(2.5,2.5))
                self.assertIs(out.emissive_maps[0],glow)
                self.assertIs(out.emissive_maps[1],glow)

    def test_stale_editor_index_refreshes_exact_actor_and_global_glow_assets(self):
        from async_model_resolve import refresh_character_material_index
        actor='characters/hunters/merc_tech/'
        mtl=actor+'tech_3p.mtl'
        decal='textures/particle/light/chroma_fan.dds.0'
        other='characters/hunters/merc_lennox/wrong.dds.0'
        local={'file':'project','relative':mtl,'workspace':'project'}
        record=lambda name:{'file':name,'relative':name,'workspace':None}
        original={mtl:[local]}
        live={
            decal:[record(decal)],
            other:[record(other)],
            'characters/hunters/shared/textures/eye.dds.0':
                [record('characters/hunters/shared/textures/eye.dds.0')],
        }
        with patch('asset_library.extracted_index',return_value=live):
            snapshot=refresh_character_material_index(
                original,actor+'tech_3p.skinm')
        self.assertIn(decal,snapshot)
        self.assertIn('characters/hunters/shared/textures/eye.dds.0',snapshot)
        self.assertNotIn(other,snapshot)
        self.assertEqual(snapshot[mtl],[local])
        self.assertEqual(original,{mtl:[local]})  # source index unchanged

    def test_gpu_diagnostic_identifies_original_slot_and_resolution_failure(self):
        from qt_gpu_viewer import QtGPUModelPanel
        src='characters/hunters/merc_tech/tech_3p.mtl'
        materials=PreviewMaterials((None,)*8,
            ('none',)*7+('body: Glow',),'CDF character assembly',
            emissive_maps=(None,)*8,emissive_strengths=(0.,)*8,
            original_slot_ids=tuple(range(7))+(11,),
            original_materials=(src,)*8,
            emissive_references=('',)*7+('textures/particle/light/chroma_fan.tif',),
            emissive_status=('no glow',)*7+('Emissive DDS missing: chroma_fan',))
        panel=SimpleNamespace(
            canvas=SimpleNamespace(materials=materials,
                mesh=SimpleNamespace(subsets=((0,3,7),)),
                _pending_texture_uploads=(),_renderer3=object(),
                gl_error=None,bloom_error=None,emission_enabled=True),
            part_select=SimpleNamespace(currentData=lambda:7),
            emissive_source_info=SimpleNamespace(setText=Mock()))
        QtGPUModelPanel._update_emissive_source_status(panel)
        diagnostic=panel.emissive_source_info.setText.call_args.args[0]
        self.assertIn('Assembly slot 7',diagnostic)
        self.assertIn('MTL leaf 11',diagnostic)
        self.assertIn(src,diagnostic)
        self.assertIn('chroma_fan.tif',diagnostic)
        self.assertIn('Emissive DDS missing',diagnostic)


if __name__=='__main__':
    import unittest
    unittest.main()
