"""Synthetic CryTek 0x745 preview tests; no proprietary model bytes."""
import struct
import unittest

from legacy_crytek_mesh import parse_crytek_chunks, read_crytek_geometry
from model_asset import inspect_model, validate_model_replacement
from model_preview import read_preview_mesh


def fixture(*, version=0x745, bad_index=False):
    chunks = [
        (0, 12, b''.join(struct.pack('<3f', *row) for row in
                        ((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)))),
        (2, 8, b''.join(struct.pack('<2f', *row) for row in
                       ((0.,0.),(1.,0.),(0.,1.)))),
        (5, 2, struct.pack('<3H', 0, 1, 33 if bad_index else 2)),
    ]
    offset, entries, body = 24 + 20 * len(chunks), [], []
    for cid, (typ, stride, payload) in enumerate(chunks, 1):
        size = 40 + len(payload)
        entries.append(struct.pack('<5I', 0xCCCC0016, 0x800,
                                   offset, cid, size))
        body.append(struct.pack('<4I', 0xCCCC0016, 0x800, offset, cid)
                    + struct.pack('<6I', 0, typ, len(payload)//stride,
                                  stride, 0, 0) + payload)
        offset += size
    return (b'CryTek\0\0'
            + struct.pack('<3I', 0xFFFF0000, version, 20)
            + struct.pack('<I', len(chunks)) + b''.join(entries)
            + b''.join(body))


class OldCryTekMeshTests(unittest.TestCase):
    def test_legacy_vertices_triangles_uvs_and_model_info(self):
        blob = fixture()
        filetype, version, _, _, entries = parse_crytek_chunks(blob)
        mesh = read_preview_mesh(blob, 'Objects/characters/archetypeFemale/ArchFemale.chr')
        self.assertEqual(filetype, 0xFFFF0000)
        self.assertEqual(version, 0x745)
        self.assertEqual(len(entries), 3)
        self.assertEqual(len(mesh.vertices), 3)
        self.assertEqual(mesh.triangles, ((0, 1, 2),))
        self.assertEqual(len(mesh.uvs), 3)
        self.assertEqual(inspect_model(blob).format_name, 'CryTek')
        self.assertEqual(inspect_model(blob).chunks, 3)

    def test_version_and_invalid_index_rejected(self):
        with self.assertRaisesRegex(ValueError, '0x745'):
            read_crytek_geometry(fixture(version=0x744))
        with self.assertRaisesRegex(ValueError, 'index outside vertex'):
            read_crytek_geometry(fixture(bad_index=True))

    def test_invalid_chunk_directory_rejected(self):
        blob = bytearray(fixture())
        struct.pack_into('<I', blob, 24 + 8, 9999999)
        with self.assertRaisesRegex(ValueError, 'out-of-bounds'):
            parse_crytek_chunks(blob)
        with self.assertRaises(ValueError):
            parse_crytek_chunks(fixture()[:-4])

    def test_replacement_check_preserves_legacy_stream_metadata(self):
        before = fixture()
        changed = bytearray(before)
        changed[-4:] = struct.pack('<2H', 1, 2)
        validate_model_replacement(before, bytes(changed))
        mutated_header = bytearray(before)
        # The first stream's element count occupies +24 of its chunk header.
        first_chunk = 24 + 20 * 3
        struct.pack_into('<I', mutated_header, first_chunk + 24, 5)
        with self.assertRaises(ValueError):
            validate_model_replacement(before, bytes(mutated_header))


if __name__ == '__main__':
    unittest.main()
