"""Synthetic CryEngine Banshee Gun DDNA gloss streaming regressions."""
from io import BytesIO
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from unittest import TestCase

from PIL import Image

from ddna_gloss import decode_ddna_gloss
from material_preview import load_preview_materials


def header(fourcc):
    result = bytearray(128)
    result[:4] = b'DDS '
    for position, val in ((4, 124), (8, 0x21007), (12, 16), (16, 16),
                          (28, 5), (76, 32), (80, 4), (108, 0x401008)):
        struct.pack_into('<I', result, position, val)
    result[84:88] = fourcc
    return bytes(result)


def bc4(value):
    return bytes((value, value)) + bytes(6)


def make_banshee_record(root):
    prefix = 'characters/hunters/merc_tech/bansheegun/'
    basename = 'techbansheegun_3p'
    records = {}

    def put(suffix, data):
        relative = prefix + suffix
        file = root / suffix
        file.write_bytes(data)
        records[relative] = [
            {'file': file, 'workspace': None, 'relative': relative}]
        return relative

    mtl = (f'<Material Shader="Illum" Name="Banshee Gun"><Textures>'
           f'<Texture Map="Diffuse" File="{basename}_diff.tif"/>'
           f'<Texture Map="Bumpmap" File="{basename}_ddna.tif"/>'
           f'<Texture Map="Specular" File="{basename}_spec.tif"/>'
           f'</Textures></Material>').encode()
    put(basename+'.mtl', mtl)
    for name, color in ((basename+'_diff', (60,40,58)),
                        (basename+'_spec', (80,80,80))):
        temp = BytesIO()
        Image.new('RGB', (16,16), color).save(temp, 'DDS')
        put(name+'.dds', temp.getvalue())
    # Five 16x16..1x1 BC5 normal mips and corresponding BC4 gloss.
    sizes = (16, 8, 4, 2, 1)
    normal = b''.join(bc4(128) * (max(1,(n+3)//4)**2 * 2)
                      for n in sizes)
    normpath = put(basename+'_ddna.dds.0', header(b'ATI2')+normal)
    chunks = [bc4(value) * max(1,(n+3)//4)**2
              for n, value in zip(sizes, (93,44,33,22,11))]
    gloss_prefix = basename+'_ddna.dds.'
    put(gloss_prefix+'0a', header(b'ATI1')[4:]+b''.join(chunks[-3:]))
    put(gloss_prefix+'1a', chunks[1])
    high = put(gloss_prefix+'2a', chunks[0])
    return records, prefix, basename, normpath, high


class DDNAGlossSidecarTests(TestCase):
    def test_actual_highest_mip_gloss_and_diffuse_colors(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            index, prefix, name, normal, _ = make_banshee_record(root)
            gloss = decode_ddna_gloss(index[normal][0], index=index)
            self.assertEqual(gloss.mode, 'L')
            self.assertEqual(gloss.size, (16,16))
            self.assertEqual(gloss.getpixel((0,0)), 93)
            materials = load_preview_materials(root, prefix+name+'.skinm',
                                               index=index)
            self.assertEqual(materials.gloss_maps[0].getpixel((0,0)), 93)
            self.assertEqual(materials.textures[0].convert('RGB').getpixel((0,0)),
                             (60,40,58))
            self.assertIsNotNone(materials.normal_maps[0])
            self.assertIsNotNone(materials.specular_maps[0])

    def test_incomplete_stream_does_not_silently_use_lowest_mip(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            index, prefix, name, normal, high = make_banshee_record(root)
            index.pop(high)
            (root / high.rsplit('/',1)[-1]).unlink()
            with self.assertRaisesRegex(ValueError, r'missing .2a'):
                decode_ddna_gloss(index[normal][0], index=index)
            materials = load_preview_materials(root, prefix+name+'.skinm',
                                               index=index)
            self.assertIsNone(materials.gloss_maps[0])
            self.assertTrue(any('DDNA gloss unavailable' in issue
                                for issue in materials.issues))
