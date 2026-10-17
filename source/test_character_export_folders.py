"""Blender character export folders keep real PNGs and verified motion assets.

Synthetic fixtures only: no game clips, runtime activation or invented frames.
"""
import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from blender_texture_folder import write_texture_folder, texture_payloads
from character_animation_catalog import AnimationReference
from character_animation_export import export_character_animation_folder
from material_preview import PreviewMaterials
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _pack_glb, _unpack_glb


def test_glb():
    mesh = SimpleNamespace(
        vertices=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
        triangles=((0,1,2),),uvs=((0,0),(1,0),(0,1)),subsets=())
    mat=PreviewMaterials((Image.new('RGBA',(8,8),(80,120,180,255)),),
                         ('Actor',),'actor.mtl')
    blob,_=embed_diffuse_textures(glb_bytes(mesh),mat)
    return blob


def synthetic_caf():
    # Valid bounded CryEngine header, but no motion/controller data.
    return (b'CrChF\x07\0\0' + struct.pack('<II',1,16)
            + struct.pack('<4I',0x1234,1,0,32))


class CharacterAnimationFolderTests(unittest.TestCase):
    def test_no_rig_does_not_invent_playable_animations(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            data=synthetic_caf()
            file=root/'test_idle.caf'
            file.write_bytes(data)
            ref=AnimationReference(
                'idle','animations/test_idle.caf','caf',True,file)
            result=export_character_animation_folder(
                root,test_glb(),'characters/hunters/merc_test/test.cdf',
                {},root,references=(ref,),convert_database=False)
            self.assertEqual(result['animations'],0)
            self.assertEqual(result['sources'],1)
            output=root/'animations'
            self.assertFalse(list(output.glob('*.glb')))
            self.assertEqual(len(list((output/'sources').glob('*.caf'))),1)
            manifest=json.loads((output/'animation_manifest.json').read_text())
            self.assertFalse(manifest['rigged_single_skin'])
            self.assertIn('no single verified rig',
                          manifest['references'][0]['conversion'])
            self.assertIn('No animation tracks',manifest['note'])

    def test_multiple_decoded_cafs_share_an_animated_rigged_glb(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=root/'idle.caf'; second=root/'walk.caf'
            first.write_bytes(synthetic_caf())
            second.write_bytes(synthetic_caf())
            refs=(
                AnimationReference('idle','animations/actor/idle.caf','caf',True,first),
                AnimationReference('walk','animations/actor/walk.caf','caf',True,second),
            )
            doc,binary=_unpack_glb(test_glb())
            doc['skins']=[{'joints':[0]}]
            source=_pack_glb(doc,binary)
            def validated(blob, tracks):
                modified,data=_unpack_glb(blob)
                modified.setdefault('animations',[]).append({
                    'name':tracks[0][0],'channels':[{
                        'sampler':0,'target':{'node':0,'path':'translation'}}],
                    'samplers':[{'input':0,'output':0}]})
                return _pack_glb(modified,data),{'clips':1,'bone_channels':1}
            with patch('native_caf.append_caf_animations',
                       side_effect=validated) as exporter:
                report=export_character_animation_folder(
                    root,source,'characters/hunters/merc_test/test.cdf',
                    {},root,references=refs,convert_database=False)
            self.assertEqual(report['animations'],2)
            self.assertGreaterEqual(exporter.call_count,4)
            animated=root/'animations'/'test_animations.glb'
            self.assertTrue(animated.is_file())
            result,_=_unpack_glb(animated.read_bytes())
            self.assertEqual([clip['name'] for clip in result['animations']],
                             ['idle','walk'])
            self.assertEqual(result['skins'],[{'joints':[0]}])
            self.assertEqual(len(list((root/'animations'/'sources').glob('*.caf'))),2)

    def test_dba_and_verified_cafs_are_exported_together_without_duplicates(self):
        # The old exporter processed a DBA only when NO CAF had converted.
        # A character with 19 loose CAFs therefore missed its packed DBA
        # Actions, even though both sources were independently verified.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            caf = root / 'tech_idle.caf'
            dba = root / 'tech_3p.dba'
            caf.write_bytes(synthetic_caf())
            dba.write_bytes(b'synthetic CryEngine DBA placeholder')
            refs = (
                AnimationReference('DBA: tech_3p',
                    'characters/hunters/merc_tech/animations/tech_3p.dba',
                    'dba', True, dba),
                AnimationReference('tech_extra',
                    'characters/hunters/merc_tech/animations/tech_extra.caf',
                    'caf', True, caf),
            )
            doc, binary = _unpack_glb(test_glb())
            doc['skins'] = [{'joints': [0]}]
            original = _pack_glb(doc, binary)
            character = SimpleNamespace(
                skeleton='characters/hunters/merc_tech/tech_3p_skeleton.chr',
                rig_parts=(('tech_3p.skinm',None,None,None),))
            def make_dba(blob, *unused, **kwargs):
                document, payload = _unpack_glb(blob)
                document['animations'] = [
                    {'name':'tech_idle','channels':[{
                        'sampler':0,'target':{'node':0,'path':'translation'}}],
                     'samplers':[{'input':0,'output':0}]},
                    {'name':'tech_move','channels':[{
                        'sampler':0,'target':{'node':0,'path':'translation'}}],
                     'samplers':[{'input':0,'output':0}]}]
                return _pack_glb(document, payload)
            def valid_caf(blob, sources):
                document, payload = _unpack_glb(blob)
                document.setdefault('animations', []).append({
                    'name':sources[0][0],
                    'channels':[{'sampler':0,
                                 'target':{'node':0,'path':'translation'}}],
                    'samplers':[{'input':0,'output':0}]})
                return _pack_glb(document, payload), {
                    'clips':1,'bone_channels':1}
            def classify(ref):
                return ('converter','DBA converter available') if ref.kind=='dba' else (
                    'candidate','Verified standalone CAF')
            with patch('character_animation_catalog.native_clip_playability',
                       side_effect=classify), patch(
                       'character_animation_catalog.caf_header',
                       return_value=True), patch(
                       'database_animation.convert_database_motion',
                       side_effect=make_dba) as convert, patch(
                       'native_caf.append_caf_animations',
                       side_effect=valid_caf):
                report=export_character_animation_folder(
                    root, original,
                    'characters/hunters/merc_tech/tech_3p.cdf',
                    {}, root, character=character, references=refs,
                    copy_sources=False)
            self.assertEqual(report['animations'],3)
            convert.assert_called_once()
            result,_=_unpack_glb(
                (root/'animations'/'tech_3p_animations.glb').read_bytes())
            self.assertEqual([a['name'] for a in result['animations']],
                             ['tech_idle','tech_move','tech_extra'])
            manifest=json.loads(
                (root/'animations'/'animation_manifest.json').read_text())
            self.assertEqual(manifest['converted_clips'],
                             ['tech_idle','tech_move','tech_extra'])
            self.assertIn('2 verified DBA',
                          manifest['references'][0]['conversion'])
            self.assertIn('Exported',manifest['references'][1]['conversion'])

    def test_matching_caf_name_is_not_duplicated_over_dba_action(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            caf=root/'tech_idle.caf'; dba=root/'tech_3p.dba'
            caf.write_bytes(synthetic_caf()); dba.write_bytes(b'converted DBA')
            refs=(AnimationReference('DBA: tech_3p','anim/tech_3p.dba','dba',True,dba),
                  AnimationReference('tech_idle','anim/tech_idle.caf','caf',True,caf))
            doc,payload=_unpack_glb(test_glb())
            doc['skins']=[{'joints':[0]}]
            source=_pack_glb(doc,payload)
            def converted(blob,*args,**kwargs):
                data,binary=_unpack_glb(blob)
                data['animations']=[{'name':'tech_idle',
                    'channels':[{'sampler':0,'target':{'node':0,'path':'translation'}}],
                    'samplers':[{'input':0,'output':0}]}]
                return _pack_glb(data,binary)
            character=SimpleNamespace(skeleton='tech_skeleton.chr',
                 rig_parts=(('tech.skinm',None,None,None),))
            def verify_caf(blob, sources):
                return blob, {'clips':1, 'bone_channels':1}
            with patch('character_animation_catalog.native_clip_playability',
                       side_effect=lambda ref: ('converter','ok') if ref.kind=='dba'
                                                  else ('candidate','ok')), patch(
                       'character_animation_catalog.caf_header',return_value=True), patch(
                       'database_animation.convert_database_motion',
                       side_effect=converted), patch(
                       'native_caf.append_caf_animations',
                       side_effect=verify_caf) as append:
                output=export_character_animation_folder(
                    root,source,'tech.cdf',{},root,character=character,
                    references=refs,copy_sources=False)
            self.assertEqual(output['animations'],1)
            append.assert_called_once()  # validation, not a duplicate append
            manifest=json.loads(
                (root/'animations'/'animation_manifest.json').read_text())
            self.assertIn('not duplicated',
                          manifest['references'][1]['conversion'])

    def test_more_than_old_sixteen_clip_limit_can_be_embedded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            refs = []
            for index in range(18):
                path = root / ('motion_%02d.caf' % index)
                path.write_bytes(synthetic_caf())
                refs.append(AnimationReference(
                    'motion_%02d' % index,
                    'animations/actor/motion_%02d.caf' % index,
                    'caf', True, path))
            doc, binary = _unpack_glb(test_glb())
            doc['skins'] = [{'joints': [0]}]
            source = _pack_glb(doc, binary)
            def validated(blob, tracks):
                modified, data = _unpack_glb(blob)
                modified.setdefault('animations', []).append({
                    'name': tracks[0][0],
                    'channels': [{'sampler': 0,
                                  'target': {'node': 0, 'path': 'rotation'}}],
                    'samplers': [{'input': 0, 'output': 0}]})
                return _pack_glb(modified, data), {
                    'clips': 1, 'bone_channels': 1}
            with patch('native_caf.append_caf_animations',
                       side_effect=validated):
                report = export_character_animation_folder(
                    root, source, 'characters/monsters/actor/actor.cdf',
                    {}, root, references=tuple(refs),
                    convert_database=False, copy_sources=False)
            self.assertEqual(report['animations'], 18)
            self.assertEqual(report['sources'], 0)
            output = root / 'animations' / 'actor_animations.glb'
            clips, _ = _unpack_glb(output.read_bytes())
            self.assertEqual(len(clips['animations']), 18)

    def test_oversized_catalog_exports_valid_subset_with_manifest_note(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            caf = root / 'idle.caf'
            caf.write_bytes(synthetic_caf())
            records = tuple(AnimationReference(
                'graph_%04d' % i, 'animations/graph_%04d.bspace' % i,
                'bspace', False) for i in range(620))
            records += (AnimationReference(
                'actor_idle', 'animations/actor/idle.caf',
                'caf', True, caf),)
            result = export_character_animation_folder(
                root, test_glb(), 'characters/hunters/merc_actor/actor_3p.cdf',
                {}, root, references=records, copy_sources=False)
            report = json.loads(
                (root / 'animations' / 'animation_manifest.json').read_text())
            self.assertEqual(result['animations'], 0)  # No rig: no invented motion.
            self.assertEqual(report['catalog_total'], 621)
            self.assertEqual(report['catalog_processed'], 512)
            self.assertEqual(report['catalog_omitted'], 109)
            self.assertTrue(any(row['name'] == 'actor_idle'
                                for row in report['references']))
            self.assertIn('109 omitted', report['catalog_note'])

    def test_oversized_catalog_keeps_exact_preferred_3p_database(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / 'actor_3p.dba'
            database.write_bytes(b'x' * 32)
            records = tuple(AnimationReference(
                'graph_%04d' % i, 'animations/graph_%04d.bspace' % i,
                'bspace', False) for i in range(600))
            records += (AnimationReference(
                'DBA: actor_3p',
                'characters/hunters/merc_actor/actor_3p.dba',
                'dba', True, database),)
            from character_animation_export import _bounded_catalog_for_export
            selected, omitted = _bounded_catalog_for_export(
                records, 'characters/hunters/merc_actor/actor_3p_skeleton.chr')
            self.assertEqual(omitted, 89)
            self.assertEqual(len(selected), 512)
            self.assertEqual(selected[0].virtual_path,
                             'characters/hunters/merc_actor/actor_3p.dba')

    def test_original_animation_directory_is_kept_without_overwriting(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/'animations'
            folder.mkdir()
            original=folder/'original.caf'
            original.write_bytes(b'Original asset')
            report=export_character_animation_folder(
                root,test_glb(),'characters/hunters/merc_test/test.cdf',
                {},root,references=(),convert_database=False)
            self.assertEqual(report['animations'],0)
            self.assertEqual(original.read_bytes(),b'Original asset')
            with self.assertRaises(FileExistsError):
                export_character_animation_folder(
                    root,test_glb(),'characters/hunters/merc_test/test.cdf',
                    {},root,references=(),convert_database=False)

    def test_nested_cdf_pngs_share_root_textures_without_overwrite(self):
        payload=test_glb()
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=root/'first.glb'
            nested=root/'Assemblies'/'first.glb'
            nested.parent.mkdir()
            results=(
                write_texture_folder(payload,first,
                    texture_root=root/'textures',model_namespace='first'),
                write_texture_folder(payload,nested,
                    texture_root=root/'textures',model_namespace='Assemblies__first'))
            self.assertEqual(len(results[0]),1)
            self.assertEqual(len(results[1]),1)
            self.assertNotEqual(results[0][0].name,results[1][0].name)
            self.assertEqual(results[0][0].read_bytes(),texture_payloads(payload)[0][2])
            self.assertEqual(results[1][0].read_bytes(),texture_payloads(payload)[0][2])
            self.assertFalse((nested.parent/'textures').exists())


if __name__ == '__main__':
    unittest.main()
