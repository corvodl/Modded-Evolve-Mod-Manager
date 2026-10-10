"""Folder/filename/size browser and isolated loose-file editing regression tests."""
from pathlib import Path
import hashlib
import json
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from dark_theme import apply_theme
from loose_game_files import scan_loose_files, copy_to_project, verified_source
from loose_file_editor import save_loose_text
from pak_browser import format_size
from pak_manager_gui import Manager
from ui_copy import load_text


class LooseFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = self.root / 'EvolveGame'
        (self.game / 'Game' / 'Config').mkdir(parents=True)
        (self.game / 'bin64_SteamRetail').mkdir()
        (self.game / 'bin64_SteamRetail' / 'Evolve.exe').write_bytes(b'exe')
        (self.game / 'Game' / 'Config' / 'settings.cfg').write_bytes(b'render=1')
        (self.game / 'Game' / 'Config' / 'texture.dds.0').write_bytes(b'dds')
        (self.game / 'Game' / 'Config' / 'archive.pak').write_bytes(b'pak')
        (self.game / 'Game' / 'Config' / 'readme.exe').write_bytes(b'exe')

    def test_sizes_and_file_scan(self):
        self.assertEqual([format_size(n) for n in [0, 1024, 1234567, -1]],
                         ['0 B', '1.0 KB', '1.2 MB', '—'])
        files = scan_loose_files(self.game)
        self.assertEqual(sorted(x.relative for x in files),
                         ['Game/Config/settings.cfg', 'Game/Config/texture.dds.0'])

    def test_project_copy_does_not_change_game(self):
        original=self.game/'Game/Config/settings.cfg'
        projects=self.root/'Projects'
        copied=copy_to_project(self.game,'Game/Config/settings.cfg',projects)
        self.assertNotEqual(copied,original)
        self.assertEqual(copied.read_bytes(), b'render=1')
        save_loose_text(copied, projects/'LooseGameFileBackups', 'render=2',
                        hashlib.sha256(b'render=1').hexdigest())
        self.assertEqual(copied.read_bytes(), b'render=2')
        self.assertEqual(original.read_bytes(),b'render=1')
        self.assertEqual(copy_to_project(self.game,'Game/Config/settings.cfg',projects).read_bytes(),b'render=2')
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            save_loose_text(copied, projects/'LooseGameFileBackups','third','0'*64)
        self.assertEqual(len(list((projects/'LooseGameFileBackups').rglob('settings.cfg'))),1)

    def test_refuse_project_inside_installed_game(self):
        with self.assertRaisesRegex(ValueError, 'outside the installed game'):
            copy_to_project(self.game, 'Game/Config/settings.cfg', self.game/'MyMods')
        self.assertFalse((self.game/'MyMods').exists())

    def test_linked_and_outside_paths_are_rejected(self):
        for name in ['../outside.txt','Game/../outside.xml','C:/Windows/secret.txt']:
            with self.assertRaises(ValueError): verified_source(self.game,name)
        linked = self.game/'Game'/'linked.txt'
        try: linked.symlink_to(self.game/'Game/Config/settings.cfg')
        except (OSError,NotImplementedError): return
        self.assertNotIn('Game/linked.txt', [x.relative for x in scan_loose_files(self.game)])
        with self.assertRaisesRegex(ValueError, 'Linked'):
            verified_source(self.game,'Game/linked.txt')

    def test_bounded_scan(self):
        self.assertEqual(len(scan_loose_files(self.game,max_files=1)),1)


class LooseBrowserGuiTests(unittest.TestCase):
    def setUp(self):
        try: self.root=tk.Tk()
        except tk.TclError as exc: self.skipTest(str(exc))
        self.addCleanup(self.root.destroy)
        self.root.geometry('1080x750')
        apply_theme(self.root)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.game=self.base/'EvolveGame';(self.game/'bin64_SteamRetail').mkdir(parents=True)
        (self.game/'bin64_SteamRetail/Evolve.exe').write_bytes(b'exe')
        (self.game/'Game').mkdir()
        (self.game/'Game/settings.cfg').write_text('test = 1')
        self.stage=self.base/'stage';(self.stage/'paks'/'Game').mkdir(parents=True)
        (self.stage/'paks'/'Game/libs.pak').write_bytes(b'1234567890')
        (self.stage/'rekey_plan.json').write_text(json.dumps({'source_root':str(self.game),'blocked':[],
            'entries':[{'kind':'signed-encrypted','path':'Game/libs.pak'}]}))
        manager=Manager.__new__(Manager)
        manager.window=self.root; manager.copy,_=load_text()
        manager.archive_entries=[];manager.visible=[];manager.batch_workspaces={}
        manager.loose_entries=[];manager.loose_selected='';manager.game_root=None
        manager.batch_root=tk.StringVar(master=self.root)
        for name,value in {'search':'','archive_count':'','archive_label':'','stage':str(self.stage),
                           'swap':str(self.base/'swap'),'projects':str(self.base/'projects'),
                           'current_workspace':'','external_pak':'','output_pak':''}.items():
            setattr(manager,name,tk.StringVar(master=self.root,value=value))
        manager.write=lambda *_:None
        manager.busy=False
        self.manager=manager
        parent=ttk.Frame(self.root);parent.pack(fill='both',expand=True)
        manager.draw_edit(parent)
        manager.reload_archives()
        self.root.update()

    def test_order_size_and_selection_isolated_from_signed_paks(self):
        tree=self.manager.archives
        self.assertEqual(tuple(tree['columns']),('folder','description','name','project','size'))
        self.assertEqual(tree.item('Game/libs.pak')['values'], ['Game','Game Libraries','libs.pak','PAK','10 B'])
        loose='loose-file:Game/settings.cfg'
        self.assertEqual(tree.item(loose)['values'][:4], ['Game','Game file','settings.cfg','Game file'])
        self.assertEqual(tree.column('size')['anchor'],'e')
        tree.selection_set(loose);tree.focus(loose);self.manager.select_archive()
        self.assertEqual(self.manager.loose_selected,'Game/settings.cfg')
        self.assertEqual(self.manager.selected_archive_relatives(),[])
        with self.assertRaisesRegex(ValueError,'Choose a PAK'):
            self.manager.selected()
        tree.selection_set('Game/libs.pak');tree.focus('Game/libs.pak');self.manager.select_archive()
        self.assertEqual(self.manager.loose_selected,'')
        self.assertEqual(self.manager.selected_archive_relatives(),['Game/libs.pak'])

    def test_filter_and_safe_editor(self):
        m=self.manager
        m.file_filter.set('Game files');self.root.update()
        self.assertEqual(m.visible,[])
        self.assertEqual(m.archives.get_children(),('loose-file:Game/settings.cfg',))
        m.archives.selection_set('loose-file:Game/settings.cfg')
        m.archives.focus('loose-file:Game/settings.cfg');m.select_archive()
        m.editors=[]
        m.open_editable()
        self.assertEqual(len(m.editors),1)
        e=m.editors[0]
        try:
            self.root.update()
            self.assertIsNotNone(e.text)
            e.text.delete('1.0','end');e.text.insert('1.0','test = 2')
            e.save()
            self.assertEqual((self.base/'projects/LooseGameFiles/Game/settings.cfg').read_text(),'test = 2')
            self.assertEqual((self.game/'Game/settings.cfg').read_text(),'test = 1')
        finally: e.window.destroy()


if __name__=='__main__': unittest.main()
