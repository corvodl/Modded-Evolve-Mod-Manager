"""Synthetic CryEngine CrChF v7 model tests; no game assets bundled."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from model_asset import inspect_model, validate_model_replacement, export_model, replace_model, is_model

def fixture(char=b'X'):
    name=b'TestGeometry'+bytes(21)
    verts=struct.pack('<6I',0,0,3,8,0,0)+char*24
    table=struct.pack('<4I',0x08021014,1,len(name),48)
    table+=struct.pack('<4I',0x08001016,2,len(verts),48+len(name))
    return b'CrChF\x07\x00\x00'+struct.pack('<II',2,16)+table+name+verts

class NativeModelTests(unittest.TestCase):
    def test_extensions(self):
        for name in ('goliath.skin','goliath.skinm','goliath.chr','goliath.chrm','object.cgam','object.cgfm'):
            self.assertTrue(is_model(name))
    def test_parse_real_layout(self):
        info=inspect_model(fixture())
        self.assertEqual((info.format_name,info.chunks,info.version),('CrChF',2,7))
        self.assertEqual(info.streams,(('positions',3,8),))
        self.assertIn('3 vertices',info.description)
    def test_reject_bad_headers(self):
        data=bytearray(fixture());data[5]=8
        with self.assertRaises(ValueError):inspect_model(data)
        data=bytearray(fixture());struct.pack_into('<I',data,48+32+8,4)
        with self.assertRaises(ValueError):inspect_model(data)
    def test_safe_import_backup(self):
        original,changed=fixture(b'X'),fixture(b'Y')
        validate_model_replacement(original,changed)
        with tempfile.TemporaryDirectory() as folder:
            workspace=Path(folder)/'work'
            source=workspace/'files'/'goliath.skinm'
            source.parent.mkdir(parents=True)
            source.write_bytes(original)
            exported=Path(folder)/'export.skinm'
            export_model(workspace,'goliath.skinm',exported)
            self.assertEqual(exported.read_bytes(),original)
            edited=Path(folder)/'edited.skinm';edited.write_bytes(changed)
            replace_model(workspace,'goliath.skinm',edited,hashlib.sha256(original).hexdigest())
            self.assertEqual(source.read_bytes(),changed)
            self.assertEqual(len(list((workspace/'EditorBackups').rglob('goliath.skinm'))),1)

if __name__=='__main__': unittest.main()
