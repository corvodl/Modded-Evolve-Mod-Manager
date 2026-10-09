"""Regression tests: file browser tree must be readable in dark mode."""
import tkinter as tk
import unittest
from tkinter import ttk
from dark_theme import apply_theme


class TreeviewThemeTests(unittest.TestCase):
    def test_theme_tree_foreground_background_and_selection(self):
        try:
            root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"No graphical display for Tk: {e}")
        try:
            root.withdraw()
            apply_theme(root)
            tree = ttk.Treeview(root, show="tree", selectmode="browse")
            tree.insert("", "end", text="libs/Characters/Example.dds")
            style = ttk.Style(root)
            self.assertEqual(style.lookup("Treeview", "background"), "#1b1b20")
            self.assertEqual(style.lookup("Treeview", "fieldbackground"), "#1b1b20")
            self.assertEqual(style.lookup("Treeview", "foreground"), "#f2f2f4")
            background_map = style.map("Treeview", "background")
            foreground_map = style.map("Treeview", "foreground")
            self.assertIn(("selected", "#40576d"), background_map)
            self.assertIn(("selected", "#ffffff"), foreground_map)
            self.assertEqual(tree.cget("style"), "")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
