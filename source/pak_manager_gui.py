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
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from launch_integration import (
    add_missing_scripts, launcher_commands, missing_scripts,
    package_status, restore_command, state_status,
)
from universal_stage import allowed_paks
from app_runtime import APP_HOME, DATA_HOME, SETTINGS_FILE, settings_source, save_settings, worker_command
from dark_theme import apply_theme
from ui_copy import load_text
from workspace_editor import WorkspaceEditor
from portable_bundle import bind_home, MARKER
from old_prepared import scan_prepared

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
        self.window.geometry('980x690')
        self.window.minsize(800,580)
        self.events = queue.Queue()
        self.busy = False
        self.editors = []
        self.archive_entries = []
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
        self.bundle_error = ''
        self.bundle = None
        self.read_settings()
        self.draw()
        if self.ui_warning:self.write('UI TEXT: '+self.ui_warning+'\n')
        self.reload_archives()
        self.update_launch_state()
        self.window.after(120, self.poll)
        self.window.protocol('WM_DELETE_WINDOW',self.on_close)
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
                for name in ('stage','swap','projects','current_workspace','output_pak','archive_label'):
                    if name in data and isinstance(data[name],str):
                        getattr(self,name).set(data[name])
            except (OSError, ValueError):
                pass

    def persist(self):
        data={name:getattr(self,name).get() for name in (
            'stage','swap','projects','current_workspace','output_pak','archive_label')}
        save_settings(data)

    def t(self, name):
        """Lookup a developer-configured label (unknown keys remain visible)."""
        return self.copy.get(name, name.replace('_', ' ').capitalize())

    def draw(self):
        root = self.window
        base = ttk.Frame(root, padding=(14, 10))
        base.pack(fill='both', expand=True)
        header_row = ttk.Frame(base)
        header_row.pack(fill='x')
        ttk.Label(header_row, text=self.t('header'), foreground='#ff4255',
                  font=('Segoe UI', 17, 'bold')).pack(side='left')
        ttk.Button(header_row, text=self.t('setup_button'), command=self.initial_setup).pack(side='right', padx=(6, 0))
        self.log_button = ttk.Button(header_row, text=self.t('details_show'), command=self.toggle_log)
        self.log_button.pack(side='right')
        ttk.Label(base, text=self.t('subtitle'), foreground='#b5b5bf').pack(anchor='w', pady=(2, 7))
        notebook = ttk.Notebook(base)
        notebook.pack(fill='both', expand=True)
        self.edit_tab = ttk.Frame(notebook, padding=10)
        self.play_tab = ttk.Frame(notebook, padding=10)
        self.settings_tab = ttk.Frame(notebook, padding=10)
        notebook.add(self.edit_tab, text='  '+self.t('tab_edit')+'  ')
        notebook.add(self.play_tab, text='  '+self.t('tab_play')+'  ')
        notebook.add(self.settings_tab, text='  '+self.t('tab_settings')+'  ')
        self.notebook = notebook
        self.draw_edit(self.edit_tab)
        self.draw_play(self.play_tab)
        self.draw_settings(self.settings_tab)
        self.log_visible = False
        self.log_window = tk.Toplevel(root)
        self.log_window.withdraw()
        self.log_window.title('Activity Log | ' + self.t('window_title'))
        self.log_window.geometry('850x340')
        self.log_window.minsize(550, 220)
        self.log_window.protocol('WM_DELETE_WINDOW', lambda: self.toggle_log(show=False))
        self.log_frame = ttk.Frame(self.log_window, padding=9)
        self.log_frame.pack(fill='both', expand=True)
        self.log = tk.Text(self.log_frame, height=6, font=('Consolas', 9), wrap='word', relief='flat')
        self.log.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(self.log_frame, orient='vertical', command=self.log.yview)
        scroll.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=scroll.set)
        bottom = ttk.Frame(base)
        bottom.pack(fill='x', pady=(6, 0))
        ttk.Label(bottom, textvariable=self.status).pack(side='left')
        ttk.Label(bottom, text=self.t('footer_note'), foreground='#b5b5bf').pack(side='right')
        self.write('Manager opened. No installed game files were changed.\n')

    def toggle_log(self, show=None):
        show = not self.log_visible if show is None else bool(show)
        if show == self.log_visible:
            return
        self.log_visible = show
        if show:
            self.log_window.deiconify()
            self.log_window.lift()
        else:
            self.log_window.withdraw()
        self.log_button.configure(text=self.t('details_hide' if show else 'details_show'))

    def draw_edit(self, parent):
        # The activity log has its own window. Give unused space to the PAK list
        # and let the user resize it without hiding the editing controls.
        self.edit_split = tk.PanedWindow(
            parent, orient=tk.VERTICAL, background='#34343b', borderwidth=0,
            sashwidth=9, sashpad=4, sashrelief=tk.RAISED,
            showhandle=True, handlesize=12, handlepad=16,
            opaqueresize=True, cursor='sb_v_double_arrow')
        self.edit_split.pack(fill='both', expand=True)
        select = ttk.LabelFrame(self.edit_split, text=self.t('choose_group'), padding=8)
        self.edit_split.add(select, minsize=135, stretch='always')
        line = ttk.Frame(select)
        line.pack(fill='x')
        ttk.Label(line, text=self.t('search_label')).pack(side='left')
        ttk.Entry(line, textvariable=self.search).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(line, text=self.t('refresh_list'), command=self.reload_archives).pack(side='left')
        ttk.Button(line, text=self.t('browse_pak'), command=self.browse_pak).pack(side='left', padx=(5, 0))
        self.search.trace_add('write', lambda *_: self.refresh_list())
        archive_area = ttk.Frame(select)
        archive_area.pack(fill='both', expand=True, pady=(5, 0))
        self.archives = tk.Listbox(archive_area, height=9, exportselection=False, font=('Consolas', 10))
        self.archives.pack(side='left', fill='both', expand=True)
        archive_scroll = ttk.Scrollbar(archive_area, orient='vertical', command=self.archives.yview)
        archive_scroll.pack(side='right', fill='y')
        self.archives.configure(yscrollcommand=archive_scroll.set)
        self.archives.bind('<<ListboxSelect>>', self.select_archive)
        self.archives.bind('<Double-Button-1>', lambda *_: self.extract())
        status_line = ttk.Frame(select)
        status_line.pack(fill='x', pady=(4, 0))
        ttk.Label(status_line, textvariable=self.archive_count, foreground='#b5b5bf').pack(side='left')
        ttk.Label(status_line, text='↕ Drag divider below to resize', foreground='#b5b5bf').pack(side='left', padx=16)
        ttk.Label(status_line, textvariable=self.archive_label, foreground='#ff6370').pack(side='right')
        lower = ttk.Frame(self.edit_split)
        self.edit_split.add(lower, minsize=185, stretch='never')
        actions = ttk.LabelFrame(lower, text=self.t('edit_group'), padding=8)
        actions.pack(fill='x')
        ttk.Label(actions, text=self.t('edit_instructions')).pack(anchor='w')
        row = ttk.Frame(actions)
        row.pack(fill='x', pady=(5, 0))
        ttk.Button(row, text=self.t('unpack_button'), command=self.extract).pack(side='left', fill='x', expand=True, padx=(0, 5))
        ttk.Button(row, text=self.t('edit_button'), command=self.open_editable).pack(side='left', fill='x', expand=True, padx=(0, 5))
        ttk.Button(row, text=self.t('review_button'), command=self.diff).pack(side='left', fill='x', expand=True)
        ttk.Label(actions, text=self.t('edit_note'), foreground='#b5b5bf').pack(anchor='w', pady=(4, 0))
        finish = ttk.LabelFrame(lower, text=self.t('build_group'), padding=8)
        finish.pack(fill='x', pady=(7, 0))
        ttk.Label(finish, text=self.t('build_instructions')).pack(anchor='w')
        row = ttk.Frame(finish)
        row.pack(fill='x', pady=(5, 0))
        ttk.Button(row, text=self.t('build_button'), style='Accent.TButton', command=self.build).pack(side='left', fill='x', expand=True, padx=(0, 5))
        ttk.Button(row, text=self.t('add_button'), command=self.install).pack(side='left', fill='x', expand=True)
        tail = ttk.Frame(finish)
        tail.pack(fill='x', pady=(4, 0))
        ttk.Button(tail, text=self.t('undo_button'), command=self.rollback).pack(side='left')
        ttk.Label(tail, text=self.t('go_play'), foreground='#b5b5bf').pack(side='right')

    def draw_play(self, parent):
        ttk.Label(parent, text=self.t('play_title'), font=('Segoe UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(parent, text=self.t('play_hint'), foreground='#b5b5bf').pack(anchor='w', pady=(2, 9))
        panel = ttk.LabelFrame(parent, text=self.t('play_group'), padding=11)
        panel.pack(fill='x')
        ttk.Label(panel, textvariable=self.launch_state, wraplength=780,
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        btns = ttk.Frame(panel)
        btns.pack(fill='x', pady=(10, 7))
        ttk.Button(btns, text=self.t('play_button'), style='Accent.TButton', command=self.launch).pack(side='left', fill='x', expand=True, padx=(0, 5), ipady=7)
        ttk.Button(btns, text=self.t('restore_button'), command=self.restore).pack(side='left', fill='x', expand=True, ipady=7)
        row = ttk.Frame(panel)
        row.pack(fill='x')
        ttk.Button(row, text=self.t('refresh_status'), command=self.update_launch_state).pack(side='left')
        ttk.Button(row, text=self.t('setup_helper'), command=self.setup_launcher).pack(side='left', padx=(6, 0))
        ttk.Button(row, text=self.t('refresh_originals'), command=self.refresh_originals).pack(side='left', padx=(6, 0))
        steps = ttk.LabelFrame(parent, text=self.t('play_help_group'), padding=11)
        steps.pack(fill='x', pady=(10, 0))
        for key in ('play_step1', 'play_step2', 'play_step3'):
            ttk.Label(steps, text=self.t(key), wraplength=800).pack(anchor='w', pady=3)
        ttk.Separator(steps).pack(fill='x', pady=7)
        ttk.Label(steps, text=self.t('play_after'), font=('Segoe UI', 10, 'bold'),
                  wraplength=800).pack(anchor='w')

    def draw_settings(self, parent):
        ttk.Label(parent, text=self.t('settings_title'), font=('Segoe UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(parent, text=self.t('settings_hint'), foreground='#b5b5bf').pack(anchor='w', pady=(2, 8))
        settings = ttk.LabelFrame(parent, text=self.t('folders_group'), padding=10)
        settings.pack(fill='x')
        for i, (key, var, browse) in enumerate((
            ('stage_label', self.stage, self.browse_stage),
            ('swap_label', self.swap, self.browse_swap),
            ('projects_label', self.projects, self.browse_projects),
        )):
            ttk.Label(settings, text=self.t(key), width=23).grid(row=i, column=0, sticky='w', pady=4)
            ttk.Entry(settings, textvariable=var).grid(row=i, column=1, sticky='ew', padx=6)
            ttk.Button(settings, text=self.t('browse_button'), command=browse).grid(row=i, column=2, sticky='e')
        settings.columnconfigure(1, weight=1)
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(9, 4))
        ttk.Button(row, text=self.t('setup_button'), style='Accent.TButton', command=self.initial_setup).pack(side='left', padx=(0, 6))
        ttk.Button(row, text=self.t('check_setup'), command=self.check_setup).pack(side='left')
        ttk.Button(row, text=self.t('open_projects'), command=self.open_projects).pack(side='left', padx=(6, 0))
        ttk.Button(row, text=self.t('save_settings'), style='Accent.TButton', command=self.save_settings).pack(side='left', padx=(6, 0))
        ttk.Separator(parent).pack(fill='x', pady=(8, 7))
        self.advanced_button = ttk.Button(parent, text=self.t('advanced_toggle_show'), command=self.toggle_advanced)
        self.advanced_button.pack(anchor='w')
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
            self.archives.selection_clear(0,tk.END)
            self.write('Selected external file: '+p+'\n')

    def reload_archives(self):
        try:
            stage=Path(self.stage.get())
            if (stage/'rekey_plan.json').is_file():
                self.archive_entries=sorted(allowed_paks(stage).values(),key=str.casefold)
            else:
                self.archive_entries=sorted([p.relative_to(stage/'paks').as_posix() for p in (stage/'paks').rglob('*.pak')],key=str.casefold)
            self.write(f'Found {len(self.archive_entries)} custom-signed PAK archives.\n')
        except Exception as e:
            self.archive_entries=[]
            self.write(f'Could not list archives: {e}\n')
        self.refresh_list()

    def refresh_list(self):
        if not hasattr(self,'archives'):return
        needle=self.search.get().casefold()
        self.visible=[x for x in self.archive_entries if needle in x.casefold()]
        self.archives.delete(0,tk.END)
        for rel in self.visible:self.archives.insert(tk.END,rel)
        self.archive_count.set(self.t('pak_count').replace('{count}', str(len(self.visible))) if self.archive_entries else self.t('no_paks'))
        if self.archive_label.get() in self.visible:
            pos=self.visible.index(self.archive_label.get())
            self.archives.selection_set(pos)
            self.archives.see(pos)

    def select_archive(self,event=None):
        ix=self.archives.curselection()
        if not ix:return
        rel=self.visible[ix[0]]
        if self.archive_label.get()!=rel:
            self.current_workspace.set('');self.output_pak.set('')
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

    def after_extract(self,ws):
        self.current_workspace.set(str(ws));self.output_pak.set('')
        self.persist()
        self.write('EDITABLE FILES: '+str(ws/'files')+'\n')
        self.open_editable()

    def open_editable(self):
        try:
            p=self.workspace()/'files'
            if not p.is_dir():raise FileNotFoundError('Unpacked file folder missing: '+str(p))
            for editor in self.editors:
                if editor.window.winfo_exists() and editor.workspace == self.workspace():
                    editor.window.lift(); return
            self.editors.append(WorkspaceEditor(self,self.workspace()))
        except Exception as e:self.fail(e)

    def flush_editors(self):
        for editor in self.editors:
            if editor.window.winfo_exists() and not editor.confirm():return False
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
        self.launch_state.set(st.message + (f'  ({st.ready}/{st.count} mod files prepared)' if st.count else ''))
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
            if st.phase in ('missing','prepared','restored'):
                messagebox.showinfo('Nothing to restore','Your original game files are already in place. No restoration is needed.')
                return
            if not messagebox.askyesno(self.t('dialog_restore_title'), self.t('dialog_restore_body')):
                return
            cmd=restore_command(Path(self.swap.get()))
            self.run_steps([('Restoring original game files',cmd)],'Restore originals',self.after_restore,
                           cwd=Path(self.swap.get()))
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
            destination=DATA_HOME/'GameSets'/('Setup_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            if not messagebox.askyesno(self.t('setup_confirm_title'),
                'Close Evolve and the normal client first.\n\n'
                'The manager will copy your original PAKs, generate signing keys and build a local signed set. '
                'Allow up to twice the PAK size plus 1 GiB free space here. Preparing a later game launch needs additional game-drive space.\n\n'
                'Storage: '+str(destination)+'\n\nCreate setup now?'):return
            cmd=[sys.executable,'-u',str(ROOT/'first_run_setup.py'),'--game',game,'--launcher',launcher,
                 '--destination',str(destination),'--seed-parent',str(DATA_HOME/'SetupSeeds')]
            if preserve_old:cmd.append('--preserve-old-prepared')
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
            folder=filedialog.askdirectory(title='Choose storage folder for the NEW originals and signed PAK set',initialdir=str(storage))
            if not folder:return
            destination=Path(folder)/('EvolveRefresh_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            if not messagebox.askyesno('Refresh originals from updated game?',
                'First restore originals, update or repair the game in the normal client, then close the game and client.\n\n'
                'This copies the installed PAKs and builds a fresh signed set. It may need twice the PAK size in free space. '
                'Previous sets and edits are kept. Old mods must be reviewed and reapplied to freshly unpacked files.\n\n'
                'New set: '+str(destination)+'\n\nContinue?'):return
            cmd=[sys.executable,'-u',str(ROOT/'refresh_originals.py'),'--game',game,'--stage',self.stage.get(),
                 '--swap',self.swap.get(),'--destination',str(destination)]
            self.run_steps([('Refreshing original PAKs and signing new set',cmd)],'Refresh Original PAKs',lambda:self.after_refresh(destination))
        except Exception as e:self.fail(e)

    def after_refresh(self,destination):
        report=json.loads((destination/'refresh_report.json').read_text(encoding='utf-8'))
        if report.get('status')!='complete':raise RuntimeError('Refresh is incomplete. Previous settings retained.')
        self.stage.set(report['stage']);self.swap.set(report['swap'])
        self.current_workspace.set('');self.output_pak.set('');self.archive_label.set('');self.external_pak.set('')
        self.persist();self.reload_archives();self.update_launch_state()
        messagebox.showinfo('Originals refreshed','The new set is selected. Previous originals, stages and editing projects were kept.\n\nUnpack the updated PAKs and review/reapply your edits. Old projects cannot be built against the new set.')

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
                if kind=='text':
                    self.write(val)
                    if 'Automatic CreateProcessW hook ready' in val:
                        self.status.set('Launcher is ready — press Play in the Evolve window now.')
                        self.launch_state.set('Launcher is open. Press Play in Evolve to start your modded game.')
                    elif 'SWAP COMPLETE' in val:
                        self.status.set('Mod files swapped. Evolve is starting...')
                elif kind=='done':
                    rc,label,callback=val
                    self.busy=False
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
        if self.busy:
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
    app.mainloop()
