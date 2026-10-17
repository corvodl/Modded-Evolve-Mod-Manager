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
        # Kala's cooked mesh has one original UV stream. There must be no
        # automatically unwrapped/repacked painting atlas in this FBX.
        self.assertIn("bpy.ops.mesh.separate(type='MATERIAL')", BLENDER_FBX_SCRIPT)
        self.assertIn("bmesh.ops.weld_verts", BLENDER_FBX_SCRIPT)
        self.assertIn("plan_split_vertex_reconnections(", BLENDER_FBX_SCRIPT)
        self.assertIn("uv0.name = 'UV0_Original'", BLENDER_FBX_SCRIPT)
        self.assertIn("_corner_signature(mesh) != original_fingerprint", BLENDER_FBX_SCRIPT)
        self.assertIn("'uv_maps':uv_maps", BLENDER_FBX_SCRIPT)
        self.assertNotIn("UV1_Texturing", BLENDER_FBX_SCRIPT)
        self.assertNotIn("bpy.ops.uv.smart_project", BLENDER_FBX_SCRIPT)
        self.assertNotIn("bpy.ops.uv.unwrap", BLENDER_FBX_SCRIPT)

    @staticmethod
    def _planner():
        # Run the same pure planning implementation embedded into Blender.
        section = BLENDER_FBX_SCRIPT.split(
            '# UV_TOPOLOGY_PLANNER_START', 1)[1].split(
            '# UV_TOPOLOGY_PLANNER_END', 1)[0]
        namespace = {'math': __import__('math')}
        exec(compile(section, 'uv_topology_planner.py', 'exec'), namespace)
        return namespace['plan_split_vertex_reconnections']

    def test_renderer_split_edge_reconstructs_single_uv_without_repacking(self):
        planner = self._planner()
        # Two triangles touch geometrically but the engine assigned different
        # vertex indices and UV corners at the edge. No UV is changed.
        xyz = [(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),
               (0.,0.,0.),(1.,0.,0.),(1.,1.,0.)]
        uvs = ((0.,0.),(0.5,0.),(0.,1.),
               (1.,0.),(0.5,0.5),(1.,1.))
        faces = [(0,1,2),(3,5,4)]
        weights = [((1,1.0),)]*6
        mapping, diag = planner(xyz,faces,weights=weights,materials=[3,3])
        self.assertEqual(mapping,{3:0,4:1})
        self.assertEqual(diag['safe_vertex_pairs'],2)
        self.assertEqual(uvs[0],(0.,0.))
        self.assertEqual(uvs[3],(1.,0.))
        self.assertEqual(len(uvs),6)

    def test_renderer_split_refuses_weight_mismatch_or_material_boundary(self):
        planner = self._planner()
        xyz = [(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),
               (0.,0.,0.),(1.,1.,0.),(1.,0.,0.)]
        faces = [(0,1,2),(3,4,5)]
        weights = [((1,1.0),)]*3 + [((2,1.0),),((2,1.0),),((2,1.0),)]
        plan, _ = planner(xyz,faces,weights=weights,materials=[3,3])
        self.assertFalse(plan)
        plan, _ = planner(xyz,faces,weights=[((1,1.),)]*6,materials=[3,4])
        self.assertFalse(plan)

    def test_renderer_split_does_not_weld_nonmanifold_edge(self):
        planner = self._planner()
        xyz = [(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),
               (0.,0.,0.),(1.,0.,0.),(1.,1.,0.),
               (0.,0.,0.),(1.,0.,0.),(0.,-1.,0.)]
        faces = [(0,1,2),(3,5,4),(6,7,8)]
        plan, diag = planner(xyz,faces)
        self.assertFalse(plan)
        self.assertGreaterEqual(diag['nonmanifold_edges_skipped'],1)

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
                    'materials':1,'uv_meshes':1,
                    'uv_maps':[{'uv_sets':['UV0_Original'],
                                'source_uv_sets':1,'generated_uv_sets':0,
                                'source_uvs_preserved':True,
                                'weights_preserved':True}]}))
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
                    'actions':['idle','walk'],'materials':2,'uv_meshes':1,
                    'uv_maps':[{'uv_sets':['UV0_Original'],
                                'source_uv_sets':1,'generated_uv_sets':0,
                                'source_uvs_preserved':True,
                                'weights_preserved':True}]}))
                return SimpleNamespace(returncode=0,stderr='',stdout='')
            with patch('fbx_export_converter.subprocess.run',side_effect=run):
                report=convert_glb_to_fbx(
                    sample_glb(rig=True,animated=True),target,blender=exe)
            self.assertEqual(report['armatures'],1)
            self.assertEqual(len(report['actions']),2)

    def test_dropped_original_weight_verification_rejects_export_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'Kala.fbx'
            exe = root / 'blender'
            exe.write_text('mock')
            def run(arguments, **_unused):
                i = arguments.index('--')
                Path(arguments[i+2]).write_bytes(FBX_SIGNATURE + b'body'*44)
                Path(arguments[i+3]).write_text(json.dumps({
                    'meshes': 1, 'armatures': 1, 'actions': ['idle','walk'],
                    'uv_maps': [{'uv_sets':['UV0_Original'],
                                 'source_uv_sets':1,'generated_uv_sets':0,
                                 'source_uvs_preserved':True,
                                 'weights_preserved':False}]}))
                return SimpleNamespace(returncode=0,stderr='',stdout='')
            with patch('fbx_export_converter.subprocess.run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'original UV or skinned topology'):
                    convert_glb_to_fbx(
                        sample_glb(rig=True,animated=True), target, blender=exe)
            self.assertFalse(target.exists())

    def test_missing_original_uv_receipt_rejects_export_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'Kala.fbx'
            exe = root / 'blender'
            exe.write_text('mock')
            def run(arguments, **_unused):
                i = arguments.index('--')
                Path(arguments[i+2]).write_bytes(FBX_SIGNATURE + b'body'*44)
                Path(arguments[i+3]).write_text(json.dumps({
                    'meshes': 1, 'armatures': 0, 'actions': []}))
                return SimpleNamespace(returncode=0,stderr='',stdout='')
            with patch('fbx_export_converter.subprocess.run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'original UV or skinned topology'):
                    convert_glb_to_fbx(sample_glb(), target, static=True,
                                       blender=exe)
            self.assertFalse(target.exists())

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
