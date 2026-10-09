"""Regression checks using temporary projects and a generated signed CryPAK."""
import contextlib
import io
import json
from pathlib import Path
import os
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zlib
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from workspace_editor import save_text, digest, validate_xml
from refresh_originals import refresh
from evolve_pak_rekey import rsa_private_oaep_wrap, load_with_signed_basename
from evolve_video_pak_tool_fixed import twofish_ctr, file_iv
from evolve_gameplay_editor import encode_cryxml, decrypt_entry, decode_cryxml
from evolve_pak_workspace import cmd_extract, cmd_build


def fixture(path, key):
    name=b'libs/Abilities/test.xml'
    data=encode_cryxml(ET.fromstring('<Ability damage="100" health="100"/>'))
    crc=zlib.crc32(data)&0xffffffff
    keys=[bytes([i+1])*16 for i in range(16)];iv=bytes(16)
    encrypted=twofish_ctr(data,keys[(~(crc>>2))&15],file_iv(dict(compressed_size=len(data),original_size=len(data),crc=crc)))
    local=struct.pack('<4s5H3I2H',b'PK\x03\x04',20,0,13,0,0,crc,len(data),len(data),len(name),0)+name+encrypted
    cdr=struct.pack('<4s6H3I5H2I',b'PK\x01\x02',20,20,0,13,0,0,crc,len(data),len(data),len(name),0,0,0,0,0,0)+name
    comment=bytearray(2320);comment[:6]=b'\x06\x00\x00\x00\x01\x03'
    struct.pack_into('<I',comment,6,133);struct.pack_into('<I',comment,139,2181)
    comment[11:139]=key.sign(cdr+path.name.encode(),padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=0),hashes.SHA256())
    comment[143:271]=rsa_private_oaep_wrap(iv,key)
    for i,k in enumerate(keys):comment[272+i*128:400+i*128]=rsa_private_oaep_wrap(k,key)
    eocd=struct.pack('<4s4H2IH',b'PK\x05\x06',0,0,1,1,len(cdr),len(local),len(comment))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(local+twofish_ctr(cdr,keys[0],iv)+eocd+comment)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ws=Path(self.tmp.name);(self.ws/'files').mkdir()
        self.path=self.ws/'files'/'a.xml';self.raw=b'\xef\xbb\xbf<r a="1"/>\r\n';self.path.write_bytes(self.raw)

    def test_save_retains_bom_newlines_and_backup(self):
        out=save_text(self.ws,'a.xml','<r a="22"/>\n',digest(self.raw),self.raw,True)
        self.assertEqual(out,b'\xef\xbb\xbf<r a="22"/>\r\n')
        self.assertEqual(next((self.ws/'EditorBackups').rglob('a.xml')).read_bytes(),self.raw)

    def test_external_edit_not_overwritten(self):
        self.path.write_bytes(b'<changed/>')
        with self.assertRaisesRegex(ValueError,'outside'):save_text(self.ws,'a.xml','<r a="2"/>',digest(self.raw),self.raw,True)
        self.assertEqual(self.path.read_bytes(),b'<changed/>')

    def test_invalid_and_structural_changes_not_saved(self):
        for text in ('<r>','<renamed a="1"/>','<r a="1"><new/></r>'):
            with self.assertRaises(Exception):save_text(self.ws,'a.xml',text,digest(self.raw),self.raw,True)
            self.assertEqual(self.path.read_bytes(),self.raw)

    def test_path_escape_and_entity_block(self):
        with self.assertRaises(ValueError):save_text(self.ws,'../a.xml','x','',b'')
        with self.assertRaises(ValueError):validate_xml(b'<!DOCTYPE r [<!ENTITY x "y">]><r>&x;</r>')

    def test_namespace_names_stay_literal(self):
        root=validate_xml(b'<r xmlns:x="urn:test" x:a="1"/>')
        self.assertEqual(list(root.attrib),['xmlns:x','x:a'])


class RefreshTests(unittest.TestCase):
    def setUp(self):
        guard=patch('refresh_originals.ensure_closed');guard.start();self.addCleanup(guard.stop)
        space=patch('refresh_originals.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3));space.start();self.addCleanup(space.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.game=self.root/'game';self.stage=self.root/'old'/'staged';self.swap=self.root/'swap';self.dest=self.root/'new'
        self.stage.mkdir(parents=True);self.swap.mkdir();(self.game/'bin64_SteamRetail').mkdir(parents=True)
        (self.game/'bin64_SteamRetail'/'Evolve.exe').write_bytes(b'fixture')
        self.original_key=rsa.generate_private_key(public_exponent=65537,key_size=1024)
        self.custom_key=rsa.generate_private_key(public_exponent=65537,key_size=1024)
        self.pub=self.original_key.public_key().public_bytes(Encoding.DER,PublicFormat.PKCS1)
        (self.stage.parent/'RSAKeyData.bin').write_bytes(self.pub)
        (self.stage/'mykeys').mkdir()
        (self.stage/'mykeys'/'public_key.bin').write_bytes(self.custom_key.public_key().public_bytes(Encoding.DER,PublicFormat.PKCS1))
        (self.stage/'mykeys'/'private_key.pem').write_bytes(self.custom_key.private_bytes(Encoding.PEM,PrivateFormat.TraditionalOpenSSL,NoEncryption()))
        shim=bytearray(4096);shim[0x870:0x8fc]=self.pub
        (self.game/'bin64_SteamRetail'/'inject.dll').write_bytes(shim)
        self.pak=self.game/'Game'/'libs.pak';fixture(self.pak,self.original_key)
        self.before=self.pak.read_bytes()
        self.output=io.StringIO();self.silent=contextlib.redirect_stdout(self.output);self.silent.__enter__();self.addCleanup(self.silent.__exit__,None,None,None)

    def prepared(self):
        self.ready=Path(str(self.pak)+'.customkey-ready');self.ready.write_bytes(b'old mod')
        self.state={'version':1,'phase':'prepared','game':str(self.game),'stage':str(self.stage),'files':[
            {'name':'Game/libs.pak','target':str(self.pak),'source':str(self.stage/'paks'/'Game'/'libs.pak'),
             'ready':str(self.ready),'backup':str(self.pak)+'.customkey-original','sha256':digest(b'old mod'),'bytes':7}]}
        (self.swap/'controlled_swap_state.json').write_text(json.dumps(self.state))

    def test_refresh_then_extract_edit_and_build_real_crypto(self):
        self.prepared()
        report=refresh(self.game,self.stage,self.swap,self.dest)
        self.assertEqual(report['status'],'complete');self.assertEqual(report['signed_count'],1)
        self.assertEqual(self.pak.read_bytes(),self.before)
        self.assertEqual((self.dest/'originals'/'Game'/'libs.pak').read_bytes(),self.before)
        self.assertFalse(self.ready.exists());self.assertEqual(Path(report['ready_moves'][0]['to']).read_bytes(),b'old mod')
        stage=Path(report['stage']);key=stage/'mykeys'/'public_key.bin';source=stage/'paks'/'Game'/'libs.pak'
        parsed,_=load_with_signed_basename(source,key)
        self.assertEqual(parsed['count'],1)
        ws=self.root/'project';cmd_extract(SimpleNamespace(pak=source,public_key=key,workspace=ws,filter=[],skip_unsupported=True))
        rel='libs/Abilities/test.xml';raw=(ws/'files'/rel).read_bytes()
        save_text(ws,rel,raw.decode().replace('damage="100"','damage="2500"'),digest(raw),raw,True)
        out=self.root/'built'/'libs.pak';out.parent.mkdir()
        cmd_build(SimpleNamespace(workspace=ws,pak=None,public_key=key,private_key=stage/'mykeys'/'private_key.pem',out=out))
        parsed,_=load_with_signed_basename(out,key);xml=decode_cryxml(decrypt_entry(out,parsed,rel))
        self.assertEqual(xml.attrib,{'damage':'2500','health':'100'})
        # New swap journal works with a changed archive count and game path.
        import importlib.util
        spec=importlib.util.spec_from_file_location('fresh_swap',Path(report['swap'])/'controlled_swap.py')
        swap=importlib.util.module_from_spec(spec);spec.loader.exec_module(swap)
        with patch.object(swap,'all_closed'):
            swap.prepare(SimpleNamespace(game_root=self.game,stage_dir=stage))
        self.assertEqual(swap.require_state()['count'],2)

    def test_active_swap_rejected_without_output(self):
        self.prepared();self.state['phase']='swapped'
        (self.swap/'controlled_swap_state.json').write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError,'active'):refresh(self.game,self.stage,self.swap,self.dest)
        self.assertFalse(self.dest.exists());self.assertEqual(self.pak.read_bytes(),self.before)

    def test_custom_pak_rejected_as_original(self):
        fixture(self.pak,self.custom_key)
        with self.assertRaises(ValueError):refresh(self.game,self.stage,self.swap,self.dest)
        self.assertFalse((self.stage/'REFRESHED-TO.json').exists())

    def test_failed_rebuild_keeps_prepared_files_and_journal(self):
        self.prepared();prior=(self.swap/'controlled_swap_state.json').read_bytes()
        with patch('refresh_originals.cmd_resign',side_effect=OSError('simulated disk failure')):
            with self.assertRaisesRegex(OSError,'disk failure'):refresh(self.game,self.stage,self.swap,self.dest)
        self.assertEqual(self.ready.read_bytes(),b'old mod')
        self.assertEqual((self.swap/'controlled_swap_state.json').read_bytes(),prior)
        self.assertFalse((self.stage/'REFRESHED-TO.json').exists())

    def test_activation_failure_rolls_back(self):
        self.prepared();replace=os.replace
        def fail_ready(src,dst):
            if Path(src)==self.ready:raise OSError('simulated move failure')
            return replace(src,dst)
        with patch('refresh_originals.os.replace',side_effect=fail_ready):
            with self.assertRaisesRegex(OSError,'move failure'):refresh(self.game,self.stage,self.swap,self.dest)
        self.assertTrue(self.ready.exists());self.assertFalse((self.stage/'REFRESHED-TO.json').exists())
        self.assertEqual(json.loads((self.swap/'controlled_swap_state.json').read_text())['phase'],'prepared')

if __name__=='__main__':unittest.main()
