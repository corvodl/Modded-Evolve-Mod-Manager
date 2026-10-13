"""Synthetic Blender glTF armature compatibility checks for assembled CDF skins."""
from __future__ import annotations

import unittest
import numpy as np

from character_assembly import combine_meshes
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_textured_export import _unpack_glb
from model_animation import _accessor
from model_rigging import (
    skin_glb_composite, skin_glb_composite_separate, validate_skin_rest_pose)
from test_model_rigging import crchf, bone_record, tiny_mesh, tiny_skeleton


class BlenderCDFRigTests(unittest.TestCase):
    def setUp(self):
        self.render = tiny_mesh()
        self.mesh = read_preview_mesh(self.render,'body.skinm')
        combined,_ = combine_meshes([('body',self.mesh,None),
                                     ('weapon',self.mesh,None)])
        self.base = glb_bytes(combined)
        self.changed_bind = crchf([(0x08002000,
            b'\x00'*32 + bone_record('root',0) +
            bone_record('child',-1,offset=2))])

    def test_compatible_composite_keeps_shared_armature(self):
        output, report = skin_glb_composite(
            self.base, [(self.render,tiny_skeleton(),4)]*2,
            tiny_skeleton())
        doc,binary = _unpack_glb(output)
        self.assertEqual(len(doc['skins']),1)
        self.assertEqual(report['part_count'],2)
        self.assertGreaterEqual(validate_skin_rest_pose(output),1)
        self.assertIn('JOINTS_0',doc['meshes'][0]['primitives'][0]['attributes'])

    def test_distinct_bind_poses_keep_two_functional_blender_armatures(self):
        parts=[(self.render,tiny_skeleton(),4),
               (self.render,self.changed_bind,4)]
        with self.assertRaisesRegex(ValueError,'conflicting bind poses'):
            skin_glb_composite(self.base,parts,tiny_skeleton())
        output,report=skin_glb_composite_separate(
            self.base,parts,part_names=('body','weapon'))
        doc,binary=_unpack_glb(output)
        self.assertEqual(len(doc['meshes']),2)
        self.assertEqual(len(doc['skins']),2)
        self.assertEqual(report['armatures'],2)
        self.assertEqual(report['part_count'],2)
        self.assertGreaterEqual(validate_skin_rest_pose(output),2)
        self.assertEqual(len(doc.get('animations',())),0)
        self.assertEqual(doc['meshes'][0]['primitives'][0]['material'],0)
        self.assertEqual(doc['meshes'][1]['primitives'][0]['material'],1)
        joint_roots=[doc['nodes'][skin['skeleton']]['name']
                     for skin in doc['skins']]
        self.assertNotEqual(*joint_roots)
        for i,skin in enumerate(doc['skins']):
            mesh_node=next(n for n in doc['nodes'] if
                           n.get('skin') == i)
            primitive=doc['meshes'][mesh_node['mesh']]['primitives'][0]
            attrs=primitive['attributes']
            self.assertIn('WEIGHTS_0',attrs)
            self.assertIn('JOINTS_0',attrs)
            positions=_accessor(doc,binary,attrs['POSITION'])
            self.assertEqual(len(positions),4)
            # Splitting incompatible armatures must preserve normals/tangents
            # from the original combined mesh; normal-mapped GLBs depend on them.
            source_doc, source_binary = _unpack_glb(self.base)
            source_attrs = source_doc['meshes'][0]['primitives'][0]['attributes']
            for semantic in ('NORMAL', 'TANGENT'):
                self.assertIn(semantic, source_attrs)
                self.assertIn(semantic, attrs)
                original = _accessor(source_doc, source_binary, source_attrs[semantic])
                exported = _accessor(doc, binary, attrs[semantic])
                self.assertEqual(len(exported), 4)
                self.assertTrue(np.array_equal(exported, original[i*4:(i+1)*4]))
            self.assertEqual(len(_accessor(doc,binary,primitive['indices'])),12)
            self.assertTrue(np.isclose(
                _accessor(doc,binary,attrs['WEIGHTS_0']).sum(axis=1),1).all())

    def test_rejects_part_size_mismatch(self):
        with self.assertRaisesRegex(ValueError,'vertex count'):
            skin_glb_composite_separate(
                self.base,[(self.render,tiny_skeleton(),3),
                           (self.render,tiny_skeleton(),4)])


if __name__ == '__main__':
    unittest.main()
