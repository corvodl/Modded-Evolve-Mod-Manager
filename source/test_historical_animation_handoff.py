"""Synthetic historical DBA index and exact-source converter transaction tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import xml.etree.ElementTree as ET

from legacy_crytek_rig import indexed_historical_dba_paths
from database_animation import convert_historical_database_motion
from test_legacy_crytek_rig import fixture, database


class HistoricalDBAHandoffTests(TestCase):
    ROOT = 'objects/characters/monsters/parasitehost/'
    DBA = 'animations/monsters/parasitehost_3/ParasiteHost3.dba'

    def test_index_requires_exact_length_delimiters(self):
        path = b'animations/monsters/parasitehost_3/parasitehost_melee_neutral_stand_idle_forward.caf'
        good = b'\x00' + bytes([len(path)]) + path + b'\x00'
        self.assertEqual(indexed_historical_dba_paths(good), (path.decode(),))
        self.assertEqual(indexed_historical_dba_paths(
            b'\x00\x01' + path + b'\x00'), ())
        self.assertEqual(indexed_historical_dba_paths(
            b'\x00' + bytes([len(path)]) + path + b'X'), ())

    def test_converter_preserves_original_skeleton_configuration(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            assets = {}
            def put(virtual, payload):
                physical = root / Path(virtual).name
                physical.write_bytes(payload)
                assets[virtual.casefold()] = [{
                    'file': physical, 'relative': virtual,
                    'archive': 'synthetic', 'workspace': None}]
                return physical

            body_virtual = self.ROOT + 'ParasiteHost3.chr'
            master_virtual = self.ROOT + 'ParasiteHost3_Skeleton.chr'
            config_virtual = self.ROOT + 'ParasiteHost3_Skeleton.chrparams'
            cdf_virtual = self.ROOT + 'ParasiteHost3.cdf'
            body = put(body_virtual, fixture())
            skeleton = put(master_virtual, fixture(master=True))
            config = (b'<Params><AnimationList><Animation name="$TracksDatabase" '
                      b'path="animations/monsters/parasitehost_3/ParasiteHost3.dba"/>'
                      b'<Animation name="idle" path="idle.caf"/></AnimationList></Params>')
            put(config_virtual, config)
            put(cdf_virtual, b'<CharacterDefinition/>')
            dba = put(self.DBA, database())
            character = SimpleNamespace(rig_parts=((body_virtual, body, None, 4),),
                                        skeleton=master_virtual, cdf=cdf_virtual)
            ref = SimpleNamespace(kind='dba', file=dba, virtual_path=self.DBA)
            observed = {}
            def validate(exe, source, asset_root, output):
                assert source.name == 'ParasiteHost3_Skeleton.chr'
                assert (asset_root / config_virtual).read_bytes() == config
                assert (asset_root / cdf_virtual).read_bytes() == b'<CharacterDefinition/>'
                assert not (asset_root / (self.ROOT + 'ParasiteHost3.chrparams')).exists()
                observed['tested'] = True
                return 'trusted-converter.exe', [str(source)]
            def popen(args, *, cwd, **kwargs):
                (Path(cwd) / 'converted.glb').write_bytes(b'test-converted-glb')
                observed['exe'] = args[0]
                return SimpleNamespace(returncode=0,
                                       communicate=lambda **k: (b'', b''))
            with (patch('animation_converter_bridge.bundled_converter',
                        return_value='pinned-stub.exe'),
                  patch('animation_converter_bridge.validate_conversion',
                        side_effect=validate),
                  patch('database_animation.subprocess.Popen', side_effect=popen),
                  patch('database_animation.attach_converted_motion',
                        return_value=b'validated-payload') as attach):
                result = convert_historical_database_motion(
                    b'weighted-glb', ref, assets, character)
            self.assertEqual(result, b'validated-payload')
            self.assertTrue(observed['tested'])
            self.assertEqual(observed['exe'], 'trusted-converter.exe')
            self.assertEqual(attach.call_args.args[2], None)
            self.assertEqual((root / Path(config_virtual).name).read_bytes(), config)

    def test_db_not_declared_in_original_config_fails_closed(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            body = root / 'body.chr'
            body.write_bytes(fixture())
            db = root / 'database.dba'
            db.write_bytes(database())
            character = SimpleNamespace(
                rig_parts=((self.ROOT + 'ParasiteHost3.chr', body, None, 4),),
                skeleton=self.ROOT + 'ParasiteHost3_Skeleton.chr',
                cdf=self.ROOT + 'ParasiteHost3.cdf')
            ref = SimpleNamespace(kind='dba',file=db,virtual_path=self.DBA)
            master = root / 'master.chr'
            master.write_bytes(fixture(master=True))
            conf = root / 'params.chrparams'
            conf.write_bytes(b'<Params><AnimationList/></Params>')
            index = {
                character.skeleton.casefold(): [{
                    'file': master, 'relative': character.skeleton}],
                (self.ROOT + 'ParasiteHost3_Skeleton.chrparams').casefold(): [{
                    'file': conf,
                    'relative': self.ROOT + 'ParasiteHost3_Skeleton.chrparams'}]}
            with self.assertRaisesRegex(ValueError, 'not declared'):
                convert_historical_database_motion(
                    b'weighted', ref, index, character)
