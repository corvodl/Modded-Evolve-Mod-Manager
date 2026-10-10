"""Workspace context menus: safe edits, extraction and Windows explorer targets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from PIL import Image
from workspace_file_actions import (checked_path, replace_raw_file,
                                    extracted_original_backup, restore_extracted_original)
from workspace_editor import WorkspaceEditor
from dark_theme import apply_theme


class AtomicWorkspaceFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / 'project'
        self.root = self.workspace / 'files'
        self.root.mkdir(parents=True)
        self.filename = self.root / 'misc' / 'config.bin'
        self.filename.parent.mkdir()
        self.filename.write_bytes(b'original binary')
        self.original_hash = hashlib.sha256(b'original binary').hexdigest()
        self.candidate = Path(self.temp.name) / 'updated.bin'
        self.candidate.write_bytes(b'edited binary')

    def test_atomic_import_then_verified_original_restore(self):
        self.assertTrue(replace_raw_file(self.workspace, 'misc/config.bin', self.candidate, self.original_hash))
        self.assertEqual(self.filename.read_bytes(), b'edited binary')
        self.assertEqual(extracted_original_backup(self.workspace, 'misc/config.bin', self.original_hash).read_bytes(), b'original binary')
        changed_hash = hashlib.sha256(b'edited binary').hexdigest()
        self.assertTrue(restore_extracted_original(self.workspace, 'misc/config.bin', self.original_hash, changed_hash))
        self.assertEqual(self.filename.read_bytes(), b'original binary')
        self.assertEqual(len(list((self.workspace/'EditorBackups').rglob('config.bin'))), 2)
        self.assertFalse(restore_extracted_original(self.workspace, 'misc/config.bin', self.original_hash, self.original_hash))

    def test_restore_requires_exact_original_sha(self):
        replace_raw_file(self.workspace, 'misc/config.bin', self.candidate, self.original_hash)
        with self.assertRaisesRegex(ValueError, 'Original bytes are not'):
            restore_extracted_original(self.workspace, 'misc/config.bin', '0'*64, hashlib.sha256(b'edited binary').hexdigest())
        self.assertEqual(self.filename.read_bytes(), b'edited binary')

    def test_external_changes_and_symlink_path_blocked(self):
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            replace_raw_file(self.workspace, 'misc/config.bin', self.candidate, 'wrong-hash')
        self.assertFalse((self.workspace/'EditorBackups').exists())
        with self.assertRaises(ValueError):
            checked_path(self.workspace, '../outside.txt')
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('unsafe')
        shortcut = self.root / 'outside-link.txt'
        try:
            shortcut.symlink_to(outside)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError):
            checked_path(self.workspace, 'outside-link.txt')


class ContextMenuGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / 'project'
        folder = self.workspace / 'files' / 'textures'
        folder.mkdir(parents=True)
        (self.workspace/'files'/'misc').mkdir()
        (self.workspace/'files'/'models').mkdir()
        self.entries = {
            'misc/config.txt': b'initial text',
            'misc/payload.bin': b'original bin',
            'textures/stone.dds': self._dds(),
            'models/goliath.skinm': b'not a real mesh',
        }
        for rel, raw in self.entries.items():
            dest = self.workspace/'files'/rel
            dest.write_bytes(raw)
        (self.workspace/'.evolve-pak-workspace.json').write_text(json.dumps({
            'version':1,
            'entries':[{'path':rel,'format':'raw','sha256':hashlib.sha256(raw).hexdigest()}
                       for rel,raw in self.entries.items()]
        }))
        try:
            self.root=tk.Tk()
        except tk.TclError as exc:
            self.skipTest('No Tk display: '+str(exc))
        self.addCleanup(self.root.destroy)
        self.root.geometry('1100x750'); apply_theme(self.root)
        self.manager=SimpleNamespace(window=self.root, busy=False, t=lambda k:k)
        self.editor=WorkspaceEditor(self.manager,self.workspace)
        self.addCleanup(lambda:self.editor.window.destroy() if self.editor.window.winfo_exists() else None)
        self.root.update()

    @staticmethod
    def _dds():
        from io import BytesIO
        output=BytesIO()
        Image.new('RGBA',(8,8),(30,80,40,255)).save(output,format='DDS')
        return output.getvalue()

    def _tab(self, tab, rel):
        self.editor.tabs.select(self.editor.tab_frames[tab])
        self.root.update()
        tree=self.editor.trees[tab]
        tree.selection_set(self.editor.path_items[tab][rel])
        self.root.update()
        return tree

    @staticmethod
    def labels(menu):
        return [menu.entrycget(i,'label') for i in range(menu.index('end')+1)
                if menu.type(i)=='command']

    def test_file_type_menus_and_bindings(self):
        for tab,rel,options in (
            ('files','misc/config.txt',['Import / Replace File...','Export File...','Show in File Explorer','Copy Game File Path']),
            ('images','textures/stone.dds',['Import PNG as DDS...','Import Compatible DDS...','Export PNG...']),
            ('models','models/goliath.skinm',['Import Model (Experimental)...','Export Native Model...','Find Model Textures']),
        ):
            tree=self._tab(tab,rel)
            self.assertTrue(tree.bind('<Button-3>'))
            menu=self.editor._file_context_menu(tab,rel)
            try:
                names=self.labels(menu)
                for option in options:
                    self.assertIn(option,names)
                self.assertIn('Restore Extracted Original...',names)
                restore_index = next(i for i in range(menu.index('end') + 1)
                                     if menu.type(i) == 'command' and menu.entrycget(i, 'label') == 'Restore Extracted Original...')
                self.assertEqual(menu.entrycget(restore_index, 'state'), 'disabled')
            finally:
                menu.destroy()

    def test_copy_reveal_and_open_route_to_extracted_file(self):
        self._tab('files','misc/config.txt')
        self.editor.copy_game_path('misc/config.txt')
        self.assertEqual(self.editor.window.clipboard_get(),'misc\\config.txt')
        with patch('workspace_editor.os',SimpleNamespace(name='nt')),patch('workspace_editor.subprocess.Popen') as launch:
            self.editor.reveal_in_explorer('misc/config.txt')
            launch.assert_called_once_with(['explorer.exe','/select,',str((self.workspace/'files'/'misc'/'config.txt').resolve())])
        with patch('workspace_editor.os',SimpleNamespace(name='nt', startfile=lambda *_: None)) as fake_os:
            with patch.object(fake_os, 'startfile') as opening:
                self.editor.open_extracted_file('misc/config.txt')
                opening.assert_called_once_with(str((self.workspace/'files'/'misc'/'config.txt').resolve()))

    def test_context_import_text_and_restore(self):
        self._tab('files','misc/config.txt')
        replacement=Path(self.temp.name)/'replacement.txt'
        replacement.write_text('edited text')
        def unexpected_dialog(title, message, **kwargs):
            raise AssertionError(f'Unexpected blocking dialog in file replacement test: {title}: {message}')
        with patch('tkinter.filedialog.askopenfilename',return_value=str(replacement)), \
             patch('workspace_editor.messagebox.showerror',side_effect=unexpected_dialog), \
             patch('workspace_editor.messagebox.askyesnocancel',side_effect=unexpected_dialog):
            self.editor.import_generic_file()
            self.assertEqual((self.workspace/'files'/'misc'/'config.txt').read_text(),'edited text')
            self.assertIsNotNone(extracted_original_backup(self.workspace,'misc/config.txt',hashlib.sha256(b'initial text').hexdigest()))
            with patch('workspace_editor.messagebox.askyesno',return_value=True):
                self.editor.restore_original('misc/config.txt')
            self.assertEqual((self.workspace/'files'/'misc'/'config.txt').read_text(),'initial text')

    def test_context_preview_keeps_unsaved_changes_on_cancel(self):
        self._tab('files','misc/config.txt')
        self.editor.text.insert('end', ' editing')
        self.assertTrue(self.editor.dirty())
        with patch('workspace_editor.messagebox.askyesnocancel',return_value=None):
            self.editor.context_preview('misc/config.txt')
        self.assertTrue(self.editor.dirty())
        self.assertIn(' editing',self.editor.text.get('1.0','end-1c'))

    def test_folder_row_and_empty_space(self):
        tree=self._tab('files','misc/config.txt')
        node=next(key for key,path in self.editor.tree_paths['files'].items() if path=='misc')
        tree.see(node); self.root.update()
        box=tree.bbox(node)
        self.assertTrue(box)
        rel,is_folder=self.editor._clicked_entry('files',SimpleNamespace(num=3,y=box[1]+1))
        self.assertEqual((rel,is_folder),('misc',True))
        self.assertEqual(self.editor._clicked_entry('files',SimpleNamespace(num=3,y=99999)),(None,None))

    def test_cancel_unsaved_on_rightclick_keeps_previous_selection(self):
        tree=self._tab('files','misc/config.txt')
        self.editor.text.insert('end',' changed')
        self.root.update()
        self.assertTrue(self.editor.dirty())
        target=self.editor.path_items['files']['misc/payload.bin']
        tree.see(target); self.root.update()
        box=tree.bbox(target)
        if not box: self.skipTest('Could not scroll target into view')
        with patch('workspace_editor.messagebox.askyesnocancel',return_value=None), patch.object(tk.Menu,'tk_popup') as shown:
            event=SimpleNamespace(num=3,y=box[1]+2,x_root=10,y_root=10)
            self.editor.show_context_menu('files',event)
            shown.assert_not_called()
        self.assertEqual(self.editor.path,'misc/config.txt')


if __name__=='__main__':unittest.main()
