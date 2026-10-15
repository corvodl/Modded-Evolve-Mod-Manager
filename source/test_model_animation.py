"""Animated GLB playback and external CryEngine conversion bridge tests."""
import math
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from animation_converter_bridge import validate_conversion
from model_animation import AnimatedGLB, _slerp
from model_blender_export import glb_bytes
from model_preview import read_preview_mesh
from model_rigging import skin_glb
from model_textured_export import _unpack_glb, _pack_glb
from test_model_rigging import tiny_mesh, tiny_skeleton


def animated_fixture():
    raw = tiny_mesh()
    mesh = read_preview_mesh(raw, 'synthetic.skinm')
    rigged, _ = skin_glb(glb_bytes(mesh), raw, tiny_skeleton())
    doc, binary = _unpack_glb(rigged)
    child_id = doc['skins'][0]['joints'][1]
    child_node = doc['nodes'][child_id]
    child_node.pop('matrix', None)
    child_node.update(translation=[1.,0.,0.], rotation=[0.,0.,0.,1.])
    def append_accessor(payload, width, kind):
        binary.extend(b'\x00' * (-len(binary)%4))
        start = len(binary)
        binary.extend(payload)
        doc['bufferViews'].append({'buffer':0,'byteOffset':start,'byteLength':len(payload)})
        doc['accessors'].append({'bufferView':len(doc['bufferViews'])-1,
                                 'componentType':5126,
                                 'count':len(payload)//(4*width),
                                 'type':kind})
        return len(doc['accessors'])-1
    times = append_accessor(struct.pack('<2f',0.,1.),1,'SCALAR')
    locations = append_accessor(struct.pack('<6f',1,0,0,2,0,0),3,'VEC3')
    doc['animations'] = [{
        'name':'Raise Head',
        'samplers':[{'input':times,'output':locations,'interpolation':'LINEAR'}],
        'channels':[{'sampler':0,'target':{'node':child_id,'path':'translation'}}]
    }]
    return _pack_glb(doc,binary)


class AnimationEngineTests(unittest.TestCase):
    def test_animation_clip_skin_deformation_and_scrub(self):
        preview = AnimatedGLB(animated_fixture())
        self.assertIn('Raise Head', preview.clips)
        self.assertEqual(preview.preview_mesh.description, '4 vertices | 4 triangles')
        rest = preview.sample('Raise Head', 0.)
        pose = preview.sample('Raise Head', .5)
        # Synthetic child-local translation changes only weighted vertices.
        self.assertAlmostEqual(float(pose[0,0]-rest[0,0]), 0., places=4)
        self.assertAlmostEqual(float(pose[2,0]-rest[2,0]), .5, places=4)
        self.assertAlmostEqual(float(pose[3,0]-rest[3,0]), .5, places=4)
        self.assertEqual(len(preview.preview_mesh.subsets),1)
        self.assertEqual(len(preview.preview_mesh.triangles),4)

    def test_material_primitives_share_one_skinned_vertex_array(self):
        import copy
        doc, binary = _unpack_glb(animated_fixture())
        primitives = doc['meshes'][0]['primitives']
        # CryEngine CDF parts and materials commonly reuse the same global
        # vertex attributes with different indexed triangle ranges.
        base = copy.deepcopy(primitives[0])
        primitives.extend(copy.deepcopy(base) for _ in range(5))
        preview = AnimatedGLB(_pack_glb(doc, binary))
        self.assertEqual(len(preview.positions), 4)
        self.assertEqual(len(preview.faces), 24)
        self.assertEqual(len(preview.preview_mesh.subsets), 6)
        self.assertEqual(len(preview.sample('Raise Head', .5)), 4)

    def test_clip_validation(self):
        with self.assertRaisesRegex(ValueError,'clip'):
            AnimatedGLB(animated_fixture()).sample('missing',.1)
        doc, raw = _unpack_glb(animated_fixture())
        doc['animations'].clear()
        with self.assertRaisesRegex(ValueError,'No supported skeletal'):
            AnimatedGLB(_pack_glb(doc,raw))

    def test_slerp_antipodal_sign(self):
        a=[0,0,0,1]
        b=[0,0,0,-1]
        np.testing.assert_allclose(_slerp(a,b,.5), a, atol=1e-6)

    def test_original_converter_no_shell_and_paths_are_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            assets=path/'assets';assets.mkdir()
            source=assets/'character.chr';source.write_bytes(b'dummy')
            converter=path/'cgf-converter.exe';converter.write_bytes(b'dummy')
            exported=path/'export';exported.mkdir()
            exe,args=validate_conversion(converter,source,assets,exported)
            self.assertEqual(exe,str(converter))
            self.assertIn('-anim',args)
            self.assertIn('-glb',args)
            self.assertIn(str(assets),args)
            with self.assertRaisesRegex(ValueError,'separate'):
                validate_conversion(converter,source,assets,assets)
            with self.assertRaisesRegex(ValueError,'valid original'):
                validate_conversion(converter,converter,assets,exported)
            with self.assertRaisesRegex(ValueError,'inside'):
                other=path/'outside.chr';other.write_bytes(b'dummy')
                validate_conversion(converter,other,assets,exported)


if __name__ == '__main__':
    unittest.main()
