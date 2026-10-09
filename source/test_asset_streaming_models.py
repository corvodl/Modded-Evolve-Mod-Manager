"""Split DDS and model workflow tests (self-generated files, no copyrighted assets)."""
from pathlib import Path
import hashlib
import struct
import tempfile
import unittest

from dds_texture import preview_dds
from dds_streaming import inspect_stream, replace_stream
from model_asset import (inspect_model, validate_model_replacement, export_model,
                         replace_model, is_model)


def dxt5_fixture(width=256, height=256):
    """Build a standard 2D DXT5 surface with complete mipmaps and solid black blocks."""
    levels = width.bit_length()
    sizes = [max(1, ((max(1, width >> i) + 3) // 4)) * max(1, ((max(1, height >> i) + 3) // 4)) * 16 for i in range(levels)]
    # Explicit conventional DDS header; zeroed compressed blocks are valid DXT5 data.
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<I', header, 4, 124)
    struct.pack_into('<I', header, 8, 0x21007)
    struct.pack_into('<I', header, 12, height)
    struct.pack_into('<I', header, 16, width)
    struct.pack_into('<I', header, 20, sizes[0])
    struct.pack_into('<I', header, 28, levels)
    struct.pack_into('<I', header, 76, 32)
    struct.pack_into('<I', header, 80, 4)
    header[84:88] = b'DXT5'
    struct.pack_into('<I', header, 108, 0x401008)
    return bytes(header) + b''.join(bytes([i])*n for i,n in enumerate(sizes))


def split_fixture(root, texture='test.dds'):
    whole = dxt5_fixture()
    # 256^2 DXT5: 65536, 16384, 4096, 1024, 256, 64, 16, 16, 16
    sizes = [65536, 16384, 4096, 1024, 256, 64, 16, 16, 16]
    assert len(whole)==128+sum(sizes)
    pos=128
    mip=[]
    for n in sizes:
        mip.append(whole[pos:pos+n]);pos+=n
    (root/(texture+'.0')).write_bytes(whole[:128]+b''.join(mip[-3:]))
    for i,item in enumerate(reversed(mip[:-3]),1):
        (root/(texture+'.'+str(i))).write_bytes(item)
    return whole


def model_fixture(chunk=b'X'*28):
    # Signature + type/version/table offset + editable chunk payload + chunk table
    assert len(chunk)==28
    header=b'CryTek\0\0'+struct.pack('<III',0xFFFF0000,0x745,48)
    table=struct.pack('<I',1)+struct.pack('<IIII',0xCCCC0000,0x800,20,1)
    return header+chunk+table


class StreamingDDSTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        self.work=Path(self.dir.name)/'workspace';self.files=self.work/'files'
        self.files.mkdir(parents=True)
        self.whole=split_fixture(self.files)

    def test_reassemble_and_decode(self):
        s=inspect_stream(self.work,'test.dds.3')
        self.assertEqual(s.merged,self.whole)
        self.assertEqual(s.tail_mips,3)
        self.assertEqual(s.count,7)
        self.assertEqual(preview_dds(s.merged).size,(256,256))

    def test_import_full_dds_and_backup_all_fragments(self):
        edited=bytearray(self.whole)
        edited[128:144]=bytes([0])*16
        edited[144:160]=bytes([15])*16
        source=Path(self.dir.name)/'replacement.dds';source.write_bytes(edited)
        original=inspect_stream(self.work,'test.dds.0')
        result=replace_stream(self.work,'test.dds.0',source,original.hashes)
        self.assertEqual(result.merged,edited)
        self.assertEqual(result.count,original.count)
        self.assertNotEqual(result.hashes,original.hashes)
        backups=list((self.work/'EditorBackups').rglob('test.dds.*'))
        self.assertEqual(len(backups),7)
        self.assertEqual(sorted(x.name for x in backups),sorted(x.name for x in original.files))

    def test_reject_missing_fragments(self):
        (self.files/'test.dds.3').unlink()
        with self.assertRaisesRegex(ValueError,'full split DDS set'):
            inspect_stream(self.work,'test.dds.0')

    def test_reject_bad_sizes_without_touching_files(self):
        (self.files/'test.dds.2').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'expected'):
            inspect_stream(self.work,'test.dds.0')

    def test_reject_mip_mismatch_and_stale_hash(self):
        source=Path(self.dir.name)/'replacement.dds';source.write_bytes(dxt5_fixture(128,128))
        initial=inspect_stream(self.work,'test.dds.0')
        with self.assertRaisesRegex(ValueError,'Incompatible DDS'):
            replace_stream(self.work,'test.dds.0',source,initial.hashes)
        source.write_bytes(self.whole)
        with self.assertRaisesRegex(ValueError,'changed since preview'):
            replace_stream(self.work,'test.dds.0',source,['fake']*len(initial.hashes))
        self.assertFalse((self.work/'EditorBackups').exists())


class CrytekModelTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        self.work=Path(self.dir.name)/'workspace';self.path=self.work/'files'/'models'/'test.cgf'
        self.path.parent.mkdir(parents=True)
        self.base=model_fixture();self.path.write_bytes(self.base)

    def test_model_metadata_and_export(self):
        info=inspect_model(self.base)
        self.assertEqual((info.chunks,info.version,info.file_length),(1,0x745,68))
        self.assertTrue(is_model(self.path))
        out=Path(self.dir.name)/'export.cgf'
        export_model(self.work,'models/test.cgf',out)
        self.assertEqual(out.read_bytes(),self.base)

    def test_guarded_replacement_and_backup(self):
        candidate=model_fixture(b'Y'*28)
        self.assertEqual(validate_model_replacement(self.base,candidate).chunks,1)
        edited=Path(self.dir.name)/'edited.cgf';edited.write_bytes(candidate)
        result=replace_model(self.work,'models/test.cgf',edited,hashlib.sha256(self.base).hexdigest())
        self.assertEqual(result,candidate)
        self.assertEqual(self.path.read_bytes(),candidate)
        saved=list((self.work/'EditorBackups').rglob('test.cgf'))
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0].read_bytes(),self.base)

    def test_reject_changed_chunk_topology(self):
        candidate=bytearray(self.base)
        candidate[-4:]=struct.pack('<I',2)
        with self.assertRaisesRegex(ValueError,'chunk_table'):
            validate_model_replacement(self.base,bytes(candidate))
        with self.assertRaisesRegex(ValueError,'Unrecognized model'):
            inspect_model(b'\0'*68)

    def test_reject_stale_import(self):
        edited=Path(self.dir.name)/'edited.cgf';edited.write_bytes(model_fixture(b'Y'*28))
        with self.assertRaisesRegex(ValueError,'changed outside'):
            replace_model(self.work,'models/test.cgf',edited,'stale')
        self.assertEqual(self.path.read_bytes(),self.base)


if __name__=='__main__':unittest.main()
