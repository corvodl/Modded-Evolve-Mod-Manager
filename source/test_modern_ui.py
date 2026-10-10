"""Modern dark/red UI regressions: minimal tabs and on-demand instructions."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from dark_theme import apply_theme
from ui_copy import load_text
from ui_help import open_help_window


class ModernThemeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.geometry('1050x740+0+0')
        apply_theme(self.root)

    def test_red_black_and_readable_button_styles(self):
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup('TFrame', 'background'), '#0b0b0d')
        self.assertEqual(style.lookup('Brand.TLabel', 'foreground'), '#fa4557')
        self.assertEqual(style.lookup('Accent.TButton', 'foreground'), '#ffffff')
        self.assertNotEqual(style.lookup('Quiet.TButton', 'background'), '#ffffff')

    def test_help_is_on_demand_and_sections_are_navigable(self):
        popup = open_help_window(self.root, 'Info', [('Edit', 'Steps'), ('Play', 'Restore')], selected=1)
        try:
            self.root.update()
            tabs = [node for node in popup.winfo_children()[0].winfo_children()
                    if isinstance(node, ttk.Notebook)]
            self.assertEqual(len(tabs), 1)
            self.assertEqual(tabs[0].index('current'), 1)
            self.assertTrue(popup.winfo_exists())
            self.assertTrue(popup.bind('<Escape>'))
            close = next(child for child in popup.winfo_children()[0].winfo_children() if isinstance(child, ttk.Button))
            close.invoke()
            self.root.update()
            self.assertFalse(popup.winfo_exists())
        finally:
            if popup.winfo_exists(): popup.destroy()

    def test_manager_has_compact_main_tabs_and_help(self):
        from pak_manager_gui import Manager
        manager = Manager.__new__(Manager)
        manager.window = self.root
        manager.copy, _ = load_text()
        for name, value in dict(search='', archive_count='', archive_label='', status='Ready',
                                launch_state='Ready', stage='', swap='', projects='', filter='',
                                current_workspace='', output_pak='').items():
            setattr(manager, name, tk.StringVar(master=self.root, value=value))
        manager.archive_entries = []
        manager.visible = []
        manager.draw()
        self.root.update()
        try:
            self.assertFalse(manager.log_visible)
            self.assertEqual(manager.log_window.state(), 'withdrawn')
            self.assertEqual(len(manager.notebook.tabs()), 4)
            manager.show_help(0)
            self.root.update()
            help_windows = [win for win in self.root.winfo_children()
                            if isinstance(win, tk.Toplevel) and win is not manager.log_window]
            self.assertTrue(help_windows)
            for window in help_windows: window.destroy()
            self.assertTrue(manager.archives.cget('yscrollcommand'))
            self.assertEqual(len(manager.edit_split.panes()), 2)
        finally:
            manager.log_window.destroy()

    def test_editor_help_uses_current_tab(self):
        from workspace_editor import WorkspaceEditor
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws/'files'/'misc').mkdir(parents=True)
            (ws/'files'/'misc'/'x.txt').write_text('text')
            (ws/'.evolve-pak-workspace.json').write_text(json.dumps({
                'version':1, 'entries':[{'path':'misc/x.txt', 'format':'raw'}]
            }))
            manager = SimpleNamespace(window=self.root, busy=False, t=lambda name: load_text()[0].get(name, name))
            editor = WorkspaceEditor(manager, ws)
            try:
                self.root.update()
                editor.active_tab = 'images'
                with patch('workspace_editor.open_help_window') as help_call:
                    editor.show_help()
                    self.assertEqual(help_call.call_args.kwargs['selected'], 1)
            finally:
                editor.window.destroy()


if __name__ == '__main__':
    unittest.main()
