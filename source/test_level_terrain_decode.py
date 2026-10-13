"""Known-value tests for versioned CryEngine source height sample decoding.

Synthetic 16x16 data only; proprietary terrain/texture payloads are not in git.
"""
from __future__ import annotations

import struct
import unittest

import numpy as np

from level_terrain_decode import decode_terrain, _samples


def synthetic_heightmap(width=16, height=16, maximum=128, unit=2):
    # Source first dimension is world X; second is world Y.
    xs = np.arange(width,dtype=np.int32)[:,None]
    ys = np.arange(height,dtype=np.int32)[None,:]
    heights = 20 + 2*xs + ys
    encoded = np.rint(heights * 65535 / maximum).astype('<u2')
    xml = (f'<Heightmap Width="{width}" Height="{height}" '
           f'UnitSize="{unit}" MaxHeight="{maximum}" '
           'WaterLevel="-1000000"/>\n').encode('ascii')
    length = encoded.size * 2
    payload = (b'\xff' + struct.pack('<H',len(xml)) + xml +
               struct.pack('<I',4) + b'\x0eHeightmapDataW' +
               struct.pack('<III',length,length,0) + encoded.tobytes() +
               b'\x1bHeightmapLayerIdBitmap_ver2' +
               b'\x00' * 16)
    return payload


class TerrainHeightmapTests(unittest.TestCase):
    def test_known_world_samples_and_upward_triangles(self):
        data = synthetic_heightmap()
        raw, width,height,unit,maximum = _samples(data)
        self.assertEqual((width,height,unit,maximum),(16,16,2,128))
        self.assertEqual(raw.shape,(16,16))
        mesh=decode_terrain(data,source='synthetic',max_grid=257)
        self.assertEqual(mesh.decoded_samples,256)
        self.assertEqual(mesh.stride,1)
        self.assertEqual(len(mesh.indices),(15*15*6))
        self.assertTrue(mesh.vertices.flags.writeable is False)
        # index = source x * height + source y; no reversed axes.
        p=mesh.vertices[3*16+4]
        self.assertAlmostEqual(float(p[0]),6,places=4)
        self.assertAlmostEqual(float(p[1]),8,places=4)
        self.assertAlmostEqual(float(p[2]),30,delta=.01)
        a,b,c=mesh.vertices[mesh.indices[:3]]
        cross=np.cross(b-a,c-a)
        self.assertGreater(float(cross[2]),0)
        self.assertGreater(float(mesh.normals[50,2]),0)

    def test_preserves_authored_boundaries_when_downsampling(self):
        mesh=decode_terrain(synthetic_heightmap(128,128),max_grid=33)
        self.assertEqual(mesh.width,128)
        self.assertEqual(mesh.height,128)
        self.assertLessEqual(len(mesh.vertices),33*33)
        self.assertEqual(float(mesh.vertices[:,0].max()),254.)
        self.assertEqual(float(mesh.vertices[:,1].max()),254.)
        self.assertTrue(np.isfinite(mesh.vertices).all())

    def test_rejects_unknown_version_lengths_and_malformed_terrain(self):
        raw=synthetic_heightmap()
        with self.assertRaisesRegex(ValueError,'header'):
            decode_terrain(bytes([0])+raw[1:])
        with self.assertRaisesRegex(ValueError,'section'):
            decode_terrain(raw.replace(b'HeightmapData',b'HeightmapDatz'))
        with self.assertRaisesRegex(ValueError,'length'):
            decode_terrain(raw.replace(b'\x00\x02\x00\x00',
                                       b'\x00\x03\x00\x00',1))
        with self.assertRaisesRegex(ValueError,'Truncated'):
            decode_terrain(raw[:len(raw)//2])
        with self.assertRaisesRegex(ValueError,'resolution'):
            decode_terrain(synthetic_heightmap(24,24))


if __name__ == '__main__':
    unittest.main()
