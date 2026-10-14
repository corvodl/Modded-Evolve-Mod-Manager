"""Synthetic .chr / CDF / LOD preview tests with no proprietary game bytes."""
from pathlib import Path
import struct
import tempfile
import unittest

from model_preview import find_preview_mesh


def valid_mesh():
    vertices = [(-1., -1., 0., 1.), (1., -1., 0., 1.),
                (0., 1., 0., 1.), (0., 0., 2., 1.)]
    packed = b''.join(struct.pack('<4e', *values) for values in vertices)
    indices = struct.pack('<12H', 0, 1, 2, 0, 1, 3, 1, 2, 3, 2, 0, 3)
    streams = (struct.pack('<6I', 0, 0, 4, 8, 0, 0) + packed,
               struct.pack('<6I', 0, 5, 12, 2, 0, 0) + indices)
    offset = 48
    table = []
    for index, chunk in enumerate(streams, 1):
        table.append(struct.pack('<4I', 0x08001016, index, len(chunk), offset))
        offset += len(chunk)
    return b'CrChF\x07\0\0' + struct.pack('<II', 2, 16) + b''.join(table) + b''.join(streams)


def older_crytek_model():
    return (b'CryTek\0\0' + struct.pack('<III', 0xffff0000, 1, 24)
            + bytes(4) + struct.pack('<I', 1)
            + struct.pack('<IIII', 1, 1, 20, 1))


class CharacterRenderCompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = 'Objects/characters/archetypeFemale/'
        self.selected = self.prefix + 'ArchFemale.chr'

    def create(self, virtual, data):
        path = self.root / 'files' / virtual
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_chr_with_render_streams_is_not_discarded(self):
        blob = valid_mesh()
        self.create(self.selected, blob)
        mesh = find_preview_mesh(self.root, self.selected, blob, index={})
        self.assertEqual(mesh.source_name, self.selected)
        self.assertEqual(len(mesh.triangles), 4)

    def test_chr_uses_exact_same_stem_lod_chr(self):
        self.create(self.selected, b'metadata without streams')
        companion = self.prefix + 'ArchFemale_LOD1.chr'
        self.create(companion, valid_mesh())
        # A different actor should never be used as a fallback.
        self.create(self.prefix + 'ArchMale_LOD0.chr', valid_mesh())
        mesh = find_preview_mesh(self.root, self.selected, b'metadata without streams',
                                 index={})
        self.assertEqual(mesh.source_name, companion)

    def test_chr_uses_cdf_skin_binding_without_guessing_name(self):
        blob = b'nonrenderable skeleton'
        self.create(self.selected, blob)
        cdf = self.prefix + 'ArchFemale.cdf'
        skin = self.prefix + 'TorsoAsset.skinm'
        self.create(cdf, (
            '<CharacterDefinition><Model File="' + self.selected
            + '"/><AttachmentList><Attachment Type="CA_SKIN" AName="body" '
            'Binding="' + skin[:-1] + '"/></AttachmentList>'
            '</CharacterDefinition>').encode('utf-8'))
        self.create(skin, valid_mesh())
        mesh = find_preview_mesh(self.root, self.selected, blob, index={})
        self.assertEqual(mesh.source_name, skin)

    def test_older_crytek_decoder_limitation_reported(self):
        legacy = older_crytek_model()
        self.create(self.selected, legacy)
        self.create(self.prefix + 'ArchFemale_LOD1.chr', legacy)
        with self.assertRaisesRegex(ValueError, 'older CryTek layout'):
            find_preview_mesh(self.root, self.selected, legacy, index={})

    def test_no_skinm_does_not_claim_skinm_required(self):
        self.create(self.selected, b'not a render mesh')
        with self.assertRaisesRegex(ValueError, 'No supported render geometry') as caught:
            find_preview_mesh(self.root, self.selected, b'not a render mesh', index={})
        self.assertIn('Do not assume', str(caught.exception))

    def test_ambiguous_indexed_companion_is_rejected(self):
        blob = b'opaque'
        self.create(self.selected, blob)
        a = self.root / 'a.chrm'
        b = self.root / 'b.chrm'
        a.write_bytes(valid_mesh())
        b.write_bytes(valid_mesh() + b'x')
        index = { (self.selected + 'm').casefold(): [
            {'relative': self.selected + 'm', 'file': a, 'workspace': None},
            {'relative': self.selected + 'm', 'file': b, 'workspace': None},
        ]}
        with self.assertRaisesRegex(ValueError, 'Conflicting model companions'):
            find_preview_mesh(self.root, self.selected, blob, index=index)


if __name__ == '__main__':
    unittest.main()
