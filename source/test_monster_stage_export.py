"""Stage-specific monster root models: path safety, skins and animation parity."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from model_blender_export import glb_bytes
from model_textured_export import _pack_glb, _unpack_glb
from monster_stage_export import (
    is_monster_character, select_monster_stage_models, stage_from_cdf)


MONSTER = 'characters/monsters/goliath'


def animated_stage(stage, *, animations=2):
    """Standalone synthetic three-vertex GLB with per-stage action names."""
    mesh = SimpleNamespace(vertices=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
        triangles=((0,1,2),), uvs=((0,0),(1,0),(0,1)), subsets=())
    doc, data = _unpack_glb(glb_bytes(mesh))
    names = tuple('stage%d_clip%d' % (stage,n) for n in range(animations))
    doc['skins'] = [{'joints':[0]}]
    doc['animations'] = [{'name': name, 'channels':[], 'samplers':[]} for name in names]
    blob = _pack_glb(doc, data)
    return blob, {
        'cdf': '%s/goliath_stage%d.cdf' % (MONSTER,stage),
        'bone_count':32, 'part_count':3,
        'embedded_animations':len(names),
        'embedded_animation_clips':names,
        'rig_status':'Rigged stage %d' % stage,
        'animation_note':'Verified exact stage rig',
    }


def stage_assemblies():
    return {'Assemblies/goliath_stage%d.glb' % stage:animated_stage(stage)
            for stage in (1,2,3)}


class MonsterStageSelectionTests(TestCase):
    def test_monster_paths_only(self):
        self.assertTrue(is_monster_character(MONSTER))
        self.assertFalse(is_monster_character('characters/hunters/merc_emet'))
        self.assertFalse(is_monster_character('characters/creatures/bird'))
        self.assertIsNone(stage_from_cdf(MONSTER+'/goliath_3p.cdf', MONSTER))
        self.assertEqual(stage_from_cdf(MONSTER+'/stage_2/goliath.cdf',MONSTER),2)
        self.assertEqual(stage_from_cdf(MONSTER+'/goliath_stage3.cdf',MONSTER),3)
        self.assertIsNone(stage_from_cdf(MONSTER+'/goliath_stage1_stage3.cdf',MONSTER))
        self.assertIsNone(stage_from_cdf('characters/monsters/other/goliath_stage1.cdf',MONSTER))

    def test_retail_krakenelder_numbered_cdfs_are_explicit_stages(self):
        elder = 'characters/monsters/krakenelder'
        for stage in (1, 2, 3):
            cdf = '%s/krakenelder%d.cdf' % (elder, stage)
            self.assertEqual(stage_from_cdf(cdf, elder), stage)
            self.assertEqual(stage_from_cdf(cdf.upper(), elder.upper()), stage)
            self.assertIsNone(stage_from_cdf(cdf, MONSTER))
        # These do not name distinct evolution stages, even if numbered.
        for name in ('krakenelder_3p.cdf', 'krakenelder3p.cdf',
                     'krakenelder01.cdf', 'krakenelder11.cdf',
                     'krakenelder1_lod.cdf', 'krakenelder1_weapon.cdf',
                     'equipment1.cdf', 'lod1.cdf', 'krakenelder4.cdf'):
            self.assertIsNone(stage_from_cdf(elder + '/' + name, elder), name)
        self.assertIsNone(stage_from_cdf(
            elder + '/gear/krakenelder1.cdf', elder))
        self.assertIsNone(stage_from_cdf(
            elder + '/stage2/krakenelder1.cdf', elder))
        self.assertEqual(stage_from_cdf(
            elder + '/stage2/krakenelder.cdf', elder), 2)

    def test_numbered_cdf_explicit_skeleton_and_skin_bindings(self):
        # Reduced source-derived fixtures from supplied krakenelder1/2/3
        # definitions. Distinct skeletons and .skin body assets are required;
        # the parser must preserve both CA_SKIN_UNIQUE and CA_SKIN.
        from character_assembly import _parse_cdf
        for stage in (1, 2, 3):
            kind = 'CA_SKIN' if stage == 3 else 'CA_SKIN_UNIQUE'
            prefix = 'characters/monsters/krakenelder/krakenelder%d' % stage
            xml = ('<CharacterDefinition><Model File="%s_skeleton.chr"/>'
                   '<AttachmentList><Attachment Type="%s" AName="body"'
                   ' Binding="%s.skin"/></AttachmentList>'
                   '</CharacterDefinition>') % (prefix, kind, prefix)
            skeleton, attachments, _ = _parse_cdf(xml.encode())
            self.assertEqual(skeleton, prefix + '_skeleton.chr')
            self.assertEqual(attachments, (('body', prefix + '.skin'),))

    def test_numbered_cdf_assemblies_use_three_different_verified_glbs(self):
        elder = 'characters/monsters/krakenelder'
        assemblies = {}
        for stage in (1, 2, 3):
            blob, original = animated_stage(stage)
            info = dict(original, cdf=elder + '/krakenelder%d.cdf' % stage)
            assemblies['Assemblies/krakenelder%d.glb' % stage] = (blob, info)
        stages, selected = select_monster_stage_models(assemblies, elder)
        self.assertEqual(set(stages), {1, 2, 3})
        self.assertEqual(selected, frozenset(assemblies))
        self.assertEqual(
            [stages[n][1]['cdf'] for n in (1,2,3)],
            [elder + '/krakenelder%d.cdf' % n for n in (1,2,3)])
        # No replacement or synthetic Stage 3 when its GLB is not verified.
        assemblies.pop('Assemblies/krakenelder3.glb')
        with self.assertRaisesRegex(ValueError, r'missing stage\(s\): 3'):
            select_monster_stage_models(assemblies, elder)

    def test_three_distinct_exact_cdf_sources_selected(self):
        assemblies = stage_assemblies()
        models, picked = select_monster_stage_models(assemblies, MONSTER)
        self.assertEqual(set(models),{1,2,3})
        self.assertEqual(len(picked),3)
        for stage in models:
            self.assertEqual(models[stage][1]['cdf'],
                             MONSTER+'/goliath_stage%d.cdf' % stage)

    def test_missing_stage_rejected_instead_of_duplicating_stage_one(self):
        assemblies = stage_assemblies()
        del assemblies['Assemblies/goliath_stage2.glb']
        with self.assertRaisesRegex(ValueError, 'missing stage\\(s\\): 2'):
            select_monster_stage_models(assemblies, MONSTER)

    def test_equal_rank_same_stage_is_not_guessed(self):
        assemblies=stage_assemblies()
        first_blob, first_info=assemblies['Assemblies/goliath_stage1.glb']
        assemblies['Assemblies/gear/goliath_stage1.glb'] = (
            first_blob,dict(first_info,cdf=MONSTER+'/gear/goliath_stage1.cdf'))
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            select_monster_stage_models(assemblies, MONSTER)


class MonsterRootExportTests(TestCase):
    def setUp(self):
        temporary=TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        self.home=root/'workspace'
        folder=self.home/'files'/MONSTER
        folder.mkdir(parents=True)
        (folder/'readme.txt').write_text('original')
        (self.home/'.evolve-pak-workspace.json').write_text('{}')
        self.output=root/'export'
        self.output.mkdir()

    def test_command_exports_three_root_models_not_generic_name(self):
        from character_export_command import export_character_directory
        from character_asset_export import export_character_asset_folder
        expected=stage_assemblies()
        def skip_reconversion(*args, **kwargs):
            return {'animations':0, 'sources':0,
                    'manifest':'animations/animation_manifest.json'}
        with (patch('character_export_command.build_cdf_folder_glbs',
                    return_value=(expected,())),
              patch('character_animation_export.export_character_animation_folder',
                    side_effect=skip_reconversion)):
            result=export_character_directory(
                self.home/'files'/MONSTER,self.output,index={})
        folder=Path(result['folder'])
        self.assertFalse((folder/'Goliath.glb').exists())
        for stage in (1,2,3):
            name='Goliath_Stage%d.glb' % stage
            blob=(folder/name).read_bytes()
            doc,_=_unpack_glb(blob)
            actual=tuple(clip['name'] for clip in doc.get('animations',()))
            self.assertEqual(actual,expected['Assemblies/goliath_stage%d.glb'%stage][1]
                             ['embedded_animation_clips'])
            self.assertEqual(len(doc['skins']),1)
            self.assertFalse((folder/'models'/name).exists())
        manifest=json.loads((folder/'asset_sources.json').read_text())
        self.assertEqual([row['stage'] for row in manifest['monster_stages']],[1,2,3])
        self.assertEqual([row['embedded_animations'] for row in manifest['monster_stages']],
                         [2,2,2])
        self.assertIsNone(manifest['full_model'])
        self.assertEqual(len(result['monster_stages']),3)

    def test_krakenelder_root_export_accepts_verified_numbered_cdfs(self):
        from character_export_command import export_character_directory
        elder = 'characters/monsters/krakenelder'
        elder_source = self.home/'files'/elder
        elder_source.mkdir(parents=True)
        (elder_source/'readme.txt').write_text('original')
        assemblies = {}
        for stage in (1, 2, 3):
            blob, original = animated_stage(stage)
            info = dict(original,cdf=elder+'/krakenelder%d.cdf'%stage)
            assemblies['Assemblies/krakenelder%d.glb'%stage]=(blob, info)
        with (patch('character_export_command.build_cdf_folder_glbs',
                    return_value=(assemblies, ())),
              patch('character_animation_export.export_character_animation_folder',
                    return_value={'animations':0,'sources':0,
                                  'manifest':'animations/animation_manifest.json'})):
            result = export_character_directory(
                self.home/'files'/elder,self.output,index={})
        folder = Path(result['folder'])
        self.assertEqual(len(result['monster_stages']), 3)
        self.assertEqual(len([p for p in folder.glob('*_Stage*.glb')]), 3)
        self.assertEqual(
            {row['cdf'] for row in result['monster_stages']},
            {elder+'/krakenelder%d.cdf'%n for n in (1,2,3)})

    def test_actual_missing_monster_dependency_is_reported(self):
        from character_export_command import export_character_directory
        elder = 'characters/monsters/krakenelder'
        (self.home/'files'/elder).mkdir(parents=True)
        errors = (
            (elder+'/krakenelder1.cdf','exact krakenelder1_skeleton.chr unavailable'),
            (elder+'/krakenelder2.cdf','krakenelder2.skin was ambiguous'),
        )
        with patch('character_export_command.build_cdf_folder_glbs',
                   return_value=({}, errors)):
            with self.assertRaisesRegex(
                    ValueError, 'krakenelder1_skeleton.chr unavailable') as raised:
                export_character_directory(
                    self.home/'files'/elder,self.output,index={})
        self.assertIn('missing stage(s): 1, 2, 3',str(raised.exception))
        self.assertIn('krakenelder2.skin was ambiguous',str(raised.exception))
        # An aborted export cannot create a misleading partial "Complete"
        # folder or modify source files.
        self.assertEqual(list(self.output.iterdir()), [])

    def test_stage_animation_mismatch_refused_before_writing(self):
        from character_asset_export import export_character_asset_folder
        models={stage:animated_stage(stage) for stage in (1,2,3)}
        blob, report=models[2]
        models[2]=(blob,dict(report,embedded_animation_clips=('wrong_stage_idle',)))
        with self.assertRaisesRegex(ValueError, 'mismatched CDF animations'):
            export_character_asset_folder(
                self.home, MONSTER,self.output,stage_models=models,
                output_name='Goliath',index={})
        self.assertEqual(list(self.output.iterdir()),[])

    def test_missing_stage_refused_before_writing(self):
        from character_asset_export import export_character_asset_folder
        models={stage:animated_stage(stage) for stage in (1,2)}
        with self.assertRaisesRegex(ValueError, 'three separately verified'):
            export_character_asset_folder(
                self.home, MONSTER,self.output,stage_models=models,
                output_name='Goliath',index={})
        self.assertEqual(list(self.output.iterdir()),[])

    def test_hunter_cannot_inherit_monster_stage_layout(self):
        from character_asset_export import export_character_asset_folder
        folder=self.home/'files'/'characters/hunters/merc_emet'
        folder.mkdir(parents=True)
        (folder/'model.skin').write_bytes(b'original')
        with self.assertRaisesRegex(ValueError,'Only monsters'):
            export_character_asset_folder(
                self.home,'characters/hunters/merc_emet',self.output,
                stage_models={stage:animated_stage(stage) for stage in (1,2,3)},
                output_name='Emet',index={})



class StageAssemblyTextureTests(TestCase):
    def test_combining_monster_parts_preserves_emissive_masks(self):
        from PIL import Image
        from character_assembly import combine_meshes
        from model_preview import PreviewMesh
        from material_preview import PreviewMaterials
        diffuse=Image.new('RGBA',(4,4),(25,90,180,255))
        glow=Image.new('RGB',(4,4),(20,150,255))
        mesh=PreviewMesh(
            ((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)), ((0,1,2),),
            (0.5,0.5,0.), 1., 'monster part',
            ((0.,0.),(1.,0.),(0.,1.)), ((0,3,0),))
        material=PreviewMaterials((diffuse,), ('glow tubes',), 'goliath.mtl',
            emissive_maps=(glow,),emissive_strengths=(2.5,),
            emissive_sources=(('tubes_glow.dds','monster_shared.pak'),))
        combined, mats=combine_meshes([
            ('body',mesh,material),('arm',mesh,material)])
        self.assertEqual(len(mats.emissive_maps),2)
        self.assertEqual(mats.emissive_strengths,(2.5,2.5))
        self.assertEqual(mats.emissive_sources[0],
                         ('tubes_glow.dds','monster_shared.pak'))
        output,_=__import__('model_textured_export').embed_diffuse_textures(
            glb_bytes(combined),mats)
        doc,_=_unpack_glb(output)
        self.assertEqual(len(doc['materials']),2)
        self.assertTrue(all('emissiveTexture' in item
                            for item in doc['materials']))
        self.assertEqual(
            doc['materials'][1]['extensions']['KHR_materials_emissive_strength']
            ['emissiveStrength'],100.)
        self.assertEqual(
            doc['materials'][1]['extras']['cryEngineAuthoredEmissiveStrength'],
            2.5)


if __name__ == '__main__':
    import unittest
    unittest.main()
