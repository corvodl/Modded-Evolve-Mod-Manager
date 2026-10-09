"""Regression: PAK list resizes on Windows, even on small CI desktops.

Windows runners may clamp requested 900px windows to the virtual desktop work
area; compare actual geometry and always exercise manual sash movement.
"""
import tkinter as tk
from tkinter import ttk
import unittest
from dark_theme import apply_theme
from pak_manager_gui import Manager
from ui_copy import load_text


class PakListResizeTests(unittest.TestCase):
    def test_resize(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest('No graphical display: ' + str(error))
        try:
            # Leave room for Windows taskbar/title bar and UI chrome. A virtual
            # desktop can be only 768px tall; 900px gets silently clamped.
            usable = root.winfo_screenheight() - 110
            if usable < 570:
                self.skipTest('Graphical desktop too small for resize test')
            smaller = usable - 125
            larger = usable
            width = min(900, root.winfo_screenwidth() - 90)
            root.geometry(f'{width}x{smaller}+10+10')
            apply_theme(root)
            manager = Manager.__new__(Manager)
            manager.window = root
            manager.copy, _ = load_text()
            manager.search = tk.StringVar(master=root)
            manager.archive_count = tk.StringVar(master=root, value='2 PAK files')
            manager.archive_label = tk.StringVar(master=root, value='Game/libs.pak')
            manager.archive_entries = ['Game/libs.pak', 'Game/objects.pak']
            manager.visible = []
            frame = ttk.Frame(root)
            frame.pack(fill='both', expand=True)
            manager.draw_edit(frame)
            root.update()
            self.assertEqual(manager.edit_split.cget('orient'), 'vertical')
            self.assertEqual(len(manager.edit_split.panes()), 2)
            manager.search.set('object')
            self.assertEqual(manager.visible, ['Game/objects.pak'])
            before_height = manager.archives.winfo_height()
            actual_before = root.winfo_height()

            root.geometry(f'{width}x{larger}+10+10')
            root.update()
            actual_growth = root.winfo_height() - actual_before
            grown = manager.archives.winfo_height()
            # If the display manager constrains the height, only compare the
            # size that it actually allowed, not the requested +125 pixels.
            if actual_growth > 45:
                self.assertGreater(grown, before_height + max(10, actual_growth // 4),
                                   f'root grew {actual_growth}px, PAK list grew only {grown-before_height}px')

            # The key user feature is a movable divider; verify that even if
            # a CI window manager does not grant the resize request.
            x, y = manager.edit_split.sash_coord(0)
            manager.edit_split.sash_place(0, x, y - 55)
            root.update()
            self.assertLess(manager.archives.winfo_height(), grown - 20)
            self.assertTrue(manager.archives.winfo_ismapped())
            self.assertTrue(manager.archives.cget('yscrollcommand'))
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
