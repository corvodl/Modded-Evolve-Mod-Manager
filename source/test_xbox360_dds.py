"""Synthetic Xbox 360 BC/CTX1 read-only transforms; no game textures shipped."""
from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from dds_texture import parse_dds
from test_editor_tabs_png import fixture_dds
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
        # Pillow defaults to an uncompressed DDS; explicitly request BC1.
        raw=fixture_dds('DXT1', (128,128), 1)
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
        raw=fixture_dds('DXT1', (128,128), 1)
        with self.assertRaisesRegex(ValueError,'surface header'):
            decode_2012_console_dds(raw)
        candidate=xboxify(raw)
        with self.assertRaisesRegex(ValueError,'highest-resolution'):
            decode_2012_console_dds(candidate[:-12])
        with self.assertRaisesRegex(ValueError,'normal-map slot'):
            decode_2012_console_dds(xboxify(
                empty_dds()+ bytes((128,128,128,128,0,0,0,0))*(32*32)))

    def test_local_split_fragment_diagnostic_exports_png_without_edits(self):
        from xbox360_texture_diagnostics import decode_fragment_files
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'library';source.mkdir()
            output=root/'exports';output.mkdir()
            model=source/'test.chr'
            model.write_bytes(b'CryTek\0\0'+struct.pack('<2I',0xffff0000,0x745))
            original=fixture_dds('DXT1',(128,128),5)
            info=parse_dds(original)
            lengths=[8192,2048,512,128,32]
            chain=original[info.data_offset:]
            self.assertEqual(len(chain),sum(lengths))
            mips=[]
            at=0
            for size in lengths:
                mips.append(chain[at:at+size])
                at+=size
            tiled=xboxify(original)
            header=bytearray(tiled[:128])
            # Small tail mips are irrelevant to the top-resolution preview,
            # but the complete stream still needs valid lengths and order.
            fragments=[
                bytes(header)+mips[3]+mips[4], mips[2],mips[1],tiled[128:]]
            paths=[]
            for i,raw in enumerate(fragments):
                path=source/('test.dds.'+str(i));path.write_bytes(raw)
                paths.append(path)
            expected=[p.read_bytes() for p in paths]
            png=output/'decoded.png'
            report=decode_fragment_files(model,paths,png)
            self.assertEqual(report['size'],(128,128))
            self.assertEqual(len(report['fragments']),4)
            with Image.open(png) as converted,Image.open(BytesIO(original)) as reference:
                self.assertEqual(converted.convert('RGB').tobytes(),
                                 reference.convert('RGB').tobytes())
            self.assertEqual([p.read_bytes() for p in paths],expected)
            with self.assertRaisesRegex(ValueError,'NEW output'):
                decode_fragment_files(model,paths,png)

    def test_512_body_texture_split_between_low_and_high_paks(self):
        """Exact 2012 ParasiteHost layout: low holds .0-.4, high holds .5.

        No game texture bytes are used. The test proves the actual file split
        cannot accidentally fall back to the 32px low-mip header and that the
        highest 512px mip is untiled from the separately indexed high PAK.
        """
        from material_preview import _decode_texture
        from dds_streaming import _mip_sizes

        prefix = 'objects/characters/monsters/parasitehost/'
        name = 'parasitehost.dds'
        original = fixture_dds('DXT1', (512, 512), 8)
        info = parse_dds(original)
        sizes = _mip_sizes(info, original)
        self.assertEqual(sizes, [131072, 32768, 8192, 2048, 512, 128, 32, 8])
        mip = []
        cursor = info.data_offset
        for length in sizes:
            mip.append(original[cursor:cursor+length])
            cursor += length
        header = bytearray(xboxify(original)[:info.data_offset])
        pieces = [bytes(header) + b''.join(mip[5:]),
                  mip[4], mip[3], mip[2], mip[1],
                  xboxify(original)[info.data_offset:]]
        self.assertEqual([len(p) for p in pieces],
                         [296, 512, 2048, 8192, 32768, 131072])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            low = root / 'dds_low.pak_import' / 'files'
            high = root / 'dds_high.pak_import' / 'files'
            index = {}
            paths = []
            for n, data in enumerate(pieces):
                workspace = low if n < 5 else high
                relative = prefix + name + '.' + str(n)
                file = workspace / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
                paths.append(file)
                index[relative.casefold()] = [{
                    'file': file, 'relative': relative,
                    'workspace': workspace.parent,
                    'archive': 'dds_low' if n < 5 else 'dds_high'}]
            item = index[(prefix+name+'.0').casefold()][0]
            result = _decode_texture(item, index=index, historical_2012=True)
            self.assertEqual(result.size, (512, 512))
            with Image.open(BytesIO(original)) as expected:
                self.assertEqual(result.convert('RGB').tobytes(),
                                 expected.convert('RGB').tobytes())
            self.assertEqual([p.read_bytes() for p in paths], pieces)

    def test_512_normal_ctx1_split_low_high_maintains_source_bytes(self):
        """Test the supplied ParasiteHost _ddn.dds.0-.4/.5 arrangement."""
        from material_preview import _decode_texture
        from dds_streaming import _mip_sizes

        header = bytearray(empty_dds(b'CTX1', 512, 512))
        struct.pack_into('<I', header, 28, 8)
        struct.pack_into('<I', header, 36, 0x1040)
        info = parse_dds(header)
        sizes = _mip_sizes(info, header)
        neutral = bytes((128, 128, 128, 128, 0, 0, 0, 0))
        mips = [neutral * (size//8) for size in sizes]
        tiled = bytearray(xboxify(bytes(header) + b''.join(mips)))
        struct.pack_into('<I', tiled, 36, 0x1040)
        pieces = [
            bytes(tiled[:128]) + b''.join(mips[5:]),
            mips[4], mips[3], mips[2], mips[1], bytes(tiled[128:])]
        self.assertEqual([len(x) for x in pieces],
                         [296, 512, 2048, 8192, 32768, 131072])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            name = 'objects/characters/monsters/parasitehost/parasitehost_ddn.dds'
            index, paths = {}, []
            for n, fragment in enumerate(pieces):
                workspace = root / ('dds_low' if n < 5 else 'dds_high')
                file = workspace / 'files' / (name + '.' + str(n))
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(fragment)
                paths.append(file)
                index[(name + '.' + str(n)).casefold()] = [{
                    'file': file, 'relative': name + '.' + str(n),
                    'workspace': workspace,
                    'archive': workspace.name}]
            diffuse = index[(name + '.0').casefold()][0]
            preview = _decode_texture(
                diffuse, index=index, historical_2012=True, normal_map=True)
            self.assertEqual(preview.size, (512, 512))
            r, g, b, a = preview.getpixel((55, 91))
            self.assertTrue(119 <= r <= 137 and 119 <= g <= 137)
            self.assertGreater(b, 240)
            self.assertEqual(a, 255)
            self.assertEqual([f.read_bytes() for f in paths], pieces)

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
