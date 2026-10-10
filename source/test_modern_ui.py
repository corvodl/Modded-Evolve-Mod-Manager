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
                                current_workspace='', output_pak='', update_status='Not checked').items():
            setattr(manager, name, tk.StringVar(master=self.root, value=value))
        manager.archive_entries = []
        manager.visible = []
        manager.batch_workspaces = {}
        manager.draw()
        self.root.update()
        try:
            self.assertFalse(manager.log_visible)
            self.assertEqual(manager.log_window.state(), 'withdrawn')
            self.assertEqual(len(manager.notebook.tabs()), 5)
            self.assertEqual([manager.notebook.tab(tab, 'text').strip()
                              for tab in manager.notebook.tabs()],
                             ['Instructions', 'Modding', 'Play & Restore', 'Settings', 'Credits'])
            self.assertEqual(manager.notebook.index('current'), 0)
            self.assertEqual(manager.version_label.cget('text'), 'v1.0.0')
            self.assertTrue(manager.header_icon)
            self.assertGreater(manager.header_icon.width(), 0)
            self.assertLessEqual(manager.header_icon.width(), 36)
            manager.notebook.select(manager.edit_tab)
            self.root.update()
            self.assertEqual(manager.notebook.index('current'), 1)
            manager.notebook.select(manager.instructions_tab)
            self.root.update()
            self.assertEqual(manager.notebook.index('current'), 0)
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

    def test_instructions_required_website_and_setup_actions(self):
        from pak_manager_gui import Manager
        manager = Manager.__new__(Manager)
        manager.window = self.root
        manager.copy, _ = load_text()
        parent = ttk.Frame(self.root)
        parent.pack(fill='both', expand=True)
        with patch.object(manager, 'initial_setup') as setup, \
             patch('pak_manager_gui.webbrowser.open') as website:
            manager.draw_instructions(parent)
            self.root.update()
            labels = []
            buttons = []
            def scan(node):
                for child in node.winfo_children():
                    if isinstance(child, ttk.Label):
                        labels.append(child.cget('text'))
                    if isinstance(child, ttk.Button):
                        buttons.append(child)
                    scan(child)
            scan(parent)
            self.assertTrue(any('modded-evolve.com' in text for text in labels))
            self.assertTrue(any('Restore Game Files' in text for text in labels))
            site = next(b for b in buttons if 'Website' in b.cget('text'))
            site.invoke()
            website.assert_called_once_with('https://modded-evolve.com/', new=2)
            setup_button = next(b for b in buttons if b.cget('text') == 'Set Up Manager')
            setup_button.invoke()
            setup.assert_called_once()

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
