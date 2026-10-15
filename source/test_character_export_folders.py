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
