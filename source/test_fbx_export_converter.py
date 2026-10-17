"""Offline FBX conversion safety tests; no Blender install needed."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fbx_export_converter import (
    FBX_SIGNATURE, BLENDER_FBX_SCRIPT, convert_glb_to_fbx)
from model_blender_export import glb_bytes
from model_textured_export import _unpack_glb, _pack_glb


def sample_glb(*, rig=False, animated=False):
    mesh = SimpleNamespace(vertices=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
        triangles=((0,1,2),), uvs=((0.,0.),(1.,0.),(0.,1.)),
        subsets=())
    doc, raw = _unpack_glb(glb_bytes(mesh))
    if rig:
        doc['skins'] = [{'joints':[0]}]
    if animated:
        doc['animations'] = [{'name':'idle'}, {'name':'walk'}]
    return _pack_glb(doc, raw)


class FbxConverterTests(unittest.TestCase):
    def test_blender_script_parses(self):
        compile(BLENDER_FBX_SCRIPT, 'blender_fbx_converter.py', 'exec')
        self.assertIn('bake_anim_use_all_actions=True', BLENDER_FBX_SCRIPT)
        self.assertIn('obj.matrix_world = world', BLENDER_FBX_SCRIPT)

    def test_valid_static_fbx_uses_real_binary_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            destination=root/'Kala_3p.fbx'
            executable=root/'blender'
            executable.write_text('mock', encoding='utf-8')
            def run(arguments, **_unused):
                i=arguments.index('--')
                Path(arguments[i+2]).write_bytes(FBX_SIGNATURE + b'valid'*32)
                Path(arguments[i+3]).write_text(json.dumps({
                    'meshes':1,'armatures':0,'actions':[],
                    'materials':1,'uv_meshes':1}))
                return SimpleNamespace(returncode=0,stderr='',stdout='')
            with patch('fbx_export_converter.subprocess.run',side_effect=run):
                result=convert_glb_to_fbx(
                    sample_glb(), destination, static=True, blender=executable)
            self.assertEqual(result['profile'],'static')
            self.assertTrue(destination.read_bytes().startswith(FBX_SIGNATURE))
            self.assertFalse(list(root.glob('*.glb')))
            with self.assertRaises(FileExistsError):
                convert_glb_to_fbx(sample_glb(), destination, blender=executable)

    def test_animated_character_retains_rig_and_all_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            target=root/'Emet.fbx'
            exe=root/'blender'
            exe.write_text('mock')
            def run(arguments, **_unused):
                i=arguments.index('--')
                self.assertEqual(arguments[i+4], 'character')
                Path(arguments[i+2]).write_bytes(FBX_SIGNATURE + b'body'*44)
                Path(arguments[i+3]).write_text(json.dumps({
                    'meshes':1,'armatures':1,
                    'actions':['idle','walk'],'materials':2,'uv_meshes':1}))
                return SimpleNamespace(returncode=0,stderr='',stdout='')
            with patch('fbx_export_converter.subprocess.run',side_effect=run):
                report=convert_glb_to_fbx(
                    sample_glb(rig=True,animated=True),target,blender=exe)
            self.assertEqual(report['armatures'],1)
            self.assertEqual(len(report['actions']),2)

    def test_missing_blender_does_not_write_misnamed_glb(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'test.fbx'
            with patch('fbx_export_converter.shutil.which',return_value=None):
                with self.assertRaisesRegex(FileNotFoundError,'Blender 4.2'):
                    convert_glb_to_fbx(sample_glb(),out)
            self.assertFalse(out.exists())

    def test_missing_fbx_or_actions_fails_without_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            target=root/'Goliath.fbx'
            exe=root/'blender'
            exe.write_text('mock')
            def run(arguments, **_unused):
                i=arguments.index('--')
                Path(arguments[i+2]).write_bytes(FBX_SIGNATURE + b'body'*44)
                Path(arguments[i+3]).write_text(json.dumps({
                    'meshes':1,'armatures':1,'actions':[]}))
                return SimpleNamespace(returncode=0,stderr='',stdout='')
            with patch('fbx_export_converter.subprocess.run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'dropped verified'):
                    convert_glb_to_fbx(
                        sample_glb(rig=True,animated=True),target,blender=exe)
            self.assertFalse(target.exists())

    def test_unrigged_animation_must_not_be_exported(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'skinned'):
                convert_glb_to_fbx(sample_glb(animated=True),Path(temp)/'bad.fbx')


if __name__=='__main__':
    unittest.main()
