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
        # Image-backed Tk/ttk surfaces are truly rounded, not merely recolored.
        self.assertEqual(style.layout('TNotebook.Tab')[0][0], 'RoundedTabSurface')
        self.assertEqual(style.layout('Accent.TButton')[0][0], 'AccentPill')
        self.assertEqual(style.layout('Quiet.TButton')[0][0], 'QuietPill')

    def test_compact_navigation_and_buttons_at_windows_window_size(self):
        """Regression: no huge 70px buttons from large image style elements."""
        style = ttk.Style(self.root)
        # Dynamic style geometry is more robust than comparing screenshot crops.
        examples = [
            ttk.Button(self.root, text='Help ?', style='Quiet.TButton'),
            ttk.Button(self.root, text='Set Up Manager'),
            ttk.Button(self.root, text='Get Evolve Stage 2', style='Accent.TButton'),
        ]
        for button in examples:
            button.pack()
        tabbook = ttk.Notebook(self.root)
        tabbook.pack(fill='x')
        for label in ('Instructions', 'Modding', 'Play & Restore',
                      'Settings', 'Credits'):
            tabbook.add(ttk.Frame(tabbook), text='  ' + label + '  ')
        self.root.update()
        for button in examples:
            self.assertLessEqual(
                button.winfo_height(), 48,
                f'{button.cget("text")} exceeds compact Windows button height')
        bbox = tabbook.bbox(0)
        self.assertTrue(bbox)
        self.assertLessEqual(bbox[3], 51, 'Navigation tabs must not be giant tiles')
        self.assertEqual(style.layout('TNotebook.Tab')[0][0], 'RoundedTabSurface')

    def test_rounded_card_reflows_and_has_real_ttk_content(self):
        from modern_surfaces import RoundedPanel
        card = RoundedPanel(self.root, padding=(18, 14), radius=17)
        card.pack(fill='x', padx=20)
        ttk.Label(card.content, text='Prepare an Evolve mod',
                  style='GuideStep.TLabel').pack(anchor='w')
        ttk.Label(card.content, text='Select an archive and then edit it.',
                  style='GuideBody.TLabel').pack(anchor='w')
        self.root.update()
        self.assertGreater(card.winfo_height(), 45)
        self.assertGreater(card.winfo_width(), 400)
        self.assertEqual(card.type(card._shape), 'image')
        self.assertTrue(card._background_image.width() > 400)
        self.assertEqual(card._background_image.height(), card.winfo_height())
        self.root.geometry('760x700')
        self.root.update()
        self.assertGreater(card.winfo_height(), 45)
        self.assertGreater(card.content.winfo_width(), 150)

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
            # Instructions uses the whole workspace until Menu is clicked.
            self.assertEqual(ttk.Style(self.root).layout('HiddenNav.TNotebook'),
                             [('Notebook.client', {'sticky': 'nswe'})])
            # Regression from the real v1.0.4 compiled screenshot: the page
            # appeared ~50px down because the old tab strip was still drawn.
            self.assertLessEqual(manager.instructions_tab.winfo_y(), 8,
                                 'Hidden section pages must not leave a tab row')
            self.assertFalse(manager.nav_open)
            self.assertFalse(manager.nav_panel.winfo_manager())
            self.assertIn('Menu', manager.menu_button.cget('text'))
            self.assertEqual(len(manager.nav_buttons), 5)
            manager.menu_button.invoke()
            self.root.update()
            self.assertTrue(manager.nav_open)
            self.assertEqual(manager.nav_panel.winfo_manager(), 'pack')
            self.assertLessEqual(manager.nav_panel.winfo_width(), 180)
            self.assertEqual(manager.nav_buttons[str(manager.instructions_tab)].cget('style'),
                             'NavActive.TButton')
            # Selecting a section must automatically collapse the drawer.
            manager.nav_buttons[str(manager.edit_tab)].invoke()
            self.root.update()
            self.assertFalse(manager.nav_open)
            self.assertFalse(manager.nav_panel.winfo_manager())
            self.assertEqual(manager.notebook.index('current'), 1)
            # Clicking the menu again, then Escape, also closes it.
            manager.menu_button.invoke()
            self.root.update()
            # Headless Windows jobs may not route generated key events to an
            # unfocused root window. Check the registered key binding and
            # exercise its dismiss handler without relying on OS focus.
            self.assertTrue(self.root.bind('<Escape>'))
            manager._dismiss_section_menu()
            self.root.update()
            self.assertFalse(manager.nav_open)
            manager.open_section(manager.instructions_tab)
            self.assertEqual(manager.notebook.index('current'), 0)
            # Navigation is now the only normal startup setup entry point.
            base = self.root.winfo_children()[0]
            header = base.winfo_children()[0]
            toolbar_buttons = [x.cget('text') for x in header.winfo_children()
                               if isinstance(x, ttk.Button)]
            self.assertNotIn('Set Up Manager', toolbar_buttons)
            self.assertEqual(manager.version_label.cget('text'), 'v1.0.4')
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
        with patch.object(manager, 'start_setup_from_instructions') as setup, \
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
            site = next(b for b in buttons if 'Evolve Stage 2' in b.cget('text'))
            site.invoke()
            website.assert_called_once_with('https://modded-evolve.com/', new=2)
            setup_button = next(b for b in buttons if b.cget('text') == 'Set Up Manager')
            setup_button.invoke()
            setup.assert_called_once()

    def test_first_boot_does_not_open_any_folder_selection_dialog(self):
        """Even exported but unconfigured bundles open at Instructions first."""
        from pak_manager_gui import Manager
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder, \
             patch.object(self.root, 'after', return_value='no-pending-callback'), \
             patch('pak_manager_gui.bind_home', return_value={'configured': False}), \
             patch('pak_manager_gui.settings_source', return_value=Path(folder)/'missing.json'), \
             patch.object(Manager, 'load_batch_mapping', return_value=None), \
             patch.object(Manager, 'reload_archives', return_value=None), \
             patch.object(Manager, 'update_launch_state', return_value=None), \
             patch.object(Manager, 'build_channel', return_value=''), \
             patch.object(Manager, 'initial_setup') as initial, \
             patch.object(Manager, 'configure_bundle') as configure, \
             patch('pak_manager_gui.filedialog.askdirectory') as folder_picker, \
             patch('pak_manager_gui.filedialog.askopenfilename') as file_picker:
            manager = Manager(self.root)
            self.root.update()
            self.assertEqual(manager.notebook.index('current'), 0)
            initial.assert_not_called()
            configure.assert_not_called()
            folder_picker.assert_not_called()
            file_picker.assert_not_called()
            self.assertFalse(manager.setup_is_ready())
            manager.log_window.destroy()

    def test_instructions_setup_switches_between_standard_and_bundle(self):
        from pak_manager_gui import Manager
        from pak_manager_gui import MARKER
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        manager = Manager.__new__(Manager)
        with TemporaryDirectory() as folder:
            fake_home = Path(folder)
            with patch('pak_manager_gui.APP_HOME', fake_home), \
                 patch.object(manager, 'configure_bundle') as configure, \
                 patch.object(manager, 'initial_setup') as regular:
                manager.start_setup_from_instructions()
                regular.assert_called_once()
                configure.assert_not_called()
                (fake_home/MARKER).write_text('{}', encoding='utf-8')
                manager.start_setup_from_instructions()
                configure.assert_called_once()

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
