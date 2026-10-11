"""Synthetic compiled CryEngine skin and glTF deformation regression tests.

Never embeds game files, keys or proprietary mesh assets in source control.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_textured_export import _unpack_glb, textured_model_from_workspace
from model_rigging import (
    parse_compiled_bones, remap_skin_weights, skin_glb, skin_companion_path)


def crchf(chunks):
    size = 16 + 16*len(chunks)
    table, bodies = [], []
    for ident, (kind, data) in enumerate(chunks, 1):
        table.append(struct.pack('<4I', kind, ident, len(data), size))
        bodies.append(data)
        size += len(data)
    return b'CrChF\x07\x00\x00' + struct.pack('<2I', len(chunks), 16) + b''.join(table) + b''.join(bodies)


def bone_record(name, parent_offset, offset=0, *, bad_inverse=False):
    record = bytearray(584)
    matrix = np.eye(4, dtype=np.float32)
    matrix[0, 3] = offset
    inverse = np.linalg.inv(matrix)
    if bad_inverse:
        inverse[0, 3] += 3
    struct.pack_into('<12f', record, 216, *inverse[:3, :4].flat)
    struct.pack_into('<12f', record, 264, *matrix[:3, :4].flat)
    encoded = name.encode('utf-8')
    record[312:312+len(encoded)] = encoded
    struct.pack_into('<4i', record, 568, -1, parent_offset, 0, 0)
    return bytes(record)


def tiny_skeleton(*, bad_inverse=False):
    data = b'\x00'*32
    data += bone_record('root', 0)
    data += bone_record('child', -1, 1, bad_inverse=bad_inverse)
    return crchf([(0x08002000, data)])


def tiny_mesh(*, invalid_weights=False, invalid_palette=False):
    vertices = [(-1., -1., 0., 1.), (1., -1., 0., 1.),
                (0., 1., 0., 1.), (0., 0., 2., 1.)]
    position = struct.pack('<6I', 0, 0, len(vertices), 8, 0, 0)
    position += b''.join(struct.pack('<4e', *v) for v in vertices)
    faces = (0, 1, 2, 0, 1, 3, 1, 2, 3, 2, 0, 3)
    indices = struct.pack('<6I', 0, 5, len(faces), 2, 0, 0)
    indices += struct.pack('<12H', *faces)
    weights = [
        (0, 0, 0, 0, 255, 0, 0, 0),
        (0, 0, 0, 0, 255, 0, 0, 0),
        (1, 0, 0, 0, 255, 0, 0, 0),
        (1, 0, 0, 0, 254 if invalid_weights else 255, 0, 0, 0),
    ]
    bone_map = struct.pack('<6I', 0, 9, 4, 8, 0, 0) + b''.join(
        struct.pack('<8B', *v) for v in weights)
    subset = struct.pack('<4I', 1, 1, 0, 0)
    subset += struct.pack('<5I4f', 0, 12, 0, 4, 0, 1, 0, 0, 0)
    subset += struct.pack('<I128H', 2, 0, 9 if invalid_palette else 1,
                          *([0]*126))
    subset += bytes(48)
    return crchf([
        (0x08001016, position), (0x08001016, indices),
        (0x08001016, bone_map), (0x08001017, subset)])


class CryEngineRiggingTests(unittest.TestCase):
    def test_bone_records_and_bind_transforms(self):
        names, parents, world, inverse = parse_compiled_bones(tiny_skeleton())
        self.assertEqual(names, ['root', 'child'])
        self.assertEqual(parents, [-1, 0])
        self.assertAlmostEqual(world[1][0, 3], 1.0)
        self.assertTrue(np.allclose(world[1] @ inverse[1], np.eye(4)))
        with self.assertRaisesRegex(ValueError, 'inverse bind'):
            parse_compiled_bones(tiny_skeleton(bad_inverse=True))

    def test_palette_weight_and_index_validation(self):
        ids, weights, subset_count = remap_skin_weights(tiny_mesh(), 4, 2)
        self.assertEqual(ids.shape, (4, 4))
        self.assertEqual(ids[:, 0].tolist(), [0, 0, 1, 1])
        self.assertEqual(subset_count, 1)
        self.assertTrue(np.all(weights.sum(axis=1) == 255))
        with self.assertRaisesRegex(ValueError, 'sum to 255'):
            remap_skin_weights(tiny_mesh(invalid_weights=True), 4, 2)
        with self.assertRaisesRegex(ValueError, 'beyond the skeleton'):
            remap_skin_weights(tiny_mesh(invalid_palette=True), 4, 2)

    def test_skin_is_structural_and_rest_pose_is_identity(self):
        raw = tiny_mesh()
        mesh = read_preview_mesh(raw, 'test.skinm')
        base = glb_bytes(mesh)
        rigged, details = skin_glb(base, raw, tiny_skeleton())
        document, binary = _unpack_glb(rigged)
        self.assertEqual(details['bone_count'], 2)
        self.assertEqual(details['vertex_count'], 4)
        self.assertEqual(len(document['skins']), 1)
        skin = document['skins'][0]
        self.assertEqual(len(skin['joints']), 2)
        self.assertEqual(document['nodes'][0]['skin'], 0)
        attributes = document['meshes'][0]['primitives'][0]['attributes']
        self.assertIn('JOINTS_0', attributes)
        self.assertIn('WEIGHTS_0', attributes)
        self.assertEqual(document['accessors'][attributes['JOINTS_0']]['componentType'], 5123)
        self.assertEqual(document['accessors'][attributes['WEIGHTS_0']]['normalized'], True)
        self.assertEqual(document['accessors'][skin['inverseBindMatrices']]['count'], 2)
        self.assertEqual(len(binary), document['buffers'][0]['byteLength'])
        joint_0 = document['nodes'][skin['joints'][0]]
        joint_1 = document['nodes'][skin['joints'][1]]
        self.assertEqual(joint_0['children'], [skin['joints'][1]])
        self.assertAlmostEqual(joint_1['matrix'][12], 1.0)
        # The world node transform multiplied by the inverse bind must be I.
        inverse_view = document['bufferViews'][document['accessors'][
            skin['inverseBindMatrices']]['bufferView']]
        rows = np.frombuffer(binary, dtype='<f4', offset=inverse_view['byteOffset'],
                             count=2*16).reshape(2, 16)
        data = [row.reshape((4, 4), order='F') for row in rows]
        first = np.array(joint_0['matrix']).reshape((4, 4), order='F')
        second = np.array(joint_1['matrix']).reshape((4, 4), order='F')
        self.assertTrue(np.allclose(first @ data[0], np.eye(4)))
        self.assertTrue(np.allclose(first @ second @ data[1], np.eye(4)))

    def test_workspace_export_with_matching_skin_is_rigged(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            workspace = path/'project'
            files = workspace/'files'
            files.mkdir(parents=True)
            (files/'test.skinm').write_bytes(tiny_mesh())
            (files/'test.skin').write_bytes(tiny_skeleton())
            mesh = read_preview_mesh((files/'test.skinm').read_bytes(), 'test.skinm')
            with patch('material_preview.load_preview_materials',
                       side_effect=ValueError('No DDS images')):
                glb, report = textured_model_from_workspace(
                    mesh, workspace, 'test.skinm')
            self.assertEqual(report['bone_count'], 2)
            self.assertIn('Rigged:', report['rig_status'])
            self.assertEqual(len(_unpack_glb(glb)[0]['skins']), 1)

    def test_loose_skin_companions(self):
        self.assertEqual(skin_companion_path(Path('abc.skinm')),
                         Path('abc.skin'))
        self.assertEqual(skin_companion_path(Path('abc.chrm')),
                         Path('abc.chr'))
        self.assertIsNone(skin_companion_path(Path('abc.cgf')))


if __name__ == '__main__':
    unittest.main()
