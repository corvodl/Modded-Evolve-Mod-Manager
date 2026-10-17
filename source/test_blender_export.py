"""Blender GLB exporter smoke/regression tests; synthetic meshes only."""
from __future__ import annotations
import json
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from model_preview import read_preview_mesh
from model_blender_export import glb_bytes, convert_model, convert_folder
from test_model_preview import test_mesh


def inspect_glb(blob):
    magic, version, total = struct.unpack_from('<4sII', blob)
    assert magic == b'glTF' and version == 2 and total == len(blob)
    length, chunk_type = struct.unpack_from('<I4s', blob, 12)
    assert chunk_type == b'JSON'
    document = json.loads(blob[20:20+length])
    offset = 20 + length
    binary_length, binary_type = struct.unpack_from('<I4s', blob, offset)
    assert binary_type == b'BIN\x00'
    assert len(blob) == offset + 8 + binary_length
    return document


class BlenderExportTests(unittest.TestCase):
    def test_valid_glb_with_uv_and_mesh(self):
        mesh = read_preview_mesh(test_mesh(), 'test.skinm')
        mesh = replace(mesh, uvs=((0.0, 0.0), (1.0, 0.0),
                                   (0.0, 1.0), (1.0, 1.0)))
        blob = glb_bytes(mesh, name='Test Character')
        document = inspect_glb(blob)
        self.assertEqual(document['asset']['version'], '2.0')
        self.assertEqual(document['accessors'][0]['count'], 4)
        self.assertEqual(document['meshes'][0]['name'], 'Test Character')
        self.assertEqual(document['meshes'][0]['primitives'][0]['mode'], 4)
        self.assertEqual(document['meshes'][0]['primitives'][0]['attributes']['POSITION'], 0)
        self.assertEqual(document['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0'], 1)
        self.assertEqual(document['buffers'][0]['byteLength'] % 4, 0)

    def test_copy_to_separate_blender_file(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'test.skinm'
            source.write_bytes(test_mesh())
            output = root / 'export.fbx'
            with patch('fbx_export_converter.convert_glb_to_fbx',
                       side_effect=lambda _blob, file, **_: Path(file).write_bytes(
                           b'Kaydara FBX Binary  TEST')):
                vertices, triangles = convert_model(source, output)
            self.assertEqual((vertices, triangles), (4, 4))
            self.assertEqual(source.read_bytes(), test_mesh())
            self.assertTrue(output.is_file())
            self.assertTrue(output.read_bytes().startswith(b'Kaydara FBX'))
            with self.assertRaisesRegex(ValueError, 'end with'):
                convert_model(source, root / 'bad.obj')

    def test_recursive_folder_export_and_skip(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            src, out = root / 'original', root / 'blender'
            (src / 'models').mkdir(parents=True)
            out.mkdir()
            (src/'models'/'render.skinm').write_bytes(test_mesh())
            (src/'models'/'not-render.chrm').write_bytes(b'native skeleton metadata')
            (src/'models'/'other.txt').write_text('never modified')
            with patch('fbx_export_converter.convert_glb_to_fbx',
                       side_effect=lambda _blob, file, **_: Path(file).write_bytes(
                           b'Kaydara FBX Binary  TEST')):
                results, errors = convert_folder(src, out)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertTrue((out/'models'/'render.fbx').is_file())
            self.assertEqual((src/'models'/'other.txt').read_text(), 'never modified')
            with self.assertRaisesRegex(ValueError, 'outside'):
                convert_folder(src, src)

    def test_gpu_first_ui_and_native_actions_preserved(self):
        code = Path(__file__).with_name('qt_workspace_editor.py').read_text(encoding='utf-8')
        for required in ('defaultGpuModelPreview', 'Export Current Model Only (.fbx)…',
                         'Export Model Folder', 'Export Native Model',
                         'Import Same-Layout Model', '_software_model_preview',
                         'QtGPUModelPanel'):
            with self.subTest(required=required):
                self.assertIn(required, code)


if __name__ == '__main__':
    unittest.main()
