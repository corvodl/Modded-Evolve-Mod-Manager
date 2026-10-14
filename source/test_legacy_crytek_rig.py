"""Synthetic 2012 rig fixtures; never commit user game assets or keys."""
import math
import struct
import unittest

import numpy as np

from legacy_crytek_rig import (read_legacy_bones, read_legacy_rig,
                                skin_glb_legacy, probe_legacy_dba)
from model_blender_export import glb_bytes
from model_preview import read_preview_mesh
from model_rigging import validate_skin_rest_pose
from model_textured_export import _unpack_glb


def make_bones(names, parents, translations):
    records = []
    for i, (name, parent, tx) in enumerate(zip(names, parents, translations)):
        record = bytearray(584)
        inverse = (1., 0., 0., -tx, 0., 1., 0., 0.,
                   0., 0., 1., 0.)
        world = (1., 0., 0., tx, 0., 1., 0., 0.,
                 0., 0., 1., 0.)
        struct.pack_into('<12f', record, 216, *inverse)
        struct.pack_into('<12f', record, 264, *world)
        encoded = name.encode()
        record[312:312+len(encoded)] = encoded
        record[312+len(encoded)] = 0
        struct.pack_into('<4i', record, 568, 0, (parent-i if parent >= 0 else 0), 0, 0)
        records.append(bytes(record))
    return bytes(32) + b''.join(records)


def stream(typ, stride, data):
    return struct.pack('<6I', 0, typ, len(data)//stride, stride, 0, 0) + data


def fixture(*, master=False, palette=(0, 1), weights_valid=True,
            version=0x745, corrupt_hierarchy=False, duplicate_name=False,
            two_subsets=False):
    if master:
        names, parents, positions = (['Root', 'Extra', 'A'], [-1, 0, 1], [0., 0.5, 1.])
    else:
        names, parents, positions = (['Root', 'A'], [-1, 0], [0., 1.])
    if corrupt_hierarchy:
        parents[-1] = -1
    if duplicate_name:
        names[-1] = names[0]
    chunks = [(0xACDC0000, make_bones(names, parents, positions))]
    if not master:
        verts = [(2.,0.,0.),(1.,1.,0.),(0.,1.,0.),(0.,0.,0.)]
        chunks += [
            (0xCCCC0016, stream(0, 12, b''.join(struct.pack('<3f', *v) for v in verts))),
            (0xCCCC0016, stream(2, 8, b''.join(struct.pack('<2f', *v) for v in (
                (0,0),(1,0),(0,1),(1,1))))),
            (0xCCCC0016, stream(5, 2, struct.pack('<6H',0,1,2,0,2,3))),
        ]
        weights = [bytes((1,0,0,0,255,0,0,0)),
                   bytes((1,0,0,0,255,0,0,0)),
                   bytes((0,0,0,0,255,0,0,0)),
                   bytes((0,0,0,0,255,0,0,0))]
        if not weights_valid:
            weights[0] = bytes((1,0,0,0,254,0,0,0))
        chunks.append((0xCCCC0016, stream(9, 8, b''.join(weights))))
        rows = ([(0, 6, 0, 4, 2)] if not two_subsets else
                [(0, 3, 0, 3, 2), (3, 3, 0, 4, 2)])
        subsets = struct.pack('<4I', 2, len(rows), 0, 0)
        subsets += b''.join(struct.pack('<5I4f', *row, 0., 0., 0., 0.) for row in rows)
        for i in range(len(rows)):
            p = list(palette) if not (two_subsets and i == 1) else [1, 0]
            subsets += struct.pack('<I', len(p)) + struct.pack('<128H', *(p + [0]*(128-len(p))))
        chunks.append((0xCCCC0017, subsets))
    directory_size = 24 + len(chunks) * 20
    offsets, payloads = [], []
    offset = directory_size
    for ident,(kind, data) in enumerate(chunks,1):
        header = struct.pack('<4I',kind,0x800,offset,ident)
        value = header+data
        offsets.append(struct.pack('<5I',kind,0x800,offset,ident,len(value)))
        payloads.append(value)
        offset += len(value)
    return (b'CryTek\0\0' + struct.pack('<3I',0xffff0000,version,20)
            + struct.pack('<I',len(chunks))+b''.join(offsets)+b''.join(payloads))


def database():
    inner = b'raw unknown controller payload'
    at = 44
    payload = struct.pack('<4I', 0xCCCC000D, 0x905, at, 1) + inner
    return (b'CryTek\0\0'+struct.pack('<3I',0xffff0000,0x745,20)
            + struct.pack('<I',1)
            + struct.pack('<5I',0xCCCC000D,0x905,at,1,len(payload))
            + payload)


class Legacy2012RigTests(unittest.TestCase):
    def test_valid_master_and_body_bones(self):
        names, parents, rest, inverse = read_legacy_bones(fixture(master=True))
        self.assertEqual(names, ('Root','Extra','A'))
        self.assertEqual(parents, (-1,0,1))
        self.assertLess(np.max(np.abs(inverse[2] @ rest[2] - np.eye(4))), 1e-6)

    def test_body_master_remap_and_exact_weights(self):
        rig = read_legacy_rig(fixture(), fixture(master=True), 4)
        self.assertEqual(rig.names, ('Root', 'Extra','A'))
        self.assertEqual(rig.body_bones, ('Root','A'))
        self.assertEqual(rig.subset_count, 1)
        self.assertEqual(rig.joints[:,0].tolist(), [2,2,0,0])
        self.assertEqual(rig.active_joints,(0,2))
        self.assertTrue(np.all(rig.weights.sum(axis=1) == 255))

    def test_glb_has_real_bones_and_rest_pose(self):
        body, skeleton = fixture(), fixture(master=True)
        mesh = read_preview_mesh(body, 'Objects/characters/monsters/ParasiteHost/ParasiteHost3.chr')
        blob, report = skin_glb_legacy(glb_bytes(mesh), body, skeleton)
        doc, data = _unpack_glb(blob)
        self.assertEqual(len(doc['skins']),1)
        self.assertEqual(report['bone_count'],3)
        self.assertEqual(report['body_bones'],2)
        self.assertEqual([doc['nodes'][i]['name'] for i in doc['skins'][0]['joints']],
                         ['Root','Extra','A'])
        attrs = doc['meshes'][0]['primitives'][0]['attributes']
        self.assertIn('JOINTS_0',attrs)
        self.assertIn('WEIGHTS_0',attrs)
        self.assertGreater(validate_skin_rest_pose(blob),0)

    def test_known_joint_deformation_without_faking_a_clip(self):
        rig = read_legacy_rig(fixture(),fixture(master=True),4)
        # Synthetic pose only: rotate A around its world rest origin (1,0,0).
        rest = rig.rest[2]
        theta = math.pi/2
        rotation = np.eye(4)
        rotation[:2,:2] = [[math.cos(theta), -math.sin(theta)],
                           [math.sin(theta), math.cos(theta)]]
        pose = rest @ rotation
        skin_transform = pose @ rig.inverse[2]
        point = np.array([2.,0.,0.,1.])
        deformed = skin_transform @ point
        self.assertTrue(np.allclose(deformed[:3], [1.,1.,0.],atol=1e-6))
        self.assertEqual(rig.weights[0,0],255)

    def test_invalid_weights_palette_and_shared_vertex_conflicts(self):
        for kwargs,match in [
            ({'weights_valid':False},'sum to 255'),
            ({'palette':(0,9)},'invalid body bone'),
            ({'two_subsets':True},'shared vertices'),
        ]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError,match):
                    read_legacy_rig(fixture(**kwargs),fixture(master=True),4)

    def test_invalid_record_names_hierarchy_and_truncation(self):
        for kwargs in ({'duplicate_name':True},{'corrupt_hierarchy':True},
                       {'version':0x744}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    read_legacy_bones(fixture(**kwargs))
        with self.assertRaises(ValueError):
            read_legacy_bones(fixture(master=True)[:-2])
        b=bytearray(fixture(master=True))
        offset = 24+20
        first_matrix = offset+48+216
        struct.pack_into('<f',b,first_matrix,float('nan'))
        with self.assertRaisesRegex(ValueError,'inverse/rest'):
            read_legacy_bones(bytes(b))

    def test_native_historical_dba_cancel_before_external_process(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import patch
        from database_animation import convert_historical_database_motion
        with TemporaryDirectory() as folder:
            root=Path(folder)
            body=root/'body.chr';body.write_bytes(fixture())
            skeleton=root/'skeleton.chr';skeleton.write_bytes(fixture(master=True))
            db=root/'database.dba';db.write_bytes(database())
            virtual='objects/characters/monsters/parasitehost/ParasiteHost3_Skeleton.chr'
            index={virtual.casefold():[{'file':skeleton,'relative':virtual,'workspace':root}]}
            character=SimpleNamespace(rig_parts=((
                'objects/characters/monsters/parasitehost/ParasiteHost3.chr', body, None, 4),),
                skeleton=virtual, cdf='objects/characters/monsters/parasitehost/ParasiteHost3.cdf')
            motion=SimpleNamespace(kind='dba',file=db,virtual_path='animations/monsters/parasitehost_3/ParasiteHost3.dba')
            with patch('subprocess.Popen') as popen:
                with self.assertRaisesRegex(ValueError,'cancelled'):
                    convert_historical_database_motion(
                        b'unused GLB',motion,index,character,preferred_workspace=root,
                        cancelled=lambda: True)
                popen.assert_not_called()

    def test_chrparams_is_metadata_not_controller_coverage(self):
        from legacy_rig_diagnostics import _animation_config
        config=(b'<Params><AnimationList>'
                b'<Animation name="#filepath" path="animations/monsters/parasitehost_3" />'
                b'<Animation name="$TracksDatabase" path="animations/monsters/parasitehost_3/ParasiteHost3.dba" />'
                b'<Animation name="idle" path="idle.caf" />'
                b'</AnimationList></Params>')
        result=_animation_config(config)
        self.assertEqual(result['alias_count'],1)
        self.assertEqual(len(result['database_declarations']),1)
        self.assertIsNone(result['controller_coverage'])
        with self.assertRaises(ValueError):
            _animation_config(b'<!DOCTYPE stuff><Params/>')

    def test_db_metadata_not_a_playable_clip(self):
        info=probe_legacy_dba(database())
        self.assertEqual(info['database_chunk_version'],'0x905')
        self.assertEqual(info['clips_decoded'],0)
        self.assertFalse(info['playback_available'])
        with self.assertRaises(ValueError):
            probe_legacy_dba(fixture())
        with self.assertRaises(ValueError):
            probe_legacy_dba(database()[:-1])


if __name__ == '__main__':
    unittest.main()
