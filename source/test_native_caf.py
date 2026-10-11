"""No game assets bundled: native CAF decoder and animated GLB regression tests."""
import struct
import unittest
import zlib

import numpy as np

from model_animation import AnimatedGLB
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_rigging import skin_glb, skin_glb_composite
from model_textured_export import _unpack_glb
from native_caf import append_caf_animations, decode_caf
from test_model_rigging import crchf, tiny_mesh, tiny_skeleton


def synthetic_caf(name='root', move=0.4):
    ident = zlib.crc32(name.casefold().encode())
    # 0x829: CRC, 2 rotation keys, 2 position keys, uncompressed XYZW,
    # byte timestamps, float3 positions sharing rotation timestamps.
    header = struct.pack('<IHHBBBBBBH',ident,2,2,1,2,2,0,0,1,0)
    q = struct.pack('<8f',0,0,0,1,0,0,0,1)
    times = b'\x00\x1e'
    pad = b'\0'*2
    positions = struct.pack('<6f',0,0,0,move,0,0)
    chunk = header + q + times + pad + positions
    motion = bytearray(132)
    struct.pack_into('<IIifii',motion,0,2,10,1,1/30,0,30)
    return crchf([(0x09253002,bytes(motion)),(0x0829100d,chunk)])


class NativeCafTests(unittest.TestCase):
    def test_original_829_controller_track(self):
        animation = decode_caf(synthetic_caf())
        self.assertEqual(len(animation.tracks),1)
        ctrl = animation.tracks[0]
        self.assertEqual(ctrl.position_times, (0.,30.))
        self.assertEqual(ctrl.rotation_times,(0.,30.))
        self.assertAlmostEqual(ctrl.positions[1][0],.4,places=5)
        self.assertEqual(len(ctrl.rotations[0]),4)
        self.assertEqual(animation.fps,30.)

    def test_corrupt_tracks_fail_closed(self):
        raw = synthetic_caf()
        with self.assertRaises(ValueError):
            decode_caf(raw[:-8])
        damaged = bytearray(raw)
        damaged[:8] = b'NOT-CAF!'
        with self.assertRaises(ValueError):
            decode_caf(bytes(damaged))

    def test_rigged_glb_receives_native_animation(self):
        raw = tiny_mesh()
        model = read_preview_mesh(raw,'synthetic.skinm')
        rigged, _ = skin_glb(glb_bytes(model),raw,tiny_skeleton())
        animated, info = append_caf_animations(
            rigged,[('root_idle',synthetic_caf('root'))])
        self.assertEqual(info['clips'],1)
        player = AnimatedGLB(animated)
        self.assertIn('root_idle',player.clips)
        first = player.sample('root_idle',0)
        later = player.sample('root_idle',.5)
        self.assertEqual(first.shape, later.shape)
        self.assertTrue(np.isfinite(later).all())
        self.assertFalse(np.allclose(first,later))

    def test_combined_glb_uses_one_shared_native_skeleton(self):
        raw = tiny_mesh()
        small = read_preview_mesh(raw,'p.skinm')
        from character_assembly import combine_meshes
        composite, _ = combine_meshes([('body',small,None),('weapon',small,None)])
        original = glb_bytes(composite)
        combined, report = skin_glb_composite(original,[
            (raw,tiny_skeleton(),len(small.vertices)),
            (raw,tiny_skeleton(),len(small.vertices))
        ],tiny_skeleton())
        self.assertEqual(report['part_count'],2)
        doc, binary = _unpack_glb(combined)
        self.assertEqual(len(doc['skins']),1)
        self.assertEqual(doc['accessors'][
            doc['meshes'][0]['primitives'][0]['attributes']['JOINTS_0']]['count'],
            len(composite.vertices))
        animated, _ = append_caf_animations(
            combined,[('root_idle',synthetic_caf())])
        self.assertTrue(AnimatedGLB(animated).clips)


if __name__ == '__main__':
    unittest.main()
