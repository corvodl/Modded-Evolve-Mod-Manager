"""The main PAK search panel expands and has an adjustable sash."""
import tkinter as tk
from tkinter import ttk
import unittest
from dark_theme import apply_theme
from pak_manager_gui import Manager
from ui_copy import load_text

class PakListResizeTests(unittest.TestCase):
    def test_resize(self):
        try: root=tk.Tk()
        except tk.TclError as e: self.skipTest(str(e))
        try:
            root.geometry('1150x720+0+0'); apply_theme(root)
            m=Manager.__new__(Manager)
            m.window=root; m.copy,_=load_text()
            m.search=tk.StringVar(master=root)
            m.archive_count=tk.StringVar(master=root,value='2 PAK files')
            m.archive_label=tk.StringVar(master=root,value='Game/libs.pak')
            m.archive_entries=['Game/libs.pak','Game/objects.pak']; m.visible=[]
            frame=ttk.Frame(root); frame.pack(fill='both',expand=True)
            m.draw_edit(frame); root.update()
            self.assertEqual(m.edit_split.cget('orient'),'vertical')
            self.assertEqual(len(m.edit_split.panes()),2)
            m.search.set('object')
            self.assertEqual(m.visible,['Game/objects.pak'])
            before=m.archives.winfo_height()
            root.geometry('1150x900+0+0');root.update()
            self.assertGreater(m.archives.winfo_height(),before+100)
            grown=m.archives.winfo_height()
            x,y=m.edit_split.sash_coord(0)
            m.edit_split.sash_place(0,x,y-90);root.update()
            self.assertLess(m.archives.winfo_height(),grown-45)
        finally:root.destroy()

if __name__=='__main__':unittest.main()
