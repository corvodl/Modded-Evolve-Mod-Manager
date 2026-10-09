"""UI tab segregation and safe PNG-to-DDS conversion tests."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from io import BytesIO
from PIL import Image
from dds_png_import import encode_png_as_dds, compression_for_dds
from dds_texture import validate_replacement, parse_dds, replace_dds, preview_dds
from dds_streaming import replace_stream, inspect_stream, _mip_sizes
from workspace_editor import WorkspaceEditor


def fixture_dds(fmt='DXT5', size=(16, 16), mipmaps=3):
    """A fully valid original-style DDS with matching compressed mip payloads."""
    level0 = Image.new('RGBA' if fmt != 'BC5' else 'RGB', size, (200, 60, 20, 255) if fmt != 'BC5' else (90, 150, 235))
    header = None
    parts = []
    for i in range(mipmaps):
        mip = level0.resize((max(1, size[0] >> i), max(1, size[1] >> i)))
        fp = BytesIO()
        mip.save(fp, format='DDS', pixel_format=fmt)
        encoded = fp.getvalue()
        offset = 148 if encoded[84:88] == b'DX10' else 128
        if header is None:
            header = bytearray(encoded[:offset])
        parts.append(encoded[offset:])
    # Pillow's BC5 header has DX10 array_size=0. Change it to the valid count.
    if fmt == 'BC5':
        header[140:144] = (1).to_bytes(4, 'little')
    else:
        header[84:88] = b'DXT5' if fmt == 'DXT5' else b'DXT1'
    flags = int.from_bytes(header[8:12], 'little') | 0x20000
    header[8:12] = flags.to_bytes(4, 'little')
    header[28:32] = mipmaps.to_bytes(4, 'little')
    header[108:112] = (0x401008).to_bytes(4, 'little')
    return bytes(header) + b''.join(parts)


class EditorTabTests(unittest.TestCase):
    def test_categorization_and_stream_collapsing(self):
        entries = ['misc/config.xml', 'textures/skin.dds', 'textures/rock.dds.0',
                   'textures/rock.dds.1', 'textures/rock.dds.2', 'models/creature.cgf',
                   'models/creature.mtl', 'models/creature.skin']
        self.assertEqual(WorkspaceEditor.visible_entries(entries, 'files'), ['misc/config.xml', 'models/creature.mtl'])
        self.assertEqual(WorkspaceEditor.visible_entries(entries, 'images'), ['textures/rock.dds.0', 'textures/skin.dds'])
        self.assertEqual(WorkspaceEditor.visible_entries(entries, 'models'), ['models/creature.cgf', 'models/creature.skin'])
        self.assertEqual(WorkspaceEditor.visible_entries(entries, 'images', 'rock'), ['textures/rock.dds.0'])

    def test_png_import_preserves_dds_layout_and_mips(self):
        for fmt in ('DXT5', 'DXT1', 'BC5'):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as tmp:
                original = fixture_dds(fmt)
                self.assertEqual(parse_dds(original).mipmaps, 3)
                png = Path(tmp) / 'changed.png'
                Image.new('RGBA', (16, 16), (18, 26, 220, 255)).save(png)
                result = encode_png_as_dds(original, png)
                self.assertEqual(len(original), len(result))
                self.assertEqual(result[:parse_dds(original).data_offset], original[:parse_dds(original).data_offset])
                self.assertEqual(validate_replacement(original, result).mipmaps, 3)
                self.assertNotEqual(result, original)
                self.assertEqual(preview_dds(result).size, (16, 16))
                original_file = Path(tmp) / 'workspace' / 'files' / 'tex.dds'
                original_file.parent.mkdir(parents=True)
                original_file.write_bytes(original)
                replacement = Path(tmp) / 'replace.dds'
                replacement.write_bytes(result)
                replace_dds(Path(tmp) / 'workspace', 'tex.dds', replacement, hashlib.sha256(original).hexdigest())
                self.assertEqual(original_file.read_bytes(), result)
                self.assertEqual(len(list((Path(tmp) / 'workspace' / 'EditorBackups').rglob('tex.dds'))), 1)

    def test_reject_bad_png_dimensions_and_unsupported_dds(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / 'changed.png'
            Image.new('RGBA', (15, 16)).save(png)
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                encode_png_as_dds(fixture_dds(), png)
            Image.new('RGBA', (16, 16)).save(png)
            dds = bytearray(fixture_dds())
            dds[84:88] = b'ABCD'
            with self.assertRaisesRegex(ValueError, 'supports DXT1'):
                encode_png_as_dds(bytes(dds), png)

    def test_tk_tabs_isolate_buttons_and_selection(self):
        import tkinter as tk
        from types import SimpleNamespace
        import json
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest('No Tk display: ' + str(error))
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp)
                (ws/'files').mkdir()
                data = fixture_dds()
                names = ['foo.txt','a.dds','creature.skin']
                for name in names:
                    (ws/'files'/name).write_bytes(data if name == 'a.dds' else b'hello')
                (ws/'.evolve-pak-workspace.json').write_text(json.dumps({'entries':[{'path':n,'format':'raw'} for n in names]}))
                manager = SimpleNamespace(window=root, busy=False, t=lambda x:x)
                from dark_theme import apply_theme
                apply_theme(root)
                editor = WorkspaceEditor(manager, ws)
                root.update()
                self.assertEqual(editor.TABS, ('files','images','models'))
                self.assertEqual(len(editor.tabs.tabs()), 3)
                self.assertIn('a.dds', editor.path_items['images'])
                self.assertNotIn('a.dds', editor.path_items['files'])
                self.assertIn('creature.skin', editor.path_items['models'])
                self.assertEqual(editor.import_png_button.instate(['disabled']), True)
                editor.tabs.select(editor.tab_frames['images']);root.update()
                editor.trees['images'].selection_set(editor.path_items['images']['a.dds']);root.update()
                self.assertEqual(editor.view_mode,'dds')
                self.assertFalse(editor.import_png_button.instate(['disabled']))
                editor.tabs.select(editor.tab_frames['models']);root.update()
                self.assertTrue(editor.import_png_button.instate(['disabled']))
                self.assertEqual(editor.active_tab,'models')
                editor.window.destroy()
        finally:
            root.destroy()

if __name__=='__main__':unittest.main()
