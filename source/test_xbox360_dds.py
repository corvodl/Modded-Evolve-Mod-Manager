"""Synthetic Xbox 360 BC/CTX1 read-only transforms; no game textures shipped."""
from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from dds_texture import parse_dds
from material_preview import _historical_2012_character
from xbox360_dds import (decode_2012_console_dds, is_2012_console_dds,
                         _tiled_to_xy, untile_xenos_blocks)


def xboxify(top_level_dds):
    """Build one in-memory Xenos-tiled and word-swapped test texture."""
    info = parse_dds(top_level_dds)
    fourcc = top_level_dds[84:88]
    pitch = 16 if fourcc == b'DXT5' else 8
    width = info.width//4
    num = (info.width//4)*(info.height//4)
    raw = top_level_dds[info.data_offset: info.data_offset+num*pitch]
    if len(raw) != num*pitch:
        raise ValueError('Synthetic source lacks top mip.')
    tiled = bytearray(num*pitch)
    for tile in range(num):
        x, y = _tiled_to_xy(tile, width, pitch)
        src = (y*width+x)*pitch
        tile_data = bytearray(raw[src:src+pitch])
        for i in range(0,pitch,2):
            tile_data[i],tile_data[i+1]=tile_data[i+1],tile_data[i]
        tiled[tile*pitch:(tile+1)*pitch]=tile_data
    hdr=bytearray(top_level_dds[:info.data_offset])
    struct.pack_into('<I',hdr,36,0x1800)
    return bytes(hdr) + bytes(tiled)


def empty_dds(fourcc=b'CTX1', width=128, height=128):
    """Make a valid standalone one-mip DDS envelope."""
    head=bytearray(128)
    head[:4]=b'DDS '
    struct.pack_into('<I',head,4,124)
    struct.pack_into('<I',head,8,0x1007)
    struct.pack_into('<II',head,12,height,width)
    struct.pack_into('<I',head,28,1)
    struct.pack_into('<I',head,36,0x1040)
    struct.pack_into('<I',head,76,32)
    struct.pack_into('<I',head,80,4)
    head[84:88]=fourcc
    struct.pack_into('<I',head,108,0x1000)
    return bytes(head)


class Xbox2012DecodeTests(unittest.TestCase):
    def test_xenos_block_coords_are_unique(self):
        for pitch, dims in ((8,(32,32)),(16,(64,32)),(8,(128,128))):
            w,h=dims
            coords={_tiled_to_xy(i,w,pitch) for i in range(w*h)}
            self.assertEqual(len(coords),w*h)
            self.assertTrue(all(0<=x<w and 0<=y<h for x,y in coords))

    def test_linear_dxt5_tiling_and_endian_roundtrip(self):
        src=BytesIO()
        image=Image.new('RGBA',(128,128),(60,120,180,255))
        image.save(src,format='DDS')
        raw=src.getvalue()
        self.assertIn(raw[84:88],(b'DXT1',b'DXT5'))
        tiled=xboxify(raw)
        self.assertTrue(is_2012_console_dds(tiled))
        decoded=decode_2012_console_dds(tiled)
        with Image.open(BytesIO(raw)) as orig:
            self.assertEqual(decoded.convert('RGB').tobytes(),
                             orig.convert('RGB').tobytes())

    def test_ctx1_rg_reconstruction(self):
        # Per-block R/G neutral midpoints and selector index zero.
        neutral=bytes((128,128,128,128,0,0,0,0))
        header=empty_dds()
        tiled=xboxify(header + neutral*(32*32))
        converted=decode_2012_console_dds(tiled,normal_map=True)
        self.assertEqual(converted.size,(128,128))
        r,g,b,a=converted.getpixel((0,0))
        self.assertGreater(b,240)
        self.assertTrue(119<=r<=137 and 119<=g<=137)
        self.assertEqual(a,255)

    def test_missing_header_oversized_and_truncated_refused(self):
        src=BytesIO()
        Image.new('RGB',(128,128),(20,40,60)).save(src,format='DDS')
        raw=src.getvalue()
        with self.assertRaisesRegex(ValueError,'surface header'):
            decode_2012_console_dds(raw)
        candidate=xboxify(raw)
        with self.assertRaisesRegex(ValueError,'highest-resolution'):
            decode_2012_console_dds(candidate[:-12])
        with self.assertRaisesRegex(ValueError,'normal-map slot'):
            decode_2012_console_dds(xboxify(
                empty_dds()+ bytes((128,128,128,128,0,0,0,0))*(32*32)))

    def test_model_gate_does_not_transform_stage2_or_unknown_character(self):
        with tempfile.TemporaryDirectory() as td:
            name='Objects/characters/monsters/ParasiteHost/ParasiteHost3.chr'
            file=Path(td)/'model.chr'
            file.write_bytes(b'CryTek\0\0' + struct.pack('<2I',0xffff0000,0x745))
            index={name.casefold():[{'file':file,'relative':name,'workspace':None}]}
            self.assertTrue(_historical_2012_character(index,td,name))
            file.write_bytes(b'CrChF\x07\0\0' + bytes(8))
            self.assertFalse(_historical_2012_character(index,td,name))


if __name__ == '__main__':
    unittest.main()
