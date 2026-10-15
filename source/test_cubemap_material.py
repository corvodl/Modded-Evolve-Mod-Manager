"""Six-face BC1 cubemap and safe split-mip assembly regression tests."""
import struct
import unittest
from cubemap_material import assemble_cubemap_stream, decode_cubemap_dds


def cubemap_header(size=4,mips=1):
    header=bytearray(128)
    header[:4]=b'DDS '
    struct.pack_into('<I',header,4,124)
    struct.pack_into('<I',header,8,0x100f | (0x20000 if mips>1 else 0))
    struct.pack_into('<II',header,12,size,size)
    struct.pack_into('<I',header,28,mips)
    struct.pack_into('<I',header,76,32)
    struct.pack_into('<I',header,80,4)
    header[84:88]=b'DXT1'
    struct.pack_into('<I',header,112,0xfe00)
    return bytes(header)


class CubemapTests(unittest.TestCase):
    def test_complete_six_face_cubemap(self):
        # Same DXT1 red block in each face.
        red=struct.pack('<HHI',0xf800,0,0)
        cubes=decode_cubemap_dds(cubemap_header()+red*6)
        self.assertEqual(len(cubes),6)
        self.assertEqual(cubes[0].size,(4,4))
        self.assertGreater(cubes[0].getpixel((0,0))[0],230)
        self.assertEqual(cubes[3].tobytes(),cubes[0].tobytes())

    def test_streamed_cubemap_joins_per_face_high_mips(self):
        low=struct.pack('<HHI',0x07e0,0,0)
        high=struct.pack('<HHI',0xf800,0,0)
        # 8x8 BC1 top: 32 bytes per face. 4x4 tail: 8 bytes.
        top=high*4
        part0=cubemap_header(8,2)+low*6
        reconstructed=assemble_cubemap_stream(part0,{1:top*6})
        faces=decode_cubemap_dds(reconstructed)
        self.assertEqual(len(faces),6)
        self.assertEqual(faces[0].size,(8,8))
        self.assertGreater(faces[0].getpixel((0,0))[0],230)
        with self.assertRaises(ValueError):
            assemble_cubemap_stream(part0,{1:top*5})
        with self.assertRaises(ValueError):
            assemble_cubemap_stream(part0,{})

    def test_reject_incomplete_cube_and_non_cube(self):
        red=struct.pack('<HHI',0xf800,0,0)
        with self.assertRaises(ValueError):
            decode_cubemap_dds(cubemap_header()+red*5)
        with self.assertRaises(ValueError):
            decode_cubemap_dds(red*6)


if __name__=='__main__':
    unittest.main()
