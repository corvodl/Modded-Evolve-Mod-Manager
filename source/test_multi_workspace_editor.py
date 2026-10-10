"""Batch Edit Files lists all PAK workspaces without merging their editable content."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import tkinter as tk

from workspace_editor import WorkspaceEditor
from pak_manager_gui import Manager
from dark_theme import apply_theme


class BatchEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rootpath = Path(self.tmp.name)
        self.stage = self.rootpath/'stage'
        (self.stage/'paks'/'Game').mkdir(parents=True)
        self.batch = self.rootpath/'Projects'/'Batch_Assets'
        self.batch.mkdir(parents=True)
        self.mapping = {}
        self.names = ['Game/goliath_model.pak', 'Game/goliath_textures.pak']
        for idx, name in enumerate(self.names):
            folder = self.batch/'archives'/f'{idx+1:02d}_{Path(name).stem}'
            (folder/'files'/'characters').mkdir(parents=True)
            rel = 'characters/' + ('body.mtl' if idx==0 else 'diffuse.txt')
            (folder/'files'/rel).write_text(f'asset from {name}')
            (folder/'.evolve-pak-workspace.json').write_text(json.dumps({
                'version': 1, 'entries': [{'path': rel, 'format': 'raw'}],
                'source_pak': str(self.stage/'paks'/name)
            }))
            self.mapping[name] = folder
        self.batch.joinpath('.evolve-pak-batch.json').write_text(json.dumps({
            'version': 1, 'state': 'complete', 'stage': str(self.stage),
            'archives': [{'archive': name, 'workspace': 'archives/'+str(folder.name),
                          'source_sha256': 'test'} for name, folder in self.mapping.items()]
        }))

    def _root(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.geometry('1100x800')
        apply_theme(root)
        return root

    def test_editor_shows_both_archives_and_switches_isolated_files(self):
        root=self._root()
        state = {'saves': 0}
        manager=SimpleNamespace(window=root, busy=False, t=lambda k:k,
            current_workspace=tk.StringVar(root,value=str(self.mapping[self.names[0]])),
            archive_label=tk.StringVar(root,value=self.names[0]),
            output_pak=tk.StringVar(root,value='old output'),
            persist=lambda:state.__setitem__('saves',state['saves']+1))
        editor=WorkspaceEditor(manager,self.mapping[self.names[0]],archive_map=self.mapping)
        self.addCleanup(lambda:editor.window.destroy() if editor.window.winfo_exists() else None)
        root.update()
        self.assertEqual(editor.archive_list.size(),2)
        self.assertEqual(editor.archive_list.get(0),self.names[0])
        self.assertEqual(editor.archive_list.get(1),self.names[1])
        self.assertIn('characters/body.mtl',editor.path_items['files'])
        self.assertNotIn('characters/diffuse.txt',editor.path_items['files'])
        editor.archive_list.selection_clear(0,tk.END)
        editor.archive_list.selection_set(1)
        editor.select_archive()
        root.update()
        self.assertEqual(editor.workspace,self.mapping[self.names[1]])
        self.assertIn('characters/diffuse.txt',editor.path_items['files'])
        self.assertNotIn('characters/body.mtl',editor.path_items['files'])
        self.assertEqual(manager.current_workspace.get(),str(self.mapping[self.names[1]]))
        self.assertEqual(manager.archive_label.get(),self.names[1])
        self.assertEqual(manager.output_pak.get(),'')
        self.assertEqual(state['saves'],1)
        self.assertEqual((self.mapping[self.names[0]]/'files'/'characters/body.mtl').read_text(),
                         'asset from '+self.names[0])

    def test_cancel_unsaved_edits_keeps_current_archive(self):
        root=self._root()
        manager=SimpleNamespace(window=root,busy=False,t=lambda k:k,
            current_workspace=tk.StringVar(root,value=str(self.mapping[self.names[0]])),
            archive_label=tk.StringVar(root,value=self.names[0]),
            output_pak=tk.StringVar(root),persist=lambda:None)
        editor=WorkspaceEditor(manager,self.mapping[self.names[0]],archive_map=self.mapping)
        self.addCleanup(lambda:editor.window.destroy() if editor.window.winfo_exists() else None)
        root.update()
        editor.path='characters/body.mtl'
        editor.raw=b'old'
        editor.view_mode='text'
        editor.loaded_text='old'
        editor.text.configure(state='normal')
        editor.text.delete('1.0','end')
        editor.text.insert('1.0','unsaved')
        with patch('workspace_editor.messagebox.askyesnocancel',return_value=None) as confirmation:
            self.assertFalse(editor.switch_archive(self.names[1]))
            confirmation.assert_called_once()
        self.assertEqual(editor.workspace,self.mapping[self.names[0]])
        self.assertEqual(editor.archive_list.curselection(),(0,))
        self.assertEqual(editor.text.get('1.0','end-1c'),'unsaved')

    def test_manager_map_reports_missing_batch_unpacks(self):
        root=self._root()
        manager=Manager.__new__(Manager)
        manager.window=root
        manager.stage=tk.StringVar(root,value=str(self.stage))
        manager.batch_root=tk.StringVar(root,value=str(self.batch))
        manager.current_workspace=tk.StringVar(root,value=str(self.mapping[self.names[0]]))
        manager.archive_label=tk.StringVar(root,value=self.names[0])
        manager.batch_workspaces={}
        manager.visible=self.names[:]
        manager.archives=tk.Listbox(root,selectmode=tk.EXTENDED)
        manager.archives.insert(tk.END,*self.names)
        manager.archives.selection_set(0,1)
        with self.assertRaisesRegex(ValueError,'Unpack Selected PAKs'):
            # A second PAK selected without a valid completed batch must
            # produce guidance, not silently open only the first PAK.
            manager.batch_root.set('')
            manager.editor_archive_map()
        manager.batch_root.set(str(self.batch))
        # Full batch loader validates extracted workspace manifests and source
        # hashes; use a controlled mapping to isolate the UI routing here.
        with patch.object(manager,'load_batch_mapping',side_effect=lambda: setattr(manager,'batch_workspaces',self.mapping)):
            mapping=manager.editor_archive_map()
        self.assertEqual(set(mapping),set(self.names))

if __name__=='__main__': unittest.main()
