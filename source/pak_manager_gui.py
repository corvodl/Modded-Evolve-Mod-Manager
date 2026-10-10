#!/usr/bin/env python3
"""Evolve Stage 2 Mod Manager: editable CryPAK workspace + verified v5 Frida launch.

Separate GUI from existing swap state: NO auto-overwrite of original scripts,
backups, staged PAKs, or the launcher's journal during installation.
"""
from __future__ import annotations
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from launch_integration import (
    add_missing_scripts, launcher_commands, missing_scripts,
    package_status, restore_command, state_status,
)
from universal_stage import allowed_paks
from app_runtime import APP_HOME, DATA_HOME, SETTINGS_FILE, settings_source, save_settings, worker_command
from dark_theme import apply_theme
from ui_help import open_help_window
from app_updater import discover as discover_update, download_and_prepare, launch_apply, installed_commit, report_startup_ready
from ui_copy import load_text
from workspace_editor import WorkspaceEditor
from portable_bundle import bind_home, MARKER
from old_prepared import scan_prepared
from orphaned_swap_recovery import plan_recovery
from multi_pak_assets import load_collection, create_batch
from pak_browser import describe_archive, archive_description, matching_archives, import_target, format_size
from loose_game_files import scan_loose_files, LooseFile
from loose_file_editor import LooseFileEditor

ROOT = Path(__file__).resolve().parent
DEFAULT_STAGE = str(DATA_HOME/'Setup'/'staged')
DEFAULT_SWAP = str(DATA_HOME/'Setup'/'swap')
SETTINGS = SETTINGS_FILE


def open_folder(path):
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        subprocess.Popen(['xdg-open',str(path)])


class Manager:
    def __init__(self, root):
        self.window = root
        apply_theme(root)
        try:
            self.app_icon = tk.PhotoImage(file=str(ROOT/'assets'/'hunt.png'))
            self.window.iconphoto(True, self.app_icon)
            if os.name == 'nt': self.window.iconbitmap(str(ROOT/'assets'/'hunt.ico'))
        except tk.TclError:
            pass
        self.copy, self.ui_warning = load_text()
        self.window.title(self.t('window_title'))
        self.window.geometry('1040x710')
        self.window.minsize(860,620)
        self.events = queue.Queue()
        self.busy = False
        self.editors = []
        self.archive_entries = []
        self.loose_entries = []
        self.loose_selected = ''
        self.game_root = None
        self.batch_workspaces = {}
        self.batch_root = tk.StringVar()
        self.visible = []
        self.stage = tk.StringVar(value=DEFAULT_STAGE)
        self.swap = tk.StringVar(value=DEFAULT_SWAP)
        self.projects = tk.StringVar(value=str(DATA_HOME/'Projects'))
        self.current_workspace = tk.StringVar()
        self.output_pak = tk.StringVar()
        self.archive_label = tk.StringVar()
        self.external_pak = tk.StringVar()
        self.search = tk.StringVar()
        self.filter = tk.StringVar()
        self.status = tk.StringVar(value=self.t('status_welcome'))
        self.launch_state = tk.StringVar(value=self.t('status_launch_check'))
        self.archive_count = tk.StringVar()
        self.update_status = tk.StringVar(value='Updates: not checked')
        self.available_update = None
        self.update_in_progress = False
        self._update_prompt_pending = False
        self._update_progress_window = None
        self.bundle_error = ''
        self.bundle = None
        self.read_settings()
        self.load_batch_mapping()
        self.draw()
        # Detailed activity is optional; automatic operation logs still open when work starts.
        if self.ui_warning:self.write('UI TEXT: '+self.ui_warning+'\n')
        self.reload_archives()
        self.update_launch_state()
        self.window.after(120, self.poll)
        self.window.protocol('WM_DELETE_WINDOW',self.on_close)
        if self.build_channel() == 'main':
            self.window.after(2500, lambda: self.check_updates(silent=True))
        if self.bundle_error:self.window.after(300,lambda:self.fail(RuntimeError(self.bundle_error)))
        elif self.bundle and not self.bundle.get('configured'):self.window.after(300,self.configure_bundle)
        elif not (Path(self.stage.get())/'stage_status.json').is_file():self.window.after(300,self.offer_initial_setup)

    def read_settings(self):
        try:self.bundle=bind_home(APP_HOME)
        except Exception as e:self.bundle_error=str(e)
        source=settings_source()
        if source.is_file():
            try:
                data=json.loads(source.read_text(encoding='utf-8'))
                for name in ('stage','swap','projects','current_workspace','output_pak','archive_label','batch_root'):
                    if name in data and isinstance(data[name],str):
                        getattr(self,name).set(data[name])
            except (OSError, ValueError):
                pass

    def persist(self):
        data={name:getattr(self,name).get() for name in (
            'stage','swap','projects','current_workspace','output_pak','archive_label','batch_root')}
        save_settings(data)

    def t(self, name):
        """Lookup a developer-configured label (unknown keys remain visible)."""
        return self.copy.get(name, name.replace('_', ' ').capitalize())

    @staticmethod
    def build_channel():
        try:
            return (APP_HOME / 'BUILD_CHANNEL.txt').read_text(encoding='ascii').strip().lower()
        except OSError:
            return ''

    def check_updates(self, silent=False):
        if self.update_in_progress or self.busy:
            if not silent: messagebox.showinfo('Updates', 'Finish the current operation first.', parent=self.window)
            return
        if self.build_channel() != 'main' or not (APP_HOME / 'EvolveModManager.exe').is_file():
            self.update_status.set('Automatic updates are available in the main Windows release.')
            if not silent:messagebox.showinfo('Updates', self.update_status.get(), parent=self.window)
            return
        self.update_in_progress = True
        self.update_status.set('Checking for updates…')
        self.update_button.configure(state='disabled')
        def background():
            try: self.events.put(('update-check', (discover_update(installed_commit(APP_HOME)), None, silent)))
            except Exception as exc: self.events.put(('update-check', (None, str(exc), silent)))
        threading.Thread(target=background, daemon=True).start()

    def on_update_check(self, candidate, error, silent):
        self.update_in_progress = False
        self.update_button.configure(state='normal')
        self.available_update = candidate
        if error:
            self.update_status.set('Update check unavailable')
            if not silent:messagebox.showwarning('Updates', f'Could not check GitHub: {error}', parent=self.window)
        elif candidate is None:
            self.update_status.set('No verified newer build available')
            if not silent: messagebox.showinfo('Updates', 'You have the latest released build, or the new build is still being verified.', parent=self.window)
        else:
            self.update_status.set('Update available: ' + candidate.version)
            # A startup check is quiet only when no update exists. Prompt once
            # after discovery, outside the event-polling callback.
            if silent:
                self._update_prompt_pending = True
                self.window.after(100, self._offer_pending_update)
            else:
                self.window.after(100, self.offer_update)

    def _offer_pending_update(self):
        if not self._update_prompt_pending or self.update_in_progress:
            return
        if self.busy:
            # Leave the prompt pending until the active task finishes.
            return
        self._update_prompt_pending = False
        self.offer_update()

    def _open_update_progress(self, version):
        popup = tk.Toplevel(self.window)
        popup.title('Updating Evolve Mod Manager')
        popup.geometry('465x172')
        popup.resizable(False, False)
        popup.configure(background='#0b0b0d')
        popup.transient(self.window)
        popup.protocol('WM_DELETE_WINDOW', lambda: None)
        frame = ttk.Frame(popup, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Installing ' + version, style='Section.TLabel').pack(anchor='w')
        self._update_stage = tk.StringVar(master=popup, value='Connecting to GitHub...')
        ttk.Label(frame, textvariable=self._update_stage, style='Muted.TLabel').pack(anchor='w', pady=(8, 10))
        self._update_bar = ttk.Progressbar(frame, mode='determinate', maximum=100, value=0)
        self._update_bar.pack(fill='x')
        self._update_percentage = tk.StringVar(master=popup, value='0%')
        ttk.Label(frame, textvariable=self._update_percentage, style='AccentText.TLabel').pack(anchor='e', pady=(4, 0))
        popup.lift()
        self._update_progress_window = popup

    def _set_update_progress(self, stage, completed, total):
        popup = self._update_progress_window
        if popup is None or not popup.winfo_exists():
            return
        fraction = max(0.0, min(1.0, completed / max(1, total)))
        # Download 0-65%, extraction 70-99%, install in separate process.
        if stage == 'Downloading':
            value = round(65 * fraction)
            label = 'Downloading update...'
        elif stage == 'Verifying':
            value = 68
            label = 'Verifying download...'
        elif stage == 'Extracting':
            value = 70 + round(29 * fraction)
            label = 'Preparing application files...'
        else:
            value = 100
            label = 'Ready to install. Restarting...'
        self._update_bar.configure(value=value)
        self._update_stage.set(label)
        self._update_percentage.set(f'{value}%')

    def _close_update_progress(self):
        popup = self._update_progress_window
        self._update_progress_window = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def offer_update(self):
        candidate = self.available_update
        if candidate is None or self.update_in_progress: return
        if self.busy:
            messagebox.showinfo('Updates', 'Finish your current operation before updating.', parent=self.window)
            return
        if not messagebox.askyesno('Update available',
                f'{candidate.version} is ready to install.\n\n'
                'The manager will download and verify the release, then restart. '
                'Your Data folder (projects, keys and backups) is preserved.\n\n'
                'Update now?', parent=self.window): return
        self.update_in_progress = True
        self.update_status.set('Downloading verified update…')
        self.update_button.configure(state='disabled')
        self._open_update_progress(candidate.version)
        def progress(stage, completed, total):
            self.events.put(('update-progress', (stage, completed, total)))
        def download():
            try: self.events.put(('update-download', (candidate, download_and_prepare(candidate, progress=progress), None)))
            except Exception as exc: self.events.put(('update-download', (candidate, None, str(exc))))
        threading.Thread(target=download, daemon=True).start()

    def on_update_download(self, candidate, staged, error):
        self.update_in_progress = False
        self.update_button.configure(state='normal')
        if error:
            self._close_update_progress()
            self.update_status.set('Update download failed')
            messagebox.showerror('Update failed', str(error) + '\n\nThe current installation is unchanged.', parent=self.window)
            return
        if self.busy or not self.flush_editors():
            self._close_update_progress()
            self.update_status.set('Update downloaded; install postponed')
            messagebox.showinfo('Update postponed', 'Finish operations and save edits first. Recheck updates when ready.', parent=self.window)
            return
        try:
            self.persist()
            log = launch_apply(candidate, staged, APP_HOME, os.getpid())
        except Exception as exc:
            self._close_update_progress()
            self.update_status.set('Could not start updater')
            messagebox.showerror('Update failed', f'{exc}\n\nThe current installation is unchanged.', parent=self.window)
            return
        self._close_update_progress()
        # The detached Windows updater waits for this process to exit and then
        # performs the replacement. Never edit running EXEs or user Data here.
        self.window.destroy()

    def draw(self):
        root = self.window
        base = ttk.Frame(root, padding=(18, 15, 18, 12))
        base.pack(fill='both', expand=True)

        header = ttk.Frame(base)
        header.pack(fill='x', pady=(0, 13))
        brand = ttk.Frame(header)
        brand.pack(side='left', fill='x', expand=True)
        ttk.Label(brand, text=self.t('header'), style='Brand.TLabel').pack(anchor='w')
        ttk.Label(brand, text=self.t('subtitle'), style='Muted.TLabel').pack(anchor='w', pady=(2, 0))
        ttk.Button(header, text=self.t('setup_button'), style='Accent.TButton',
                   command=self.initial_setup).pack(side='right', padx=(8, 0))
        self.log_button = ttk.Button(header, text=self.t('details_show'),
                                     style='Quiet.TButton', command=self.toggle_log)
        self.log_button.pack(side='right', padx=(8, 0))
        ttk.Button(header, text=self.t('help_button'), style='Quiet.TButton',
                   command=self.show_help).pack(side='right')

        notebook = ttk.Notebook(base)
        notebook.pack(fill='both', expand=True)
        self.edit_tab = ttk.Frame(notebook, padding=12)
        self.play_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        self.credits_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.edit_tab, text='  ' + self.t('tab_edit') + '  ')
        notebook.add(self.play_tab, text='  ' + self.t('tab_play') + '  ')
        notebook.add(self.settings_tab, text='  ' + self.t('tab_settings') + '  ')
        notebook.add(self.credits_tab, text='  ' + self.t('tab_credits') + '  ')
        self.notebook = notebook
        self.draw_edit(self.edit_tab)
        self.draw_play(self.play_tab)
        self.draw_settings(self.settings_tab)
        self.draw_credits(self.credits_tab)

        self.log_visible = False
        self.log_window = tk.Toplevel(root)
        self.log_window.withdraw()
        self.log_window.configure(background='#0b0b0d')
        self.log_window.title('Activity | ' + self.t('window_title'))
        self.log_window.geometry('850x340')
        self.log_window.minsize(550, 220)
        self.log_window.protocol('WM_DELETE_WINDOW', lambda: self.toggle_log(show=False))
        self.log_frame = ttk.Frame(self.log_window, padding=12)
        self.log_frame.pack(fill='both', expand=True)
        self.log = tk.Text(self.log_frame, height=6, font=('Consolas', 9), wrap='word',
                           relief='flat', borderwidth=0)
        self.log.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(self.log_frame, orient='vertical', command=self.log.yview)
        scroll.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=scroll.set)

        ttk.Separator(base).pack(fill='x', pady=(10, 8))
        bottom = ttk.Frame(base)
        bottom.pack(fill='x')
        ttk.Label(bottom, textvariable=self.status, style='Status.TLabel').pack(side='left', fill='x', expand=True)
        ttk.Label(bottom, text=self.t('footer_note'), style='AccentText.TLabel').pack(side='right')
        self.write('Manager opened. No installed game files were changed.\n')

    def show_help(self, section=None):
        sections = [
            (self.t('help_mod_title'), self.t('help_mod_body')),
            (self.t('help_play_title'), self.t('help_play_body')),
            (self.t('help_setup_title'), self.t('help_setup_body')),
            (self.t('help_safety_title'), self.t('help_safety_body')),
        ]
        if section is None:
            current = self.notebook.select()
            section = {str(self.edit_tab): 0, str(self.play_tab): 1,
                       str(self.settings_tab): 2, str(self.credits_tab): 2}.get(current, 0)
        open_help_window(self.window, self.t('help_title'), sections, selected=section)

    def draw_credits(self, parent):
        github = 'https://github.com/corvodl/Modded-Evolve-Mod-Manager'
        website = 'https://modded-evolve.com/'
        ttk.Label(parent, text=self.t('credits_heading'), style='Section.TLabel').pack(anchor='w', pady=(5, 13))
        card = ttk.Frame(parent, style='Card.TFrame', padding=20)
        card.pack(fill='x')
        ttk.Label(card, text=self.t('credits_discord'), style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(card, text=self.t('credits_author_role'), style='CardMuted.TLabel').pack(anchor='w', pady=(3, 18))
        ttk.Separator(card).pack(fill='x', pady=(0, 15))
        ttk.Label(card, text=self.t('credits_github_label'), style='CardMuted.TLabel').pack(anchor='w')
        ttk.Button(card, text=github, style='Quiet.TButton', command=lambda: webbrowser.open(github, new=2)).pack(anchor='w', pady=(5, 14))
        ttk.Label(card, text=self.t('credits_website_label'), style='CardMuted.TLabel').pack(anchor='w')
        ttk.Button(card, text=website, style='Quiet.TButton', command=lambda: webbrowser.open(website, new=2)).pack(anchor='w', pady=(5, 8))
        ttk.Label(parent, text=self.t('credits_install_note'), style='Muted.TLabel', wraplength=780).pack(anchor='w', pady=(15, 0))

    def toggle_log(self, show=None):
        show = not self.log_visible if show is None else bool(show)
        if show == self.log_visible:
            return
        self.log_visible = show
        if show:
            # Place beside the main window if possible, rather than obscuring
            # its controls. Existing manual position is respected after first open.
            if not getattr(self, '_log_positioned', False):
                self.window.update_idletasks()
                root_x, root_y = self.window.winfo_rootx(), self.window.winfo_rooty()
                candidate = root_x + self.window.winfo_width() + 12
                if candidate + 850 > self.window.winfo_screenwidth():
                    candidate = max(20, root_x + 60)
                self.log_window.geometry(f'+{candidate}+{max(20, root_y + 70)}')
                self._log_positioned = True
            self.log_window.deiconify()
            self.log_window.lift()
        else:
            self.log_window.withdraw()
        self.log_button.configure(text=self.t('details_hide' if show else 'details_show'))

    def draw_edit(self, parent):
        # Keep the resizable two-pane PAK list. Hide detailed how-to behind Help.
        self.edit_split = tk.PanedWindow(
            parent, orient=tk.VERTICAL, background='#30272c', borderwidth=0,
            sashwidth=7, sashpad=3, sashrelief=tk.FLAT,
            showhandle=False, opaqueresize=True, cursor='sb_v_double_arrow')
        self.edit_split.pack(fill='both', expand=True)
        select = ttk.LabelFrame(self.edit_split, text=self.t('choose_group'), padding=10)
        self.edit_split.add(select, minsize=135, stretch='always')
        line = ttk.Frame(select)
        line.pack(fill='x', pady=(0, 7))
        ttk.Label(line, text=self.t('search_label'), style='Muted.TLabel').pack(side='left')
        ttk.Entry(line, textvariable=self.search).pack(side='left', fill='x', expand=True, padx=(10, 12))
        ttk.Button(line, text=self.t('refresh_list'), style='Quiet.TButton', command=self.reload_archives).pack(side='left')
        ttk.Button(line, text=self.t('browse_pak'), style='Quiet.TButton', command=self.browse_pak).pack(side='left', padx=(7, 0))
        self.file_filter = tk.StringVar(master=self.window, value='All files')
        ttk.Combobox(line, textvariable=self.file_filter, state='readonly', width=15,
                     values=('All files', 'PAK archives', 'Game files')).pack(side='left', padx=(8, 0))
        self.file_filter.trace_add('write', lambda *_: self.refresh_list())
        self.search.trace_add('write', lambda *_: self.refresh_list())
        archive_area = ttk.Frame(select)
        archive_area.pack(fill='both', expand=True)
        # Folder remains first. A filename-derived description sits to the left
        # of the exact PAK name, so users can identify assets without guessing.
        # Loose files use distinct item IDs and never enter the signed-PAK flow.
        self.archive_sort = ('folder', False)
        self.archives = ttk.Treeview(archive_area, columns=('folder', 'description', 'name', 'project', 'size'),
                                     show='headings', selectmode='extended', height=9,
                                     style='Archive.Treeview')
        for column, label, width, anchor in (
            ('folder', 'Folder', 155, 'w'),
            ('description', 'Description', 220, 'w'),
            ('name', 'File', 305, 'w'),
            ('project', 'Type / Project', 125, 'center'),
            ('size', 'Size', 95, 'e'),
        ):
            self.archives.heading(column, text=label,
                                  command=lambda col=column: self.sort_archives(col))
            self.archives.column(column, width=width, minwidth=75,
                                 stretch=column in ('folder', 'description', 'name'), anchor=anchor)
        self.archives.pack(side='left', fill='both', expand=True)
        archive_scroll = ttk.Scrollbar(archive_area, orient='vertical', command=self.archives.yview)
        archive_scroll.pack(side='right', fill='y')
        self.archives.configure(yscrollcommand=archive_scroll.set)
        self.archives.bind('<<TreeviewSelect>>', self.select_archive)
        self.archives.bind('<Double-Button-1>', self.open_browser_selection)
        self.archives.bind('<Button-3>', self.show_archive_context_menu)
        self.archives.bind('<Shift-F10>', self.show_archive_context_menu)
        self.archives.bind('<Control-a>', self.select_all_archives)
        self.archives.bind('<Control-A>', self.select_all_archives)
        status_line = ttk.Frame(select)
        status_line.pack(fill='x', pady=(7, 0))
        ttk.Label(status_line, textvariable=self.archive_count, style='Muted.TLabel').pack(side='left')
        ttk.Label(status_line, text=self.t('resize_hint'), style='Muted.TLabel').pack(side='left', padx=14)
        self.browser_selection = tk.StringVar(master=self.window, value='')
        ttk.Label(status_line, textvariable=self.browser_selection,
                  style='AccentText.TLabel').pack(side='right')

        lower = ttk.Frame(self.edit_split)
        self.edit_split.add(lower, minsize=160, stretch='never')
        actions = ttk.LabelFrame(lower, text=self.t('edit_group'), padding=10)
        actions.pack(fill='x')
        row = ttk.Frame(actions)
        row.pack(fill='x')
        for index, (key, command) in enumerate((('unpack_button', self.extract),
                      ('batch_unpack_button', self.extract_batch),
                      ('edit_button', self.open_editable), ('review_button', self.diff))):
            ttk.Button(row, text=self.t(key), style='Accent.TButton' if key == 'edit_button' else 'TButton',
                       command=command).pack(side='left', fill='x', expand=True, padx=(0, 7) if index < 3 else 0)
        finish = ttk.LabelFrame(lower, text=self.t('build_group'), padding=10)
        finish.pack(fill='x', pady=(8, 0))
        row = ttk.Frame(finish)
        row.pack(fill='x')
        ttk.Button(row, text=self.t('build_button'), style='Accent.TButton', command=self.build).pack(side='left', fill='x', expand=True, padx=(0, 7))
        ttk.Button(row, text=self.t('add_button'), command=self.install).pack(side='left', fill='x', expand=True, padx=(0, 7))
        ttk.Button(row, text='Import Modified PAK…', style='Quiet.TButton',
                   command=self.import_modified_pak).pack(side='left', fill='x', expand=True, padx=(0, 7))
        ttk.Button(row, text=self.t('undo_button'), style='Quiet.TButton', command=self.rollback).pack(side='left')

    def draw_play(self, parent):
        ttk.Label(parent, text=self.t('play_title'), style='Section.TLabel').pack(anchor='w', pady=(5, 3))
        ttk.Label(parent, text=self.t('play_hint'), style='Muted.TLabel').pack(anchor='w', pady=(0, 17))
        panel = ttk.Frame(parent, style='Card.TFrame', padding=17)
        panel.pack(fill='x')
        ttk.Label(panel, text=self.t('play_group'), style='CardMuted.TLabel').pack(anchor='w')
        ttk.Label(panel, textvariable=self.launch_state, style='CardTitle.TLabel').pack(anchor='w', pady=(5, 13))
        btns = ttk.Frame(panel, style='Card.TFrame')
        btns.pack(fill='x', pady=(0, 12))
        ttk.Button(btns, text=self.t('play_button'), style='Accent.TButton', command=self.launch).pack(side='left', fill='x', expand=True, padx=(0, 8), ipady=7)
        ttk.Button(btns, text=self.t('restore_button'), command=self.restore).pack(side='left', fill='x', expand=True, ipady=7)
        row = ttk.Frame(panel, style='Card.TFrame')
        row.pack(fill='x')
        ttk.Button(row, text=self.t('refresh_status'), style='Quiet.TButton', command=self.update_launch_state).pack(side='left')
        ttk.Button(row, text=self.t('status_details'), style='Quiet.TButton', command=self.show_status_details).pack(side='left', padx=(8, 0))
        ttk.Button(row, text=self.t('setup_helper'), style='Quiet.TButton', command=self.setup_launcher).pack(side='left', padx=(8, 0))
        ttk.Button(row, text=self.t('refresh_originals'), style='Quiet.TButton', command=self.refresh_originals).pack(side='left', padx=(8, 0))
        ttk.Button(parent, text=self.t('play_help_action'), style='Quiet.TButton',
                   command=lambda: self.show_help(1)).pack(anchor='w', pady=(16, 0))

    def show_status_details(self):
        st = state_status(Path(self.swap.get()))
        msg = st.message + (f'\n\nPrepared files: {st.ready}/{st.count}' if st.count else '')
        messagebox.showinfo(self.t('status_details'), msg, parent=self.window)

    def draw_settings(self, parent):
        ttk.Label(parent, text=self.t('settings_title'), style='Section.TLabel').pack(anchor='w', pady=(5, 14))
        settings = ttk.LabelFrame(parent, text=self.t('folders_group'), padding=14)
        settings.pack(fill='x')
        for i, (key, var, browse) in enumerate((
            ('stage_label', self.stage, self.browse_stage),
            ('swap_label', self.swap, self.browse_swap),
            ('projects_label', self.projects, self.browse_projects),
        )):
            ttk.Label(settings, text=self.t(key), width=19).grid(row=i, column=0, sticky='w', pady=6)
            ttk.Entry(settings, textvariable=var).grid(row=i, column=1, sticky='ew', padx=10)
            ttk.Button(settings, text=self.t('browse_button'), style='Quiet.TButton', command=browse).grid(row=i, column=2, sticky='e')
        settings.columnconfigure(1, weight=1)
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(14, 9))
        ttk.Button(row, text=self.t('setup_button'), style='Accent.TButton', command=self.initial_setup).pack(side='left', padx=(0, 8))
        ttk.Button(row, text=self.t('check_setup'), command=self.check_setup).pack(side='left')
        ttk.Button(row, text=self.t('open_projects'), style='Quiet.TButton', command=self.open_projects).pack(side='left', padx=(8, 0))
        ttk.Button(row, text=self.t('save_settings'), command=self.save_settings).pack(side='right')
        update_row = ttk.Frame(parent)
        update_row.pack(fill='x', pady=(4, 8))
        ttk.Label(update_row, textvariable=self.update_status, style='Muted.TLabel').pack(side='left', fill='x', expand=True)
        self.update_button = ttk.Button(update_row, text='Check for Updates', style='Quiet.TButton', command=self.check_updates)
        self.update_button.pack(side='right')
        ttk.Button(update_row, text='Install Update', style='Accent.TButton', command=self.offer_update).pack(side='right', padx=(0, 8))
        ttk.Separator(parent).pack(fill='x', pady=(8, 12))
        self.advanced_button = ttk.Button(parent, text=self.t('advanced_toggle_show'),
                                          style='Quiet.TButton', command=self.toggle_advanced)
        self.advanced_button.pack(side='left')
        ttk.Button(parent, text=self.t('settings_help_action'), style='Quiet.TButton',
                   command=lambda: self.show_help(2)).pack(side='right')
        self.advanced_win = None

    def toggle_advanced(self):
        if self.advanced_win is not None and self.advanced_win.winfo_exists():
            self.advanced_win.lift()
            self.advanced_win.focus_force()
            return
        win = tk.Toplevel(self.window)
        self.advanced_win = win
        win.title(self.t('advanced_group') + ' | ' + self.t('window_title'))
        win.geometry('800x450')
        win.minsize(650, 400)
        win.protocol('WM_DELETE_WINDOW', self.close_advanced)
        self.advanced_button.configure(text=self.t('advanced_toggle_hide'))
        advanced = ttk.LabelFrame(win, text=self.t('advanced_group'), padding=12)
        advanced.pack(fill='both', expand=True, padx=12, pady=12)
        ttk.Label(advanced, text=self.t('filter_label')).pack(anchor='w')
        ttk.Entry(advanced, textvariable=self.filter).pack(fill='x', pady=(3, 8))
        ttk.Label(advanced, text=self.t('workspace_label')).pack(anchor='w')
        ttk.Entry(advanced, textvariable=self.current_workspace).pack(fill='x', pady=(3, 8))
        ttk.Label(advanced, text=self.t('output_label')).pack(anchor='w')
        ttk.Entry(advanced, textvariable=self.output_pak).pack(fill='x', pady=(3, 8))
        controls = ttk.Frame(advanced)
        controls.pack(fill='x', pady=(8, 0))
        ttk.Button(controls, text=self.t('export_button'), command=self.export_bundle).pack(side='left')
        ttk.Button(controls, text=self.t('configure_button'), command=self.configure_bundle).pack(side='left', padx=(6, 0))
        ttk.Label(advanced, text=self.t('advanced_limit'), foreground='#b5b5bf', wraplength=740).pack(anchor='w', pady=(12, 0))

    def close_advanced(self):
        if self.advanced_win is not None and self.advanced_win.winfo_exists():
            self.advanced_win.destroy()
        self.advanced_win = None
        self.advanced_button.configure(text=self.t('advanced_toggle_show'))

    def browse_stage(self):
        p=filedialog.askdirectory(initialdir=self.stage.get(),title='Choose your custom-signing stage')
        if p:self.stage.set(p);self.reload_archives();self.update_launch_state()

    def browse_swap(self):
        p=filedialog.askdirectory(initialdir=self.swap.get(),title='Choose your PauseSwapTest folder')
        if p:self.swap.set(p);self.update_launch_state()

    def browse_projects(self):
        p=filedialog.askdirectory(initialdir=self.projects.get(),title='Choose project folder')
        if p:self.projects.set(p)

    def browse_pak(self):
        p=filedialog.askopenfilename(title='Select an existing custom-signed PAK',filetypes=[('PAK archives','*.pak'),('All files','*.*')])
        if p:
            self.external_pak.set(p)
            self.archive_label.set('External archive: '+p)
            self.current_workspace.set('');self.output_pak.set('')
            self.archives.selection_remove(self.archives.selection())
            self.loose_selected = ''
            if hasattr(self, "browser_selection"): self.browser_selection.set("External archive")
            self.write('Selected external file: '+p+'\n')

    def load_batch_mapping(self):
        """Reattach completed batch projects after restarting the manager."""
        self.batch_workspaces = {}
        if not self.batch_root.get():
            return
        batch = Path(self.batch_root.get()).resolve()
        try:
            data = load_collection(batch)
            if Path(data['stage']).resolve() != Path(self.stage.get()).resolve():
                return
            for row in data['archives']:
                self.batch_workspaces[row['archive']] = batch / row['workspace']
        except (OSError, ValueError, KeyError, TypeError):
            return

    def selected_archive_relatives(self):
        """Return exactly the staged PAKs visibly selected by the user."""
        # Only return visible rows in screen order, not the Treeview's focus
        # or any stale selection kept before a filter was applied.
        selected = set(self.archives.selection())
        return [rel for rel in self.visible if rel in selected]

    def _find_game_root(self):
        # Only discover files in the actual installed game recorded by stage setup.
        # Never scan arbitrary directories, the manager's private Data, or staged PAKs.
        try:
            plan = json.loads((Path(self.stage.get())/'rekey_plan.json').read_text(encoding='utf-8'))
            candidate = Path(plan['source_root']).resolve(strict=True)
            if (candidate/'bin64_SteamRetail'/'Evolve.exe').is_file():
                return candidate
        except (KeyError, ValueError, OSError, TypeError):
            pass
        return None

    def reload_archives(self):
        try:
            stage=Path(self.stage.get())
            if (stage/'rekey_plan.json').is_file():
                self.archive_entries=sorted(allowed_paks(stage).values(),key=str.casefold)
            else:
                self.archive_entries=sorted([p.relative_to(stage/'paks').as_posix() for p in (stage/'paks').rglob('*.pak')],key=str.casefold)
            self.game_root = self._find_game_root()
            self.loose_entries = scan_loose_files(self.game_root) if self.game_root else []
            self.load_batch_mapping()
            self.write(f'Found {len(self.archive_entries)} PAK archives and {len(self.loose_entries)} loose files.\n')
        except Exception as e:
            self.archive_entries=[]
            self.loose_entries=[]
            self.game_root=None
            self.write(f'Could not list archives: {e}\n')
        self.refresh_list()

    def select_all_archives(self, event=None):
        # Ctrl+A selects PAKs only, so batch operations never see loose assets.
        if self.visible:
            self.archives.selection_set(self.visible)
            self.archives.focus(self.visible[0])
            self.select_archive()
        return 'break'

    def sort_archives(self, column):
        sort_by = column if column in ('folder', 'description', 'name', 'size', 'project') else 'folder'
        name, reverse = self.archive_sort
        self.archive_sort = (sort_by, not reverse if name == sort_by else False)
        self.refresh_list()

    @staticmethod
    def _loose_iid(relative):
        return 'loose-file:' + relative

    def refresh_list(self):
        if not hasattr(self, 'archives'): return
        sort_by, reverse = self.archive_sort
        previous_selection = set(self.archives.selection())
        mode = self.file_filter.get() if hasattr(self, 'file_filter') else 'All files'
        text = self.search.get().casefold().strip()
        self.visible = (matching_archives(self.archive_entries, self.search.get())
                        if mode != 'Game files' else [])
        loose = ([item for item in getattr(self, 'loose_entries', [])
                  if text in item.relative.casefold()]
                 if mode != 'PAK archives' else [])
        rows = []
        stage_paks = Path(self.stage.get())/'paks' if hasattr(self, 'stage') else None
        for rel in self.visible:
            name, folder = describe_archive(rel)
            unpacked = (rel in getattr(self, 'batch_workspaces', {})
                        or (self.archive_label.get() == rel
                            and bool(self.current_workspace.get())
                            and (Path(self.current_workspace.get()) / '.evolve-pak-workspace.json').is_file()))
            path = stage_paks.joinpath(*rel.split('/')) if stage_paks else None
            try: size = path.stat().st_size if path and path.is_file() else -1
            except OSError: size = -1
            rows.append((rel, folder, archive_description(rel), name,
                         'Unpacked' if unpacked else 'PAK', size, False))
        for item in loose:
            name, folder = describe_archive(item.relative)
            rows.append((self._loose_iid(item.relative), folder, 'Game file', name,
                         'Game file', item.size, True))
        def sort_key(row):
            iid, folder, description, name, label, size, is_loose = row
            if sort_by == 'size': return (size, folder.casefold(), name.casefold(), iid.casefold())
            if sort_by == 'name': return (name.casefold(), folder.casefold(), iid.casefold())
            if sort_by == 'description': return (description.casefold(), folder.casefold(), name.casefold())
            if sort_by == 'project': return (label.casefold(), folder.casefold(), name.casefold())
            return (folder.casefold(), name.casefold(), iid.casefold())
        rows.sort(key=sort_key, reverse=reverse)
        self.archives.delete(*self.archives.get_children())
        for index, (iid, folder, description, name, state, size, is_loose) in enumerate(rows):
            self.archives.insert('', 'end', iid=iid,
                                 values=(folder, description, name, state, format_size(size)),
                                 tags=('game-file' if is_loose else 'odd' if index%2 else 'even',))
        self.browser_visible = [r[0] for r in rows]
        restored = [iid for iid in self.browser_visible if iid in previous_selection]
        if restored:
            self.archives.selection_set(restored)
        elif self.archive_label.get() in self.browser_visible:
            self.archives.selection_set(self.archive_label.get())
        elif self._loose_iid(getattr(self, 'loose_selected','')) in self.browser_visible:
            self.archives.selection_set(self._loose_iid(self.loose_selected))
        if self.archives.selection():
            self.archives.see(self.archives.selection()[0])
        self.archive_count.set(f'{len(rows)} files ({len(self.visible)} PAKs, {len(loose)} game files)' if rows
                               else self.t('no_paks'))
        self.update_browser_selection()

    def update_browser_selection(self):
        if not hasattr(self, 'browser_selection'): return
        chosen = self.selected_archive_relatives()
        selection = self.archives.selection()
        if len(selection) > 1:
            self.browser_selection.set(f'{len(selection)} selected')
        elif len(chosen) == 1:
            self.browser_selection.set(describe_archive(chosen[0])[0])
        elif len(selection) == 1 and selection[0].startswith('loose-file:'):
            self.browser_selection.set('Game file copy')
        else:
            self.browser_selection.set('')

    def _pak_locations(self, relative):
        """Return a staged file and its actual installed-game counterpart."""
        if relative not in self.archive_entries:
            raise ValueError('Select a staged PAK first.')
        parts = relative.replace('\\', '/').split('/')
        if not parts or any(part in ('', '.', '..') for part in parts):
            raise ValueError('Unsafe PAK path: ' + relative)
        staged = Path(self.stage.get()) / 'paks' / Path(*parts)
        game_root = getattr(self, 'game_root', None) or self._find_game_root()
        original = Path(game_root) / Path(*parts) if game_root else None
        return staged, original

    @staticmethod
    def _show_file_in_explorer(path):
        """Select a file in Explorer without opening, editing or launching it."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError('File not found: ' + str(path))
        if os.name == 'nt':
            subprocess.Popen(['explorer.exe', '/select,', str(path)])
        else:
            open_folder(path.parent)

    def inspect_browser_pak(self, relative=None):
        try:
            relative = relative or self.archive_label.get()
            staged, original = self._pak_locations(relative)
            name = describe_archive(relative)[0]
            size = format_size(staged.stat().st_size) if staged.is_file() else 'Missing'
            original_status = ('Found' if original.is_file() else 'Not found') if original else 'Unknown game folder'
            workspace = self.batch_workspaces.get(relative)
            if not workspace and self.archive_label.get() == relative and self.current_workspace.get():
                workspace = Path(self.current_workspace.get())
            workspace_text = str(workspace) if workspace and Path(workspace).is_dir() else 'Not unpacked'
            messagebox.showinfo(
                'PAK details: ' + name,
                'Description (estimated from filename): ' + archive_description(relative) +
                '\nArchive: ' + relative +
                '\nStaged size: ' + size +
                '\nStaged copy: ' + str(staged) +
                '\nInstalled game copy: ' + (str(original) if original else 'Unavailable') +
                '\nGame copy: ' + original_status +
                '\nUnpacked workspace: ' + workspace_text +
                '\n\nThese labels are inferred from filenames, not a scan of the contents.',
                parent=self.window)
        except Exception as exc:
            self.fail(exc)

    def reveal_browser_pak(self, relative=None, original=True):
        try:
            relative = relative or self.archive_label.get()
            staged, game_file = self._pak_locations(relative)
            target = game_file if original else staged
            if target is None:
                raise ValueError('The original game folder is unknown. Check your setup in Settings.')
            self._show_file_in_explorer(target)
        except Exception as exc:
            self.fail(exc)

    def copy_browser_pak_path(self, relative=None, original=True):
        try:
            staged, game_file = self._pak_locations(relative or self.archive_label.get())
            path = game_file if original else staged
            if path is None:
                raise ValueError('The original game folder is unknown. Check your setup in Settings.')
            self.window.clipboard_clear()
            self.window.clipboard_append(str(path))
            self.write('Copied PAK path: ' + str(path) + '\n')
        except Exception as exc:
            self.fail(exc)

    def open_unpacked_browser_pak(self, relative=None):
        try:
            rel = relative or self.archive_label.get()
            workspace = self.batch_workspaces.get(rel)
            if not workspace and self.archive_label.get() == rel and self.current_workspace.get():
                workspace = Path(self.current_workspace.get())
            if not workspace or not (Path(workspace) / '.evolve-pak-workspace.json').is_file():
                raise ValueError('Unpack this PAK first.')
            open_folder(Path(workspace) / 'files')
        except Exception as exc:
            self.fail(exc)

    def show_archive_context_menu(self, event=None):
        """Right-click one row without accidentally applying actions to another."""
        if event is None:
            return
        if getattr(event, 'keysym', '') == 'F10':
            iid = self.archives.focus() or (
                self.archives.selection()[0] if self.archives.selection() else '')
            if not iid:
                return 'break'
            bbox = self.archives.bbox(iid)
            x_root = self.archives.winfo_rootx() + (bbox[0] + 25 if bbox else 20)
            y_root = self.archives.winfo_rooty() + (bbox[1] + 15 if bbox else 20)
        else:
            if self.archives.identify('region', event.x, event.y) != 'cell':
                return
            iid = self.archives.identify_row(event.y)
            if not iid:
                return
            x_root, y_root = event.x_root, event.y_root
        if iid not in self.archives.selection():
            self.archives.selection_set(iid)
        self.archives.focus(iid)
        self.select_archive()
        menu = tk.Menu(self.archives, tearoff=False)
        if iid.startswith('loose-file:'):
            relative = iid[len('loose-file:'):]
            menu.add_command(label=self.t('pak_menu_edit_loose'), command=self.open_editable)
            game = getattr(self, 'game_root', None)
            if game:
                path = Path(game) / Path(*relative.split('/'))
                menu.add_command(label=self.t('pak_menu_reveal_game'),
                                 command=lambda p=path: self._show_file_in_explorer(p))
        else:
            relative = iid
            selected = self.selected_archive_relatives()
            if len(selected) > 1:
                menu.add_command(label=self.t('pak_menu_batch'), command=self.extract_batch)
            else:
                menu.add_command(label=self.t('pak_menu_unpack'), command=self.extract)
            menu.add_command(label=self.t('pak_menu_inspect'),
                             command=lambda rel=relative: self.inspect_browser_pak(rel))
            menu.add_separator()
            menu.add_command(label=self.t('pak_menu_reveal_game'),
                             command=lambda rel=relative: self.reveal_browser_pak(rel, original=True))
            menu.add_command(label=self.t('pak_menu_reveal_stage'),
                             command=lambda rel=relative: self.reveal_browser_pak(rel, original=False))
            menu.add_command(label=self.t('pak_menu_copy_game'),
                             command=lambda rel=relative: self.copy_browser_pak_path(rel, original=True))
            menu.add_command(label=self.t('pak_menu_copy_stage'),
                             command=lambda rel=relative: self.copy_browser_pak_path(rel, original=False))
            menu.add_separator()
            menu.add_command(label=self.t('pak_menu_open_unpacked'),
                             command=lambda rel=relative: self.open_unpacked_browser_pak(rel))
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()
        return 'break'

    def open_browser_selection(self, event=None):
        if getattr(self, 'loose_selected', ''):
            self.open_editable()
        else:
            self.extract()

    def select_archive(self,event=None):
        selected = self.archives.selection()
        focused = self.archives.focus()
        chosen_iid = focused if focused in selected else (selected[0] if selected else '')
        if chosen_iid.startswith('loose-file:'):
            self.loose_selected = chosen_iid[len('loose-file:'):]
            self.archive_label.set('')
            self.external_pak.set('')
            self.current_workspace.set('')
            self.output_pak.set('')
            self.update_browser_selection()
            return
        self.loose_selected = ''
        chosen = self.selected_archive_relatives()
        self.update_browser_selection()
        if not chosen: return
        rel = focused if focused in chosen else chosen[0]
        if self.archive_label.get() != rel:
            ws = self.batch_workspaces.get(rel)
            self.current_workspace.set(str(ws) if ws and (ws/'.evolve-pak-workspace.json').is_file() else '')
            self.output_pak.set('')
        self.external_pak.set('')
        self.archive_label.set(rel)

    def selected(self):
        external=self.external_pak.get()
        if external:
            p=Path(external)
            if not p.is_file():raise FileNotFoundError('Selected PAK is missing: '+str(p))
            return p,None
        rel=self.archive_label.get()
        if rel not in self.archive_entries:
            raise ValueError('Choose a PAK from the list first.')
        p=Path(self.stage.get())/'paks'/Path(*rel.split('/'))
        if not p.is_file():raise FileNotFoundError('PAK missing from stage: '+str(p))
        return p,rel

    def workspace(self):
        p=Path(self.current_workspace.get())
        if not self.current_workspace.get() or not (p/'.evolve-pak-workspace.json').is_file():
            raise ValueError('First click "Unpack PAK" to make editable files.')
        return p

    def keys(self):
        p=Path(self.stage.get())/'mykeys'
        pub=p/'public_key.bin';priv=p/'private_key.pem'
        if not pub.is_file():raise FileNotFoundError('Custom public signing key missing: '+str(pub))
        return pub,priv

    def new_directory(self,prefix):
        root=Path(self.projects.get());root.mkdir(parents=True,exist_ok=True)
        name=root/prefix
        i=2
        while name.exists():
            name=root/(prefix+'_'+str(i));i+=1
        return name

    def extract(self):
        try:
            pak,rel=self.selected();pub,_=self.keys()
            name=(rel or 'External/'+pak.name).replace('\\','/').replace('/','_').replace('.pak','')
            ws=self.new_directory(name)
            cmd=[sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),'extract',
                 '--pak',str(pak),'--public-key',str(pub),'--workspace',str(ws),'--skip-unsupported']
            if self.filter.get().strip():cmd += ['--filter',self.filter.get().strip()]
            self.run_steps([('Unpacking archive',cmd)], 'Unpack archive',lambda:self.after_extract(ws))
        except Exception as e:self.fail(e)

    def extract_batch(self):
        try:
            if self.busy:
                raise RuntimeError('Wait for the current task to finish.')
            selected=self.selected_archive_relatives()
            if not 2 <= len(selected) <= 30:
                raise ValueError('Use Ctrl/Shift-click to select 2–30 PAKs in the list first.')
            if self.external_pak.get():
                raise ValueError('Batch extraction is for the verified staged PAK list only.')
            if self.filter.get().strip():
                raise ValueError('Clear the extraction filter for batch asset linking; complete texture paths are required.')
            pub,_=self.keys()
            if not messagebox.askyesno('Unpack multiple PAKs?',
                    f'Unpack {len(selected)} signed PAKs into separate workspaces?\n\n'
                    'This can use substantial disk space. Identical asset names are kept separate, '
                    'and each PAK must still be built and installed individually.\n\n'
                    'Continue?',parent=self.window):
                return
            batch=self.new_directory('Batch_Assets')
            cmd=[sys.executable,'-u',str(ROOT/'multi_pak_assets.py'),
                 '--stage',self.stage.get(),'--public-key',str(pub),
                 '--destination',str(batch)]
            for rel in selected:cmd += ['--pak',rel]
            self.run_steps([('Unpacking selected PAKs',cmd)],'Batch unpack',
                           lambda:self.after_extract_batch(batch))
        except Exception as e:self.fail(e)

    def after_extract_batch(self, batch):
        data=load_collection(batch)
        self.batch_root.set(str(batch))
        self.load_batch_mapping()
        first=data['archives'][0]
        self.archive_label.set(first['archive'])
        self.external_pak.set('')
        self.current_workspace.set(str(self.batch_workspaces[first['archive']]))
        self.output_pak.set('')
        self.persist()
        self.write('BATCH UNPACK COMPLETE: '+str(batch)+'\n')
        self.write('Edit Files lists ALL unpacked PAKs above the file tabs; select the one you want to edit.\n')
        self.write('Find textures from the Models tab; archives remain independent for rebuilds.\n')
        self.open_editable()

    def after_extract(self,ws):
        self.current_workspace.set(str(ws));self.output_pak.set('')
        self.persist()
        self.write('EDITABLE FILES: '+str(ws/'files')+'\n')
        self.open_editable()

    def editor_archive_map(self):
        """Archives in a completed batch, never a merged or editable fake PAK."""
        self.load_batch_mapping()
        selected = self.selected_archive_relatives()
        if len(selected) > 1:
            missing = [name for name in selected if name not in self.batch_workspaces]
            if missing:
                raise ValueError('You selected multiple PAKs, but not all were unpacked in a batch. '
                                 'Click "Unpack Selected PAKs" first, wait for it to finish, then choose Edit Files. '
                                 'Missing: ' + ', '.join(missing[:4]))
        # A batch collection is browsable even if the main list has since been
        # narrowed to one PAK. This is also how the editor opens after a batch.
        current = self.workspace().resolve()
        if current in (Path(p).resolve() for p in self.batch_workspaces.values()):
            return {name: path for name, path in self.batch_workspaces.items()}
        return {}

    def open_editable(self):
        try:
            if getattr(self, 'loose_selected', ''):
                if self.busy: raise RuntimeError('Wait until the current task finishes.')
                if not self.game_root: raise ValueError('Installed game location is unavailable.')
                editor = LooseFileEditor(self.window, self.game_root, self.loose_selected,
                                         Path(self.projects.get()))
                self.editors.append(editor)
                return
            ws = self.workspace().resolve()
            if not (ws/'files').is_dir():
                raise FileNotFoundError('Unpacked file folder missing: '+str(ws/'files'))
            archive_map = self.editor_archive_map()
            for editor in self.editors:
                if editor.window.winfo_exists() and (getattr(editor, 'workspace', None) == ws or ws in getattr(editor, 'archive_map', {}).values()):
                    editor.window.lift()
                    if editor.workspace != ws and archive_map:
                        label = next((key for key, path in editor.archive_map.items() if Path(path).resolve() == ws), None)
                        if label:
                            editor.switch_archive(label)
                    return
            self.editors.append(WorkspaceEditor(self, ws, archive_map=archive_map))
        except Exception as e:self.fail(e)

    def flush_editors(self):
        for editor in self.editors:
            if editor.window.winfo_exists() and hasattr(editor, 'confirm') and not editor.confirm():return False
        return True

    def diff(self):
        try:
            if not self.flush_editors():return
            ws=self.workspace()
            self.run_steps([('Checking edits',[sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),
                         'diff','--workspace',str(ws)])], 'Check edits')
        except Exception as e:self.fail(e)

    def build(self):
        try:
            if not self.flush_editors():return
            ws=self.workspace();pub,priv=self.keys()
            if not priv.is_file():raise FileNotFoundError('Private signing key is missing: '+str(priv))
            meta=json.loads((ws/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))
            source=Path(meta['source_pak'])
            selected_pak,selected_rel=self.selected()
            if source.resolve()!=selected_pak.resolve():
                raise ValueError('Selected PAK does not match this editing project. Select the PAK used when unpacking.')
            if not source.is_file():raise FileNotFoundError('Source PAK is missing: '+str(source))
            builds=Path(self.projects.get())/'Built'
            stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            folder=(selected_rel or source.name).replace('/','_')
            dest=builds/folder/stamp/source.name
            dest.parent.mkdir(parents=True,exist_ok=False)
            cmd=[sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),'build','--workspace',str(ws),
                 '--public-key',str(pub),'--private-key',str(priv),'--out',str(dest)]
            self.run_steps([('Rebuilding and signing PAK',cmd)],'Build Mod',lambda:self.after_build(dest))
        except Exception as e:self.fail(e)

    def after_build(self,p):
        self.output_pak.set(str(p));self.persist()
        self.write('SIGNED PAK READY: '+str(p)+'\n')
        messagebox.showinfo(self.t('dialog_build_title'), self.t('dialog_build_body'))

    def install(self):
        try:
            pak,rel=self.selected()
            if rel is None:raise ValueError('For staging, select a PAK from the signed archive list rather than an external file.')
            mod=Path(self.output_pak.get())
            if not mod.is_file():raise FileNotFoundError('Click "Build Mod" first.')
            if mod.name.casefold()!=pak.name.casefold():raise ValueError('Built PAK filename does not match your selected archive.')
            ws=self.workspace()
            meta=json.loads((ws/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))
            if Path(meta['source_pak']).resolve()!=pak.resolve():
                raise ValueError('This build belongs to another PAK. Select its original archive first.')
            if not messagebox.askyesno('Add your modified PAK?',
                    f'Add your signed version of {rel} to your prepared mods?\n\n'
                    'This does not edit installed game files. A backup is kept so you can undo it.\n\n'
                    'Close Evolve and its launcher first.'):
                return
            cmd=[sys.executable,'-u',str(ROOT/'universal_stage.py'),'install',
                 '--stage',self.stage.get(),'--swap-dir',self.swap.get(),
                 '--backup-root',str(Path(self.projects.get())/'Backups'),
                 '--relative',rel,'--mod',str(mod)]
            self.run_steps([('Adding modified PAK to staged mods',cmd)],'Add PAK',self.after_install)
        except Exception as e:self.fail(e)

    def import_modified_pak(self):
        """Safely stage a user-supplied, already custom-signed PAK.

        The existing universal_stage.install worker performs full RSA and
        archive-record verification and keeps a rollback backup. Importing
        never writes to the installed game, and does not require an editor
        workspace or rebuild.
        """
        try:
            if self.busy:
                raise RuntimeError('Wait until the current operation finishes.')
            self.archive_entries = sorted(allowed_paks(Path(self.stage.get())).values(), key=str.casefold)
            built = Path(self.projects.get()) / 'Built'
            source = filedialog.askopenfilename(
                title='Import a compatible, modified PAK',
                initialdir=str(built if built.is_dir() else Path(self.projects.get())),
                filetypes=[('PAK archives', '*.pak')])
            if not source:return
            source = Path(source).resolve()
            if not source.is_file() or source.suffix.casefold() != '.pak':
                raise ValueError('Choose an existing .pak archive.')
            selected = self.archive_label.get() if self.archive_label.get() in self.archive_entries else None
            relative = import_target(source.name, self.archive_entries, selected)
            staged = (Path(self.stage.get())/'paks'/Path(*relative.split('/'))).resolve()
            if source == staged:
                raise ValueError('This is the original staged PAK. Choose an independently modified archive.')
            if not messagebox.askyesno('Import modified PAK?',
                    f'Add {source.name} to your prepared mods as {relative}?\n\n'
                    'The archive must be signed with this manager\'s current key '
                    'and retain the original entries and filename.\n\n'
                    'A rollback backup will be saved. Installed game files will '
                    'NOT be changed. Close Evolve and its launcher first.',
                    parent=self.window):
                return
            self.archive_label.set(relative)
            self.external_pak.set('')
            if relative in self.visible:
                self.archives.selection_set(relative)
                self.archives.focus(relative)
                self.archives.see(relative)
            command = [sys.executable, '-u', str(ROOT/'universal_stage.py'), 'install',
                       '--stage', self.stage.get(), '--swap-dir', self.swap.get(),
                       '--backup-root', str(Path(self.projects.get())/'Backups'),
                       '--relative', relative, '--mod', str(source)]
            self.run_steps([('Verifying RSA and importing modified PAK', command)],
                           'Import Modified PAK', self.after_import)
        except Exception as exc:
            self.fail(exc)

    def after_import(self):
        # Any previously extracted workspace was based on the older staged PAK.
        # Keep the files and backups on disk, but never present it as current.
        self.current_workspace.set('')
        self.output_pak.set('')
        self.persist()
        self.refresh_list()
        self.after_install()

    def after_install(self):
        self.update_launch_state()
        if messagebox.askyesno(self.t('dialog_added_title'), self.t('dialog_added_body')):
            self.notebook.select(self.play_tab)

    def rollback(self):
        try:
            root=Path(self.projects.get())/'Backups'
            p=filedialog.askdirectory(title='Choose timestamped backup folder (contains metadata.json)',
                                      initialdir=str(root if root.is_dir() else ROOT))
            if not p:return
            meta=Path(p)/'metadata.json'
            if not meta.is_file():raise ValueError('Select the dated backup folder containing metadata.json.')
            record=json.loads(meta.read_text(encoding='utf-8'))
            if not messagebox.askyesno('Undo staged mod?',
                    f'Restore the earlier signed {record["relative"]} from this backup?\n\n'
                    'This does not modify live game files.'):
                return
            self.run_steps([('Undoing staged PAK',[sys.executable,'-u',str(ROOT/'universal_stage.py'),
                             'restore','--backup',p])],'Undo staged mod',self.update_launch_state)
        except Exception as e:self.fail(e)

    def setup_launcher(self):
        try:
            swap=Path(self.swap.get())
            if not swap.is_dir():raise FileNotFoundError('Existing one-click folder missing: '+str(swap))
            missing=missing_scripts(swap)
            if not missing:
                messagebox.showinfo('Launcher already set up','Your working launcher helper files are already present. Nothing needs changing.')
                return
            if not messagebox.askyesno('Install missing helpers?',
                'Add these missing helper scripts to your existing one-click folder?\n\n'
                + '\n'.join(missing)+'\n\nExisting scripts, backup files, keys, and swap state will NOT be overwritten.'):
                return
            added=add_missing_scripts(swap)
            self.write('Added missing launcher files: '+(', '.join(added) or 'none')+'\n')
            self.update_launch_state()
            messagebox.showinfo('Setup finished','Added missing launcher helper files. Your previous mod files and backups were kept.')
        except Exception as e:self.fail(e)

    def update_launch_state(self):
        st=state_status(Path(self.swap.get()))
        if st.phase in ('missing', 'prepared', 'restored') and (
                st.game or self.game_root):
            # Don't claim the game is safe when the journal is stale.
            try:
                game = Path(st.game) if st.game else self.game_root
                if plan_recovery(game):
                    from launch_integration import SwapStatus
                    st = SwapStatus('unsafe', 'Unrestored original backups found in game folder.')
            except (OSError, ValueError):
                # A missing/unconfigured game is diagnosed when Restore is clicked.
                pass
        summaries = {
            'missing': self.t('launch_missing'),
            'restored': self.t('launch_restored'),
            'prepared': self.t('launch_prepared'),
            'swapped': self.t('launch_restore'),
            'swapping': self.t('launch_restore'),
            'restoring': self.t('launch_restore'),
            'interrupted': self.t('launch_restore'),
            'unsafe': self.t('launch_attention'),
        }
        self.launch_state.set(summaries.get(st.phase, self.t('launch_attention')))
        return st

    def launch(self):
        try:
            if self.bundle_error:raise RuntimeError(self.bundle_error)
            if (APP_HOME/MARKER).is_file() and not json.loads((APP_HOME/MARKER).read_text(encoding='utf-8')).get('configured'):
                self.configure_bundle();return
            if self.busy:raise RuntimeError('Another task is still running.')
            st=self.update_launch_state()
            if st.phase=='unsafe' or st.phase in ('swapped','swapping','restoring','interrupted'):
                raise RuntimeError('Previous swap is not safely restored. Close Evolve and use Restore Original Game Files first.')
            if st.stage and Path(st.stage).resolve()!=Path(self.stage.get()).resolve():
                raise ValueError('The launcher state belongs to a different signing stage. Check Settings.')
            cmds=launcher_commands(Path(self.swap.get()),Path(self.stage.get()),st.phase)
            if not messagebox.askyesno(self.t('dialog_play_title'), self.t('dialog_play_body')):
                return
            self.write('Starting guided mod launch. Wait for HOOK READY before pressing Play.\n')
            self.run_steps(cmds,'Play with Mods',self.after_launch,cwd=Path(self.swap.get()))
        except Exception as e:self.fail(e)

    def after_launch(self):
        self.update_launch_state()
        self.write('Evolve launch handed off. After playing, close game + launcher, then RESTORE originals.\n')

    def restore(self):
        try:
            if self.busy:raise RuntimeError('Wait for the current operation to finish.')
            st=self.update_launch_state()
            # The journal can be missing or stale even with hundreds of live
            # .customkey-original backups. Inspect the installed game first.
            game = Path(st.game) if st.game and Path(st.game).is_dir() else (
                self.game_root or self._find_game_root())
            if not game:
                chosen = filedialog.askdirectory(
                    title='Choose EvolveGame folder to check original-file backups')
                if not chosen: return
                game = Path(chosen)
            game = Path(game).resolve(strict=True)
            pending = plan_recovery(game)
            if pending and st.phase not in ('swapped','swapping','restoring','interrupted'):
                # Journal-independent recovery intentionally uses a separate
                # manifest, does not touch the journal and preserves live mods.
                size = sum(item.bytes for item in pending) / (1024 ** 3)
                if not messagebox.askyesno(
                        'Recover original game files?',
                        f'Found {len(pending)} original backups ({size:.2f} GiB) even though '
                        'the launch journal does not report an active swap.\n\n'
                        'Close Evolve and its launcher first.\n\n'
                        'Recovery will preserve EVERY currently installed modified file '
                        'under a unique recovery name, restore each original backup, '
                        'and save a recovery manifest in your manager Data folder.\n\n'
                        'This may require setting up your mods again afterward. '
                        'Continue?', parent=self.window):
                    return
                cmd = [sys.executable, '-u', str(ROOT/'orphaned_swap_recovery.py'),
                       '--game', str(game), '--manifest-dir',
                       str(DATA_HOME/'RecoveryLogs'), '--apply']
                self.run_steps([('Recovering original game files without a swap journal', cmd)],
                               'Restore originals', self.after_restore)
                return
            if not pending and st.phase in ('missing','prepared','restored'):
                messagebox.showinfo('Nothing to restore',
                                    'No original swap backups were found in the selected game folder.',
                                    parent=self.window)
                return
            if not pending and st.phase == 'unsafe':
                raise RuntimeError('Swap state is unsafe, but no original backups were found. '
                                   'Inspect the swap journal before continuing.')
            if not messagebox.askyesno(self.t('dialog_restore_title'),
                                       self.t('dialog_restore_body')):
                return
            cmd=restore_command(Path(self.swap.get()))
            self.run_steps([('Restoring original game files',cmd)],'Restore originals',
                           self.after_restore,cwd=Path(self.swap.get()))
        except Exception as e:self.fail(e)

    def offer_initial_setup(self):
        if messagebox.askyesno(self.t('setup_question_title'),
            self.t('setup_question_body')):
            self.initial_setup()

    def initial_setup(self):
        try:
            if self.busy:raise RuntimeError('Wait for the current operation to finish.')
            if self.bundle_error:raise RuntimeError(self.bundle_error)
            if (APP_HOME/MARKER).is_file():
                messagebox.showinfo('Use the app-only package','This is a full data bundle. Configure its bundled setup, or use the standard app-only release to create a fresh setup from your own game.');return
            if not self.flush_editors():return
            st=self.update_launch_state()
            if st.phase not in ('missing','restored','prepared'):
                raise RuntimeError('Restore the existing game swap before starting a new setup.')
            game=filedialog.askdirectory(title='Step 1: Choose installed EvolveGame folder')
            if not game:return
            launcher=filedialog.askopenfilename(title='Step 2: Choose the normal Modded Evolve client EXE',filetypes=[('Windows executable','*.exe')])
            if not launcher:return
            pending = scan_prepared(Path(game))
            if pending['backups']:
                messagebox.showerror(self.t('old_backups_title'),
                    self.t('old_backups_body') + '\n\n' + str(pending['backups'][0]))
                return
            if pending['partial']:
                messagebox.showerror(self.t('old_partial_title'),
                    self.t('old_partial_body') + '\n\n' + str(pending['partial'][0]))
                return
            preserve_old = False
            if pending['ready']:
                preserve_old = messagebox.askyesno(self.t('old_ready_title'),
                    self.t('old_ready_body').replace('{count}', str(len(pending['ready']))))
                if not preserve_old:
                    self.write('Setup cancelled. Previously prepared mod files left unchanged.\n')
                    return
            backup_choice=messagebox.askyesnocancel('Keep full original PAK copies?',
                'YES — Keep a separate full snapshot of every original PAK (uses more disk space).\n\n'
                'NO — Space-saving setup: read the installed originals directly and keep only custom-signed PAKs.\n\n'
                'Both modes protect the original files during live swaps and allow normal restoration.\n'
                'No does not remove any previously saved snapshots.\n\n'
                'Cancel — Exit setup without changes.')
            if backup_choice is None:return
            keep_original_snapshots=bool(backup_choice)
            destination=DATA_HOME/'GameSets'/('Setup_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            if not messagebox.askyesno(self.t('setup_confirm_title'),
                'Close Evolve and the normal client first.\n\n'
                'The manager will verify your original PAKs, generate signing keys and build a local signed set. '
                + ('It will also keep complete original snapshots. Allow up to twice the PAK size plus 1 GiB here.'
                   if keep_original_snapshots else
                   'Space-saving mode: no permanent original PAK copies. Allow up to the PAK size plus 1 GiB here.')
                + ' Preparing a later game launch needs additional game-drive space.\n\n'
                'Storage: '+str(destination)+'\n\nCreate setup now?'):return
            cmd=[sys.executable,'-u',str(ROOT/'first_run_setup.py'),'--game',game,'--launcher',launcher,
                 '--destination',str(destination),'--seed-parent',str(DATA_HOME/'SetupSeeds')]
            if preserve_old:cmd.append('--preserve-old-prepared')
            if not keep_original_snapshots:cmd.append('--skip-original-snapshots')
            self.run_steps([('Creating mod setup from installed game',cmd)],'Set up from game',lambda:self.after_initial_setup(destination))
        except Exception as e:self.fail(e)

    def after_initial_setup(self,destination):
        report=json.loads((destination/'refresh_report.json').read_text(encoding='utf-8'))
        if report.get('status')!='complete':raise RuntimeError('Local setup did not finish; previous settings retained.')
        self.stage.set(report['stage']);self.swap.set(report['swap'])
        self.current_workspace.set('');self.output_pak.set('');self.archive_label.set('');self.external_pak.set('')
        Path(self.projects.get()).mkdir(parents=True,exist_ok=True)
        self.persist();self.reload_archives();self.update_launch_state()
        old=report.get('preserved_old_prepared',{})
        note=('\n\n'+self.t('old_ready_complete').replace('{count}',str(old['count']))) if old.get('count') else ''
        messagebox.showinfo(self.t('setup_complete_title'),self.t('setup_complete_body')+note)

    def export_bundle(self):
        try:
            if self.busy:raise RuntimeError('Wait for the current task to finish.')
            if self.bundle_error:raise RuntimeError(self.bundle_error)
            if not self.flush_editors():return
            if not getattr(sys,'frozen',False):raise RuntimeError('Build the Windows app, then export from EvolveModManager.exe so the ZIP can include its runtime.')
            self.persist()
            output=filedialog.asksaveasfilename(title='Save complete manager and mod setup',defaultextension='.zip',
                initialfile='EvolveModManager-Complete.zip',filetypes=[('ZIP archive','*.zip')])
            if not output:return
            if not messagebox.askyesno(self.t('dialog_export_title'), self.t('dialog_export_body')):return
            cmd=[sys.executable,'-u',str(ROOT/'portable_bundle.py'),'export','--home',str(APP_HOME),
                 '--stage',self.stage.get(),'--swap',self.swap.get(),'--projects',self.projects.get(),'--output',output]
            self.run_steps([('Collecting complete manager bundle',cmd)],'Export complete ZIP',
                lambda:messagebox.showinfo('Complete ZIP ready','Share this ZIP: '+output+'\n\nExtract the whole folder and open EvolveModManager.exe. First launch asks for the installed game and normal client.'))
        except Exception as e:self.fail(e)

    def configure_bundle(self):
        try:
            if self.busy:raise RuntimeError('Wait for the current task to finish.')
            if self.bundle_error:raise RuntimeError(self.bundle_error)
            if not (APP_HOME/MARKER).is_file():
                messagebox.showinfo('No exported bundle','This button configures a Complete Manager ZIP. Use Export Complete Manager ZIP to create one from your existing setup.');return
            marker=json.loads((APP_HOME/MARKER).read_text(encoding='utf-8'))
            if marker.get('configured'):
                messagebox.showinfo('Bundle configured','This bundle is already configured for: '+marker.get('game',''));return
            game=filedialog.askdirectory(title='Choose your installed EvolveGame folder')
            if not game:return
            launcher=filedialog.askopenfilename(title='Choose the normal Modded Evolve client EXE',filetypes=[('Windows executable','*.exe')])
            if not launcher:return
            cmd=[sys.executable,'-u',str(ROOT/'portable_bundle.py'),'configure','--home',str(APP_HOME),'--game',game,'--launcher',launcher]
            self.run_steps([('Checking installed game and configuring bundle',cmd)],'Configure complete bundle',self.after_bundle_configure)
        except Exception as e:self.fail(e)

    def after_bundle_configure(self):
        self.read_settings();self.reload_archives();self.update_launch_state()
        messagebox.showinfo('Bundle ready','All manager resources are in this folder. Your game and normal client paths are configured.')

    def refresh_originals(self):
        try:
            if self.busy:raise RuntimeError('Wait for the current task to finish.')
            if not self.flush_editors():return
            st=self.update_launch_state()
            if st.phase not in ('missing','prepared','restored'):
                raise RuntimeError('Restore Original Game Files first, then update/repair through the normal client.')
            game=filedialog.askdirectory(title='Choose updated EvolveGame folder',initialdir=st.game or None)
            if not game:return
            storage=DATA_HOME/'GameSets';storage.mkdir(parents=True,exist_ok=True)
            folder=filedialog.askdirectory(title='Choose storage folder for the NEW signed PAK set',initialdir=str(storage))
            if not folder:return
            backup_choice=messagebox.askyesnocancel('Keep full original PAK copies for this update?',
                'YES — Store full original PAK snapshots with the new stage (more space).\n\n'
                'NO — Use installed originals without permanent snapshots (saves space).\n\n'
                'Both choices retain safe live-swap restoration backups and earlier game sets.\n'
                'Cancel — Do not refresh.')
            if backup_choice is None:return
            keep_original_snapshots=bool(backup_choice)
            destination=Path(folder)/('EvolveRefresh_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            if not messagebox.askyesno('Refresh originals from updated game?',
                'First restore originals, update or repair the game in the normal client, then close the game and client.\n\n'
                'This verifies installed PAKs and builds a fresh signed set. '
                + ('Full original snapshots are also kept; allow twice the PAK size plus 1 GiB.'
                   if keep_original_snapshots else
                   'Space-saving mode skips extra originals; allow PAK size plus 1 GiB.')
                + ' Previous sets and edits are kept. Old mods must be reviewed and reapplied.\n\n'
                'New set: '+str(destination)+'\n\nContinue?'):return
            cmd=[sys.executable,'-u',str(ROOT/'refresh_originals.py'),'--game',game,'--stage',self.stage.get(),
                 '--swap',self.swap.get(),'--destination',str(destination)]
            if not keep_original_snapshots:cmd.append('--skip-original-snapshots')
            self.run_steps([('Refreshing original PAKs and signing new set',cmd)],'Refresh Original PAKs',lambda:self.after_refresh(destination))
        except Exception as e:self.fail(e)

    def after_refresh(self,destination):
        report=json.loads((destination/'refresh_report.json').read_text(encoding='utf-8'))
        if report.get('status')!='complete':raise RuntimeError('Refresh is incomplete. Previous settings retained.')
        self.stage.set(report['stage']);self.swap.set(report['swap'])
        self.current_workspace.set('');self.output_pak.set('');self.archive_label.set('');self.external_pak.set('')
        self.persist();self.reload_archives();self.update_launch_state()
        messagebox.showinfo('Originals refreshed','The new set is selected. Existing data and editing projects were retained.\n\nUnpack the updated PAKs and review/reapply your edits. Old projects cannot be built against the new set.')

    def after_restore(self):
        self.update_launch_state()
        messagebox.showinfo('Restore finished','Restoration script finished. Check the activity log to confirm all originals were restored.')

    def check_setup(self):
        notes=package_status(Path(self.stage.get()),Path(self.swap.get()))
        st=self.update_launch_state()
        msg=('Setup looks ready.' if not notes else 'Some things need attention:\n\n'+'\n'.join('• '+x for x in notes))
        msg += '\n\nSwap status: '+st.message
        self.write('SETUP CHECK: '+msg.replace('\n',' | ')+'\n')
        messagebox.showinfo('Setup check',msg)

    def save_settings(self):
        try:self.persist();self.reload_archives();self.update_launch_state();messagebox.showinfo('Saved','Folder settings saved.')
        except Exception as e:self.fail(e)

    def open_projects(self):
        try:
            p=Path(self.projects.get());p.mkdir(parents=True,exist_ok=True);open_folder(p)
        except Exception as e:self.fail(e)

    def write(self,line):
        if not hasattr(self,'log'):return
        self.log.insert(tk.END,line);self.log.see(tk.END)

    def fail(self,error):
        self.toggle_log(show=True)
        self.write(f'ERROR: {type(error).__name__}: {error}\n')
        self.status.set('Could not continue: '+str(error))
        messagebox.showerror('Could not continue',str(error))

    def run_steps(self,steps,label,on_success=None,cwd=ROOT):
        if self.busy:raise RuntimeError('Wait for the current task to finish.')
        self.busy=True
        self.toggle_log(show=True)
        self.status.set(label+'...')
        self.write('\n>>> '+label+'\n')
        def task():
            rc=0
            try:
                for step_name,command in steps:
                    self.events.put(('text','> '+step_name+'\n'))
                    p=subprocess.Popen(worker_command(command),creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0),cwd=str(cwd),stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1)
                    for line in p.stdout:
                        self.events.put(('text',line))
                    rc=p.wait()
                    if rc:
                        self.events.put(('text',f'FAILED step: {step_name}; exit code {rc}\n'))
                        break
            except Exception as e:
                rc=-1
                self.events.put(('text',f'FAILED TO START: {e}\n'))
            self.events.put(('done',(rc,label,on_success)))
        threading.Thread(target=task,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,val=self.events.get_nowait()
                if kind == 'update-check':
                    self.on_update_check(*val)
                elif kind == 'update-progress':
                    self._set_update_progress(*val)
                elif kind == 'update-download':
                    self.on_update_download(*val)
                elif kind=='text':
                    self.write(val)
                    if 'Automatic CreateProcessW hook ready' in val:
                        self.status.set('Launcher is ready — press Play in the Evolve window now.')
                        self.launch_state.set('Launcher is open. Press Play in Evolve to start your modded game.')
                    elif 'SWAP COMPLETE' in val:
                        self.status.set('Mod files swapped. Evolve is starting...')
                elif kind=='done':
                    rc,label,callback=val
                    self.busy=False
                    if self._update_prompt_pending:
                        self.window.after(100, self._offer_pending_update)
                    if rc==0:
                        self.status.set('Completed: '+label)
                        self.write('>>> SUCCESS\n')
                        if callback:
                            try:callback()
                            except Exception as e:self.fail(e)
                        if label in ('Play with Mods','Restore originals','Add PAK'):
                            self.update_launch_state()
                    else:
                        self.status.set(f'FAILED: {label} (exit code {rc}) — see activity log')
                        self.toggle_log(show=True)
                        self.write('>>> FAILED. No automatic repair attempted; keep journals/backups intact.\n')
                        if label in ('Play with Mods','Restore originals'):
                            self.update_launch_state()
        except queue.Empty:
            pass
        self.window.after(120,self.poll)

    def on_close(self):
        if self.busy or self.update_in_progress:
            messagebox.showwarning('Still running','Wait for the current operation to complete. Closing now could interrupt your swap or rebuild.')
            return
        if not self.flush_editors():return
        try:self.persist()
        except Exception:pass
        self.window.destroy()


if __name__=='__main__':
    # Give Windows a stable taskbar identity rather than Python's generic group.
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Evolve.Stage2.ModManager')
        except (AttributeError, OSError):
            pass
    app=tk.Tk()
    Manager(app)
    # Only acknowledge once Tk has started processing its event loop.
    app.after(400, lambda: report_startup_ready(APP_HOME))
    app.mainloop()
