"""Cross-project DDS discovery and Credits tab tests; no actual game assets."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from material_preview import load_preview_materials
from multi_pak_assets import project_asset_index, model_material_links
from model_preview import find_preview_mesh
from test_model_preview import test_mesh
from test_editor_tabs_png import fixture_dds
from ui_copy import load_text


MODEL = 'characters/monsters/goliath/goliath1_lod1.skinm'
SKIN = 'characters/monsters/goliath/goliath1.skin'
MTL = 'characters/monsters/goliath/goliath1.mtl'
TEXTURE = 'characters/monsters/goliath/textures/goliath1_diff.dds.0'
REF = 'characters/monsters/goliath/textures/goliath1_diff.tif'


class CrossProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stage = self.base / 'setup' / 'staged'
        self.projects = self.base / 'Data' / 'Projects'
        self.stage_paks = self.stage / 'paks' / 'Game'
        self.stage_paks.mkdir(parents=True)
        self.projects.mkdir(parents=True)
        key = self.stage / 'mykeys' / 'public_key.bin'
        key.parent.mkdir(parents=True)
        key.write_bytes(b'locally generated fixture key')
        self.key_hash = hashlib.sha256(key.read_bytes()).hexdigest()
        self.models = self._workspace('models', 'objects.pak')
        self.textures = self._workspace('textures', 'textures.pak')
        self._write(self.models, MODEL, test_mesh())
        self._write(self.models, MTL,
            ('<Material><SubMaterials><Material Name="Goliath Body" Shader="Illum">'
             '<Textures><Texture Map="Diffuse" File="' + REF + '"/>'
             '</Textures></Material></SubMaterials></Material>').encode())
        self._write(self.textures, TEXTURE, fixture_dds('DXT5', (16, 16), 1))

    def _workspace(self, folder, pak):
        source = self.stage_paks / pak
        source.write_bytes(pak.encode())
        ws = self.projects / folder
        (ws / 'files').mkdir(parents=True)
        (ws / 'files' / '_test_readme.txt').write_text('fixture')
        (ws / '.evolve-pak-workspace.json').write_text(json.dumps({
            'version': 1, 'source_pak': str(source), 'source_sha256': 'fixture',
            'public_key_sha256': self.key_hash,
            'entries': [{'path': '_test_readme.txt', 'format': 'raw'}]}))
        return ws

    def _write(self, ws, name, contents):
        p = ws / 'files' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contents)
        manifest = ws / '.evolve-pak-workspace.json'
        data = json.loads(manifest.read_text())
        data['entries'].append({'path': name, 'format': 'raw'})
        manifest.write_text(json.dumps(data))
        return p

    def test_separately_unpacked_paks_resolve_diffuse_without_folder_prompt(self):
        materials = load_preview_materials(self.models, MODEL,
                                            projects_root=self.projects, stage_root=self.stage)
        self.assertEqual(materials.count, 1)
        self.assertIn('goliath1.mtl', materials.source)
        self.assertEqual(materials.textures[0].size, (16, 16))
        links = model_material_links(self.models, MODEL,
                                     projects_root=self.projects, stage_root=self.stage)
        self.assertEqual(links['status'], 'found')
        self.assertEqual(links['textures'][0]['status'], 'found')
        self.assertIn(TEXTURE, links['textures'][0]['paths'])

    def test_material_itself_can_reside_in_second_pak(self):
        material = self.models / 'files' / MTL
        raw = material.read_bytes()
        material.unlink()
        data_file = self.models / '.evolve-pak-workspace.json'
        data = json.loads(data_file.read_text())
        data['entries'] = [entry for entry in data['entries'] if entry['path'] != MTL]
        data_file.write_text(json.dumps(data))
        self._write(self.textures, MTL, raw)
        got = load_preview_materials(self.models, MODEL,
                                     projects_root=self.projects, stage_root=self.stage)
        self.assertEqual(got.count, 1)

    def test_old_stage_or_signing_key_does_not_leak_textures(self):
        data_file = self.textures / '.evolve-pak-workspace.json'
        data = json.loads(data_file.read_text())
        data['public_key_sha256'] = 'different signing key'
        data_file.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'No diffuse DDS'):
            load_preview_materials(self.models, MODEL, projects_root=self.projects,
                                   stage_root=self.stage)
        # Project from an external/non-staged PAK is likewise excluded.
        data['public_key_sha256'] = self.key_hash
        data['source_pak'] = str(self.base / 'old' / 'textures.pak')
        data_file.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'No diffuse DDS'):
            load_preview_materials(self.models, MODEL, projects_root=self.projects,
                                   stage_root=self.stage)

    def test_duplicate_paths_rejected_without_guessing(self):
        other = self._workspace('duplicated', 'duplicates.pak')
        self._write(other, TEXTURE, fixture_dds('DXT5', (16, 16), 1))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            load_preview_materials(self.models, MODEL,
                                   projects_root=self.projects, stage_root=self.stage)

    def test_other_project_companion_mesh(self):
        self._write(self.models, SKIN, b'metadata only')
        companion = self._write(self.textures, SKIN + 'm', test_mesh())
        mesh = find_preview_mesh(self.models, SKIN, b'metadata only',
                                 projects_root=self.projects, stage_root=self.stage)
        self.assertEqual(len(mesh.vertices), 4)
        self.assertTrue(companion.is_file())

    def test_symlink_texture_not_indexed(self):
        outside = self.base / 'outside.dds.0'
        outside.write_bytes(fixture_dds('DXT5', (16, 16), 1))
        path = self.textures / 'files' / TEXTURE
        path.unlink()
        try:
            path.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest('Symlinks unavailable: ' + str(error))
        with self.assertRaisesRegex(ValueError, 'No diffuse DDS'):
            load_preview_materials(self.models, MODEL,
                                   projects_root=self.projects, stage_root=self.stage)

    def test_outside_projects_does_not_auto_link(self):
        index = project_asset_index(self.stage, self.projects, self.stage)
        self.assertIsNone(index)
        with self.assertRaisesRegex(ValueError, 'No diffuse DDS'):
            load_preview_materials(self.models, MODEL)


class CreditsUITests(unittest.TestCase):
    def test_credits_labels_and_installer_links(self):
        import tkinter as tk
        from tkinter import ttk
        from pak_manager_gui import Manager
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        try:
            manager = Manager.__new__(Manager)
            manager.window = root
            manager.copy, _ = load_text()
            box = ttk.Frame(root)
            box.pack(fill='both', expand=True)
            manager.draw_credits(box)
            labels = []
            commands = []
            def collect(widget):
                if isinstance(widget, (ttk.Label, ttk.Button)):
                    labels.append(widget.cget('text'))
                    if isinstance(widget, ttk.Button):
                        commands.append(widget)
                for child in widget.winfo_children():
                    collect(child)
            collect(box)
            self.assertTrue(any('@CorvoDL' in s for s in labels))
            self.assertTrue(any('github.com/corvodl/Modded-Evolve-Mod-Manager' in s for s in labels))
            self.assertTrue(any('modded-evolve.com' in s for s in labels))
            with patch('pak_manager_gui.webbrowser.open') as open_url:
                for button in commands:button.invoke()
            self.assertEqual(open_url.call_count, 2)
            urls = [call.args[0] for call in open_url.call_args_list]
            self.assertEqual(urls, ['https://github.com/corvodl/Modded-Evolve-Mod-Manager',
                                    'https://modded-evolve.com/'])
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
