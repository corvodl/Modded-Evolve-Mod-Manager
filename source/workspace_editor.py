"""Integrated browser/editor for existing exported PAK entries."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from evolve_gameplay_editor import parse_cryxml_text
from dds_texture import is_dds, is_split_dds, parse_dds, preview_dds, export_png, replace_dds
from dds_streaming import inspect_stream, inspect_whole_part0, replace_stream
from model_asset import is_model, inspect_model, export_model, replace_model
from model_preview import ModelPreview, find_preview_mesh
from multi_pak_assets import model_material_links
from dds_png_import import encode_png_as_dds, compression_for_dds
from workspace_file_actions import (checked_path, sha_file, replace_raw_file,
                                    extracted_original_backup, restore_extracted_original)

MAX_TEXT = 5 * 1024 * 1024
TEXT_SUFFIXES = {'.xml', '.txt', '.cfg', '.ini', '.lua', '.json', '.csv', '.mtl', '.chrparams', '.cdf', '.animevents', '.lmg', '.bspace', '.comb'}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def validate_xml(data, original=None):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD and entity declarations are not supported.')
    root = parse_cryxml_text(data)
    if original is not None:
        before = parse_cryxml_text(original)
        def check(a, b):
            if a.tag != b.tag or list(a.attrib) != list(b.attrib) or len(a) != len(b):
                raise ValueError('CryXmlB supports existing values only. Keep tags, attributes and their order intact.')
            for x, y in zip(a, b): check(x, y)
        check(before, root)
    return root

def save_text(workspace, relative, text, expected_hash, original, cryxml=False):
    root = (Path(workspace)/'files').resolve()
    path = (root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('The file is missing or outside this project.')
    current = path.read_bytes()
    if digest(current) != expected_hash:
        raise ValueError('This file changed outside the editor. Reload it before saving; your buffer is still available.')
    # Preserve a UTF-8 BOM and newline convention when present.
    newline = '\r\n' if b'\r\n' in original else '\n'
    data = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', newline).encode('utf-8')
    if original.startswith(b'\xef\xbb\xbf'): data = b'\xef\xbb\xbf' + data
    if cryxml or path.suffix.lower() == '.xml':
        validate_xml(data, original if cryxml else None)
    if data == current: return data
    backup = Path(workspace)/'EditorBackups'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')/relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open('xb') as stream: stream.write(current)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.edit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if digest(path.read_bytes()) != expected_hash:
            raise ValueError('File changed during save. Original backup retained; reload before retrying.')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return data

class WorkspaceEditor:
    """Tabbed workspace explorer: Files, Images, and Models each own their actions."""
    TABS = ('files', 'images', 'models')

    @staticmethod
    def category(relative):
        if is_dds(relative) or is_split_dds(relative):
            return 'images'
        if is_model(relative):
            return 'models'
        return 'files'

    @staticmethod
    def visible_entries(records, tab, query=''):
        """Select matching file entries; show one row for each split DDS set."""
        candidates = (r for r in records if WorkspaceEditor.category(r) == tab)
        if tab == 'images':
            # Collapse each fragment family to one entry. If its .dds.0 is
            # missing, show the earliest available part with a helpful error.
            images = list(candidates)
            first_part = {}
            for name in images:
                if is_split_dds(name):
                    base, index = name.rsplit('.', 1)
                    key = base.casefold()
                    number = int(index)
                    if key not in first_part or number < first_part[key]:
                        first_part[key] = number
            candidates = (name for name in images if not is_split_dds(name)
                          or int(name.rsplit('.', 1)[1]) == first_part[name.rsplit('.', 1)[0].casefold()])
        return sorted((r for r in candidates if query.casefold() in r.casefold()), key=str.casefold)

    def __init__(self, manager, workspace, archive_map=None):
        self.manager = manager
        self.workspace = Path(workspace).resolve()
        # Archive selectors are browsing-only; each PAK retains its own isolated
        # workspace, signing manifest, backups, and independent Build Mod step.
        self.archive_map = {name: Path(path).resolve() for name, path in (archive_map or {}).items()}
        self.current_archive = next((name for name, path in self.archive_map.items()
                                     if path == self.workspace), '')
        self._switching_archive = False
        self.records = self._read_records(self.workspace)
        self.path = None
        self.raw = b''
        self.loaded_text = ''
        self.view_mode = None
        self.preview_photo = None
        self.streaming = None
        self.texture_folder = None
        self.active_tab = 'files'
        self._changing_tab = False
        self.window = tk.Toplevel(manager.window)
        self.window.title(manager.t('editor_title'))
        self.window.geometry('1140x740'); self.window.minsize(800, 540)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.query = tk.StringVar()
        bar = ttk.Frame(self.window, padding=8); bar.pack(fill='x')
        ttk.Label(bar, text=manager.t('editor_search')).pack(side='left')
        ttk.Entry(bar, textvariable=self.query, width=44).pack(side='left', padx=8, fill='x', expand=True)
        ttk.Button(bar, text=manager.t('editor_open_folder'), command=self.explore).pack(side='left', padx=(0, 6))
        ttk.Button(bar, text=manager.t('editor_reload'), command=self.reload).pack(side='left')

        # Show every unpacked PAK in the SAME editor popup; clicking one changes
        # the file trees below without mixing or overwriting archive entries.
        self.archive_list = None
        if len(self.archive_map) > 1:
            archives = ttk.LabelFrame(self.window, text=manager.t('editor_archive_selector'), padding=(8, 5))
            archives.pack(fill='x', padx=8, pady=(0, 5))
            self.archive_list = tk.Listbox(archives, height=min(5, len(self.archive_map)),
                                           exportselection=False, selectmode='browse',
                                           background='#1b1b20', foreground='#f2f2f4',
                                           selectbackground='#40576d', selectforeground='white',
                                           font=('Consolas', 10))
            self.archive_list.pack(side='left', fill='x', expand=True)
            scroll = ttk.Scrollbar(archives, orient='vertical', command=self.archive_list.yview)
            scroll.pack(side='right', fill='y')
            self.archive_list.configure(yscrollcommand=scroll.set)
            for archive in self.archive_map:
                self.archive_list.insert(tk.END, archive)
            self._highlight_archive()
            self.archive_list.bind('<<ListboxSelect>>', self.select_archive)

        self.tabs = ttk.Notebook(self.window)
        self.tabs.pack(fill='both', expand=True, padx=8, pady=(0, 6))
        self.tab_frames = {}
        self.trees = {}
        self.tree_nodes = {}
        self.tree_paths = {}
        self.path_items = {}
        self.viewers = {}

        for tab in self.TABS:
            page = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(page, text=manager.t('editor_tab_' + tab))
            self.tab_frames[tab] = page
            tools = ttk.Frame(page)
            tools.pack(fill='x', pady=(0, 7))
            if tab == 'files':
                ttk.Button(tools, text=manager.t('editor_save'), command=self.save).pack(side='left')
                ttk.Label(tools, text=manager.t('editor_files_help')).pack(side='left', padx=10)
            elif tab == 'images':
                self.export_button = ttk.Button(tools, text=manager.t('texture_export'), command=self.export_texture)
                self.export_button.pack(side='left', padx=(0, 6))
                self.import_png_button = ttk.Button(tools, text=manager.t('texture_import_png'), command=self.import_png)
                self.import_png_button.pack(side='left', padx=(0, 6))
                self.import_button = ttk.Button(tools, text=manager.t('texture_import'), command=self.import_texture)
                self.import_button.pack(side='left', padx=(0, 6))
                ttk.Label(tools, text=manager.t('editor_images_help')).pack(side='left', padx=8)
            else:
                self.model_export_button = ttk.Button(tools, text=manager.t('model_export'), command=self.export_model)
                self.model_export_button.pack(side='left', padx=(0, 6))
                self.model_import_button = ttk.Button(tools, text=manager.t('model_import'), command=self.import_model)
                self.model_import_button.pack(side='left', padx=(0, 6))
                ttk.Button(tools, text='Find Model Textures', command=self.show_model_textures).pack(side='left', padx=(0, 6))
                ttk.Button(tools, text='Choose Texture Folder', command=self.choose_texture_folder).pack(side='left', padx=(0, 6))
                ttk.Label(tools, text=manager.t('editor_models_help')).pack(side='left', padx=8)

            pane = ttk.Panedwindow(page, orient='horizontal')
            pane.pack(fill='both', expand=True)
            left = ttk.Frame(pane)
            right = ttk.Frame(pane)
            pane.add(left, weight=1)
            pane.add(right, weight=4)
            tree = ttk.Treeview(left, show='tree', selectmode='browse')
            tree.pack(side='left', fill='both', expand=True)
            scroll = ttk.Scrollbar(left, command=tree.yview)
            scroll.pack(side='right', fill='y')
            tree.configure(yscrollcommand=scroll.set)
            tree.bind('<<TreeviewSelect>>', lambda event, name=tab: self.select(name))
            tree.bind('<Button-3>', lambda event, name=tab: self.show_context_menu(name, event))
            tree.bind('<Shift-F10>', lambda event, name=tab: self.show_context_menu(name, event))
            self.trees[tab] = tree
            self.tree_nodes[tab] = {}
            self.tree_paths[tab] = {}
            self.path_items[tab] = {}
            if tab == 'files':
                self.text = tk.Text(right, wrap='none', undo=True, font=('Consolas', 11),
                                    background='#18181f', foreground='#eeeeef', insertbackground='white')
                self.text.grid(row=0, column=0, sticky='nsew')
                right.rowconfigure(0, weight=1)
                right.columnconfigure(0, weight=1)
                ys = ttk.Scrollbar(right, command=self.text.yview)
                ys.grid(row=0, column=1, sticky='ns')
                xs = ttk.Scrollbar(right, orient='horizontal', command=self.text.xview)
                xs.grid(row=1, column=0, sticky='ew')
                self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set, state='disabled')
                self.viewers[tab] = self.text
            elif tab == 'images':
                self.image_label = tk.Label(right, background='#18181f', foreground='#eeeeef',
                                            text='Select a DDS texture to preview.', compound='top')
                self.image_label.pack(fill='both', expand=True)
                self.viewers[tab] = self.image_label
            else:
                self.model_label = tk.Label(right, background='#18181f', foreground='#eeeeef',
                                            text='Select a CryTek model to inspect.', justify='left',
                                            anchor='nw', padx=16, pady=12)
                self.model_label.pack(fill='both', expand=True)
                self.model_preview = ModelPreview(right)
                self.viewers[tab] = self.model_label

        self.info = tk.StringVar(value=manager.t('editor_hint'))
        ttk.Label(self.window, textvariable=self.info, wraplength=1080, padding=8).pack(fill='x')
        ttk.Label(self.window, text=manager.t('editor_footer'), padding=(8, 0, 8, 8)).pack(fill='x')
        self.query.trace_add('write', lambda *_: self.populate())
        self.tabs.bind('<<NotebookTabChanged>>', self.on_tab_changed)
        self.window.bind('<Control-s>', lambda _: self.save())
        self.window.bind('<Control-f>', lambda _: self.find_text())
        self.reset_views()
        self.populate()

    @staticmethod
    def _read_records(workspace):
        # The editor historically accepts minimal workspace manifests in unit
        # tests and older projects. Validate file paths without requiring a
        # new manifest version or changing existing editing behavior.
        from evolve_pak_workspace import normalized_name
        data = json.loads((Path(workspace)/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))
        entries = data['entries']
        if not isinstance(entries, list):
            raise ValueError('Invalid workspace file inventory.')
        records = {}
        folded = set()
        for row in entries:
            rel = normalized_name(row['path'])
            if rel != row['path'] or rel.casefold() in folded:
                raise ValueError('Invalid or duplicate workspace file path: ' + rel)
            folded.add(rel.casefold())
            records[rel] = row
        return records

    def _highlight_archive(self):
        if self.archive_list is None:
            return
        self._switching_archive = True
        try:
            self.archive_list.selection_clear(0, tk.END)
            if self.current_archive in self.archive_map:
                index = list(self.archive_map).index(self.current_archive)
                self.archive_list.selection_set(index)
                self.archive_list.see(index)
        finally:
            self._switching_archive = False

    def select_archive(self, _event=None):
        if self._switching_archive or self.archive_list is None:
            return
        selected = self.archive_list.curselection()
        if selected:
            self.switch_archive(list(self.archive_map)[selected[0]])

    def switch_archive(self, name):
        """Switch workspace safely while keeping PAK files and builds isolated."""
        if name not in self.archive_map:
            raise ValueError('This PAK is not in the extracted archive collection.')
        target = self.archive_map[name]
        if target == self.workspace:
            self._highlight_archive()
            return True
        if self.manager.busy:
            self.info.set('Wait for the current operation before changing PAKs.')
            self._highlight_archive()
            return False
        if not self.confirm():
            self._highlight_archive()
            return False
        # Validate before committing a switch so a corrupted workspace cannot
        # leave the editor pointing at a partial or nonexistent archive.
        try:
            records = self._read_records(target)
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror('Cannot open unpacked PAK', str(error), parent=self.window)
            self._highlight_archive()
            return False
        self._switching_archive = True
        try:
            for tree in self.trees.values():
                tree.selection_remove(tree.selection())
            self.workspace = target
            self.current_archive = name
            self.records = records
            self.path = None
            self.raw = b''
            self.loaded_text = ''
            self.view_mode = None
            self.reset_views()
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.configure(state='disabled')
            self.populate()
            self.manager.current_workspace.set(str(target))
            self.manager.archive_label.set(name)
            self.manager.output_pak.set('')
            self.manager.persist()
            self.window.title(self.manager.t('editor_title') + ' — ' + name)
            self.info.set('Browsing ' + name + ' | Build Mod applies only to this PAK.')
            self._highlight_archive()
        finally:
            self._switching_archive = False
        return True

    def on_tab_changed(self, _=None):
        if self._changing_tab:
            return
        new_tab = self.TABS[self.tabs.index(self.tabs.select())]
        if new_tab == self.active_tab:
            return
        if not self.confirm():
            self._changing_tab = True
            try:
                self.tabs.select(self.tab_frames[self.active_tab])
            finally:
                self._changing_tab = False
            return
        self.active_tab = new_tab
        self.path = None
        self.view_mode = None
        self.raw = b''
        self.loaded_text = ''
        self.reset_views()
        selection = self.trees[new_tab].selection()
        rel = self.tree_nodes[new_tab].get(selection[0]) if selection else None
        if rel:
            self.load(rel)
        else:
            self.info.set(self.manager.t('editor_tab_' + new_tab) + ' | ' +
                          self.manager.t('editor_select_file'))

    def dirty(self):
        return self.path is not None and self.view_mode == 'text' and self.text.get('1.0','end-1c') != self.loaded_text

    def confirm(self):
        if not self.dirty(): return True
        answer=messagebox.askyesnocancel('Unsaved changes','Save your edits before continuing?',parent=self.window)
        if answer is None:return False
        return self.save() if answer else True

    def populate(self):
        needle = self.query.get()
        for tab in self.TABS:
            tree = self.trees[tab]
            selected_path = self.tree_nodes[tab].get(tree.selection()[0]) if tree.selection() else None
            tree.delete(*tree.get_children())
            paths, files, reverse = {}, {}, {}
            dirs = {'': ''}
            for rel in self.visible_entries(self.records, tab, needle):
                parts = rel.split('/')
                parent = ''
                for index, part in enumerate(parts[:-1]):
                    folder = '/'.join(parts[:index + 1])
                    if folder not in dirs:
                        dirs[folder] = tree.insert(parent, 'end', text=part, open=bool(needle))
                        paths[dirs[folder]] = folder
                    parent = dirs[folder]
                name = parts[-1] + (' (stream)' if is_split_dds(rel) else '')
                node = tree.insert(parent, 'end', text=name)
                files[node] = rel
                paths[node] = rel
                reverse[rel] = node
            self.tree_nodes[tab] = files
            self.tree_paths[tab] = paths
            self.path_items[tab] = reverse
            if selected_path in reverse:
                tree.selection_set(reverse[selected_path])

    def select(self, tab):
        if tab != self.active_tab:
            return
        tree = self.trees[tab]
        selection = tree.selection()
        rel = self.tree_nodes[tab].get(selection[0]) if selection else None
        if rel and rel != self.path:
            if self.confirm():
                self.load(rel)
            elif self.path in self.path_items[tab]:
                tree.selection_set(self.path_items[tab][self.path])

    def _context_text(self, name, fallback):
        """New context labels are developer-configurable, not user overrides."""
        copy = getattr(self.manager, 'copy', {})
        return copy.get('context_' + name, fallback) if isinstance(copy, dict) else fallback

    def _clicked_entry(self, tab, event):
        """Return (relative path, folder flag) for a right-clicked tree node."""
        tree = self.trees[tab]
        node = tree.identify_row(event.y) if getattr(event, 'num', None) == 3 else ''
        if not node and getattr(event, 'num', None) != 3:
            chosen = tree.selection()
            node = chosen[0] if chosen else ''
        if not node:
            return None, None
        relative = self.tree_paths[tab].get(node)
        if relative is None:
            return None, None
        return (relative, node not in self.tree_nodes[tab])

    def show_context_menu(self, tab, event):
        """Windows right-click actions; only the clicked workspace file is changed."""
        relative, is_folder = self._clicked_entry(tab, event)
        if relative is None:
            return 'break'
        tree = self.trees[tab]
        node = tree.identify_row(event.y) if getattr(event, 'num', None) == 3 else ''
        if not node and getattr(event, 'num', None) != 3:
            chosen = tree.selection()
            node = chosen[0] if chosen else ''
        if is_folder:
            menu = tk.Menu(tree, tearoff=False)
            menu.add_command(label=self._context_text('reveal_folder', 'Show Folder in File Explorer'),
                             command=lambda rel=relative: self.reveal_in_explorer(rel, True))
            menu.add_command(label=self._context_text('copy_folder', 'Copy Game Folder Path'),
                             command=lambda rel=relative: self.copy_game_path(rel))
            menu.add_separator()
            menu.add_command(label=self._context_text('expand_folder', 'Expand / Collapse'),
                             command=lambda item=node: tree.item(item, open=not tree.item(item, 'open')))
        else:
            # Right-click must not bypass the unsaved-buffer prompt or use the
            # previous file's raw bytes after the user clicks a new file.
            if tab == self.active_tab and relative != self.path:
                if not self.confirm():
                    return 'break'
                tree.selection_set(node)
                self.load(relative)
            elif tab != self.active_tab:
                return 'break'
            menu = self._file_context_menu(tab, relative)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()
        return 'break'

    def _file_context_menu(self, tab, relative):
        menu = tk.Menu(self.trees[tab], tearoff=False)
        current = (self.path == relative)
        view_ok = current and self.view_mode not in ('unavailable', None)
        menu.add_command(label=self._context_text('preview', 'Edit / Preview'), command=lambda rel=relative: self.context_preview(rel))
        menu.add_separator()
        if tab == 'images':
            menu.add_command(label=self._context_text('import_png', 'Import PNG as DDS...'), command=self.import_png,
                             state='normal' if view_ok else 'disabled')
            menu.add_command(label=self._context_text('import_dds', 'Import Compatible DDS...'), command=self.import_texture,
                             state='normal' if view_ok else 'disabled')
            menu.add_command(label=self._context_text('export_png', 'Export PNG...'), command=self.export_texture,
                             state='normal' if view_ok else 'disabled')
        elif tab == 'models':
            menu.add_command(label=self._context_text('import_model', 'Import Model (Experimental)...'), command=self.import_model,
                             state='normal' if view_ok else 'disabled')
            menu.add_command(label=self._context_text('export_model', 'Export Native Model...'), command=self.export_model,
                             state='normal' if view_ok else 'disabled')
            menu.add_command(label=self._context_text('find_textures', 'Find Model Textures'), command=self.show_model_textures,
                             state='normal' if view_ok else 'disabled')
        else:
            menu.add_command(label=self._context_text('replace_file', 'Import / Replace File...'), command=self.import_generic_file)
            if self.view_mode == 'text' and current:
                menu.add_command(label=self._context_text('save', 'Save Text Changes'), command=self.save)
        menu.add_command(label=self._context_text('export_file', 'Export File...'), command=lambda rel=relative: self.export_raw_file(rel))
        menu.add_separator()
        menu.add_command(label=self._context_text('reveal', 'Show in File Explorer'), command=lambda rel=relative: self.reveal_in_explorer(rel))
        menu.add_command(label=self._context_text('open_default', 'Open With Default App'), command=lambda rel=relative: self.open_extracted_file(rel))
        menu.add_command(label=self._context_text('copy_path', 'Copy Game File Path'), command=lambda rel=relative: self.copy_game_path(rel))
        menu.add_separator()
        # Split streaming DDS is a coordinated set, not an independent file.
        # Never partially restore a fragment without a transaction for ALL parts.
        sha = self.records[relative].get('sha256', '')
        try:
            current_hash = sha_file(checked_path(self.workspace, relative))
            original = (extracted_original_backup(self.workspace, relative, sha)
                        if not is_split_dds(relative) and sha and current_hash != sha else None)
        except (OSError, ValueError):
            original = None
        menu.add_command(label=self._context_text('restore', 'Restore Extracted Original...'),
                         command=lambda rel=relative: self.restore_original(rel),
                         state='normal' if original is not None else 'disabled')
        return menu

    def context_preview(self, relative):
        # A menu action must not discard a dirty text buffer silently.
        if not self.confirm():
            return
        self.load(relative)

    def copy_game_path(self, relative):
        self.window.clipboard_clear()
        self.window.clipboard_append(relative.replace('/', '\\'))
        self.info.set('Copied virtual game path: ' + relative)

    def reveal_in_explorer(self, relative, directory=False):
        try:
            target = checked_path(self.workspace, relative, directory=directory)
            if os.name != 'nt':
                raise OSError('Show in File Explorer is available on Windows.')
            if directory:
                subprocess.Popen(['explorer.exe', str(target)])
            else:
                subprocess.Popen(['explorer.exe', '/select,', str(target)])
        except (OSError, ValueError) as error:
            messagebox.showerror('Cannot reveal file', str(error), parent=self.window)

    def open_extracted_file(self, relative):
        try:
            target = checked_path(self.workspace, relative)
            if target.suffix.casefold() in {'.exe', '.com', '.cmd', '.bat', '.ps1', '.vbs', '.js', '.scr'}:
                raise ValueError('Opening executable scripts from a PAK is disabled for safety.')
            if os.name != 'nt':
                raise OSError('Default application opening is available on Windows.')
            os.startfile(str(target))
        except (OSError, ValueError) as error:
            messagebox.showerror('Cannot open file', str(error), parent=self.window)

    def export_raw_file(self, relative):
        from tkinter import filedialog
        try:
            source = checked_path(self.workspace, relative)
            filename = Path(relative).name
            destination = filedialog.asksaveasfilename(parent=self.window,
                         title='Export extracted file', initialfile=filename)
            if not destination:
                return
            target = Path(destination).resolve()
            if source == target:
                raise ValueError('Export destination cannot be the original extracted file.')
            shutil.copyfile(source, target)
            self.info.set('Exported: ' + relative + ' -> ' + str(target))
        except (OSError, ValueError) as error:
            messagebox.showerror('Export failed', str(error), parent=self.window)

    def import_generic_file(self):
        """Import text with CryXML guards or opt-in raw binary with a backup."""
        if self.active_tab != 'files' or not self.path:
            return
        if self.manager.busy:
            messagebox.showerror('Wait for current task', 'Finish the current operation first.', parent=self.window)
            return
        if not self.confirm():
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.window,
                    title='Choose replacement for ' + Path(self.path).name)
        if not path:
            return
        rel = self.path
        try:
            target = checked_path(self.workspace, rel)
            before = target.read_bytes()
            if self.view_mode == 'text' and digest(before) != digest(self.raw):
                raise ValueError('File changed externally. Reload before importing a replacement.')
            record = self.records[rel]
            # CryXML is exported as text; never write arbitrary raw bytes over it.
            if record['format'] == 'cryxml' or target.suffix.casefold() in TEXT_SUFFIXES:
                replacement = Path(path).read_bytes()
                if len(replacement) > MAX_TEXT:
                    raise ValueError('Text replacement exceeds the 5 MiB editor limit.')
                candidate = replacement.decode('utf-8-sig')
                if '\x00' in candidate:
                    raise ValueError('Cannot import binary bytes into a text file.')
                save_text(self.workspace, rel, candidate, digest(before), before,
                          cryxml=record['format'] == 'cryxml')
            else:
                if not messagebox.askyesno('Replace binary file?',
                        'This is an unknown binary format. Replacement may not work in Evolve.\n\n'
                        'Replace this extracted file and keep the previous bytes in EditorBackups?',
                        parent=self.window):
                    return
                replace_raw_file(self.workspace, rel, path, digest(before))
            self.load(rel)
            self.info.set('Imported replacement: ' + rel + ' | Previous bytes backed up')
        except (OSError, ValueError, RuntimeError, UnicodeError) as error:
            messagebox.showerror('Import rejected', str(error), parent=self.window)

    def restore_original(self, relative):
        if self.manager.busy:
            messagebox.showerror('Wait for current task', 'Finish the current operation first.', parent=self.window)
            return
        if is_split_dds(relative):
            messagebox.showerror('Cannot restore part of a stream',
                'A split DDS needs every original fragment restored together. Automated stream restore is not supported.',
                parent=self.window)
            return
        if not self.confirm():
            return
        try:
            target = checked_path(self.workspace, relative)
            original_hash = self.records[relative].get('sha256', '')
            current_hash = sha_file(target)
            if current_hash == original_hash:
                self.info.set('Already matches extracted original: ' + relative)
                return
            if not messagebox.askyesno('Restore extracted original?',
                    'Restore the original extracted contents of ' + relative + '?\n\n'
                    'Current edited bytes will be saved to EditorBackups.\n'
                    'Other PAK files will not be modified.', parent=self.window):
                return
            restore_extracted_original(self.workspace, relative, original_hash, current_hash)
            self.load(relative)
            self.info.set('Restored extracted original: ' + relative + ' | Previous bytes backed up')
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Restore failed', str(error), parent=self.window)

    def reset_views(self):
        self.preview_photo = None
        self.streaming = None
        self.image_label.configure(image='', text='Select a DDS texture to preview.')
        self.model_preview.clear()
        self.model_preview.frame.pack_forget()
        if not self.model_label.winfo_manager():
            self.model_label.pack(fill='both', expand=True)
        self.model_label.configure(text='Select a CryEngine model or mesh companion to inspect.')
        self.import_button.configure(state='disabled')
        self.import_png_button.configure(state='disabled')
        self.export_button.configure(state='disabled')
        self.model_import_button.configure(state='disabled')
        self.model_export_button.configure(state='disabled')

    def load(self,rel):
        try:
            root=(self.workspace/'files').resolve(); file=(root/rel).resolve()
            if not file.is_relative_to(root):raise ValueError('File resolves outside project.')
            if self.category(rel) != self.active_tab:raise ValueError('Selected file belongs to a different tab.')
            if is_split_dds(rel):
                if rel.casefold().endswith('.dds.0'):
                    try:
                        standalone = inspect_whole_part0(self.workspace, rel)
                    except ValueError:
                        pass  # Real split DDS: inspect_stream checks every required part.
                    else:
                        self.show_texture(rel, standalone)
                        return
                stream = inspect_stream(self.workspace, rel)
                self.show_texture(rel, stream.merged, stream=stream)
                return
            if is_model(rel):
                self.show_model(rel, file.read_bytes())
                return
            if is_dds(rel):
                raw=file.read_bytes()
                self.show_texture(rel, raw)
                return
            if file.stat().st_size>MAX_TEXT:raise ValueError('File exceeds the 5 MiB text editor limit. Use an external editor.')
            raw=file.read_bytes()
            if self.records[rel]['format']!='cryxml' and file.suffix.lower() not in TEXT_SUFFIXES:
                raise ValueError('Binary/unsupported file. Open its folder to use a suitable editor.')
            text=raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            if '\x00' in text:raise ValueError('Binary file cannot be edited as text.')
            self.reset_views()
            self.path=rel;self.raw=raw;self.loaded_text=text;self.view_mode='text'
            self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',text);self.text.edit_reset()
            self.info.set(rel+' | UTF-8 | '+('CryXmlB: edit existing values only' if self.records[rel]['format']=='cryxml' else 'Text file'))
        except Exception as e:
            # File browsing should never present a modal error on each click.
            # Unsupported/missing streams instead show their problem in the
            # preview area; explicit Import/Export actions still show dialogs.
            self.reset_views()
            self.path = rel
            self.raw = b''
            self.loaded_text = ''
            self.view_mode = 'unavailable'
            detail = str(e)
            if self.active_tab == 'images':
                tip = ('\n\nFor split streaming textures, extract all parts '
                       '(.dds.0 through .dds.N) from the same PAK. '
                       'Single-file .dds.0 textures are also supported when complete.')
                self.image_label.configure(image='', text='Texture preview unavailable\n\n' + detail + tip)
            elif self.active_tab == 'models':
                self.model_label.configure(text='Model preview unavailable\n\n' + detail)
            else:
                self.text.configure(state='normal')
                self.text.delete('1.0', 'end')
                self.text.insert('1.0', 'Cannot open this file as text.\n\n' + detail)
                self.text.configure(state='disabled')
            self.info.set(rel + ' | ' + detail)

    def show_texture(self, rel, raw, stream=None):
        from PIL import ImageTk
        info = parse_dds(raw)
        image = preview_dds(raw)
        photo = ImageTk.PhotoImage(image, master=self.window)
        self.reset_views()
        self.path = rel
        self.raw = raw
        self.streaming = stream
        self.loaded_text = ''
        self.view_mode = ('dds_stream' if stream is not None else
                          'dds0_whole' if is_split_dds(rel) else 'dds')
        self.image_label.configure(image=photo, text=info.description + '\nPreview shows the largest mip level only')
        self.preview_photo = photo  # Tk images must remain referenced.
        self.import_button.configure(state='normal')
        self.export_button.configure(state='normal')
        try:
            compression_for_dds(raw)
        except ValueError:
            self.import_png_button.configure(state='disabled')
        else:
            self.import_png_button.configure(state='normal')
        self.info.set(('Streaming DDS: ' + str(stream.count) + ' files | ' if stream else 'DDS texture: ') + rel + ' | ' + info.description)

    def export_texture(self):
        if self.view_mode not in ('dds', 'dds_stream', 'dds0_whole') or not self.path:return
        from tkinter import filedialog
        basename = re.sub(r'\.dds(?:\.\d+)?$', '', Path(self.path).name, flags=re.I)
        dest = filedialog.asksaveasfilename(parent=self.window, title='Export DDS as PNG',
                    initialfile=basename + '.png', defaultextension='.png',
                    filetypes=[('PNG image', '*.png')])
        if not dest:return
        try:
            export_png(self.raw, Path(dest))
            self.info.set('Exported PNG (top mip only): ' + dest)
        except Exception as e:messagebox.showerror('PNG export failed',str(e),parent=self.window)

    def import_texture(self):
        if self.view_mode not in ('dds', 'dds_stream', 'dds0_whole') or not self.path:return
        if self.manager.busy:
            messagebox.showerror('Wait for current task','A manager operation is still running.',parent=self.window);return
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.window, title='Select replacement DDS',
                     filetypes=[('DDS textures', '*.dds')])
        if not path:return
        try:
            rel = self.path
            if self.streaming is not None:
                stream = replace_stream(self.workspace, rel, path, self.streaming.hashes)
                self.show_texture(rel, stream.merged, stream=stream)
            else:
                result = replace_dds(self.workspace, rel, path, digest(self.raw))
                self.show_texture(rel, result)
            self.info.set('Imported compatible DDS: ' + rel + ' | Original kept in EditorBackups')
        except Exception as e:messagebox.showerror('DDS import rejected',str(e),parent=self.window)

    def import_png(self):
        if self.active_tab != 'images' or self.view_mode not in ('dds', 'dds_stream', 'dds0_whole') or not self.path:
            return
        if self.manager.busy:
            messagebox.showerror('Wait for current task', 'A manager operation is still running.', parent=self.window)
            return
        from tkinter import filedialog
        png_path = filedialog.askopenfilename(parent=self.window, title='Import an edited PNG',
                                              filetypes=[('PNG images', '*.png')])
        if not png_path:
            return
        try:
            converted = encode_png_as_dds(self.raw, png_path)
            # Keep the existing transactional DDS/stream import logic and backups.
            with tempfile.TemporaryDirectory(prefix='evolve-dds-import-') as temp:
                candidate = Path(temp) / 'converted.dds'
                candidate.write_bytes(converted)
                rel = self.path
                if self.streaming is not None:
                    new_stream = replace_stream(self.workspace, rel, candidate, self.streaming.hashes)
                    self.show_texture(rel, new_stream.merged, stream=new_stream)
                else:
                    updated = replace_dds(self.workspace, rel, candidate, digest(self.raw))
                    self.show_texture(rel, updated)
            self.info.set('Imported PNG as compatible DDS: ' + rel + ' | Backups kept in EditorBackups')
        except Exception as error:
            messagebox.showerror('PNG import rejected', str(error), parent=self.window)

    def _asset_roots(self):
        """Only search extracted workspaces under the current manager stage."""
        def configured(name):
            field = getattr(self.manager, name, None)
            return field.get() if field is not None and hasattr(field, 'get') else None
        return {'projects_root': configured('projects'), 'stage_root': configured('stage')}

    def show_model(self, rel, raw):
        self.reset_views()
        self.path = rel
        self.raw = raw
        self.loaded_text = ''
        self.view_mode = 'model'
        self.model_export_button.configure(state='normal')
        try:
            info = inspect_model(raw)
            report = info.description + '\nNative CryEngine chunked model recognized.\nImport requires identical chunk table, stream descriptors, and binary length.'
            self.model_import_button.configure(state='normal')
        except ValueError as error:
            report = 'Experimental model support\n' + str(error) + '\nExport the native file to inspect it with an external application.'
        try:
            mesh = find_preview_mesh(self.workspace, rel, raw, **self._asset_roots())
        except ValueError as error:
            self.model_label.configure(text=rel + '\n\n' + report +
                                       '\n\n3D preview unavailable: ' + str(error) +
                                       '\nThis viewer does not convert meshes for Blender.')
        else:
            self.model_label.pack_forget()
            self.model_preview.frame.pack(fill='both', expand=True)
            self.model_preview.set_mesh(mesh)
            self.apply_model_materials(rel)
        self.info.set('Model: ' + rel + ' | Native export available; replacement is experimental')

    def apply_model_materials(self, rel):
        # Material/texture PAKs may be stored in another batch workspace.
        # Missing textures are common and must not prevent mesh inspection.
        try:
            from material_preview import load_preview_materials
            material = load_preview_materials(self.workspace, rel, self.texture_folder, **self._asset_roots())
        except (ValueError, OSError) as error:
            self.model_preview.set_materials(None)
            self.model_preview.details.set('Untextured | ' + str(error)[:180] + ' | Unpack the texture PAK or choose a texture folder')
        else:
            self.model_preview.set_materials(material)
            self.info.set('UV material preview: ' + material.source +
                          f' | {material.count} diffuse textures (approximate lighting)')

    def choose_texture_folder(self):
        if self.active_tab != 'models':return
        from tkinter import filedialog
        folder = filedialog.askdirectory(parent=self.window, title='Choose folder containing extracted DDS files')
        if not folder:return
        self.texture_folder = Path(folder)
        if self.view_mode == 'model' and self.model_preview.mesh is not None:
            self.apply_model_materials(self.path)

    def show_model_textures(self):
        """Show material-to-texture links across the unpacked PAK collection."""
        if self.active_tab != 'models' or self.view_mode != 'model' or not self.path:
            return
        try:
            report = model_material_links(self.workspace, self.path, **self._asset_roots())
            lines = ['Model: ' + self.path, 'Material: ' + report['material'],
                     'Material status: ' + report['status'],
                     'Cross-PAK links: ' + ('yes' if report['collection'] else 'no (current PAK only)'), '']
            for item in report['textures']:
                lines.append(f"{item['map']}: {item['reference']}")
                lines.append('  ' + item['status'] +
                             ('  |  ' + '; '.join(item['archives']) if item['archives'] else ''))
                if item['paths']:
                    lines.append('  Extracted file: ' + ', '.join(item['paths']))
            if report['status'] == 'ambiguous':
                lines.append('Multiple DIFFERENT versions of the material were found.')
                lines.append('Select the model from the PAK containing its matching .mtl,')
                lines.append('or move old/duplicate extracted projects out of the Projects folder.')
                lines.append('Conflicting material workspaces:')
                for candidate in report.get('candidates', []):
                    lines.append('  ' + candidate['archive'] + ' | ' + candidate['workspace'])
            elif report['status'] == 'missing':
                lines.append('Material file not found. Unpack the PAK containing its .mtl file.')
            elif not report['textures']:
                lines.append('This material contains no texture references.')
            lines += ['', 'CryEngine .tif references can correspond to cooked .dds/.dds.0 assets.',
                      'When diffuse DDS files are found, the 3D preview uses their UVs with approximate lighting.',
                      'To edit a texture, choose its PAK in the main list and open the Images tab.']
            view = tk.Toplevel(self.window)
            view.title('Model material and texture locations')
            view.geometry('900x560')
            panel = tk.Text(view, wrap='word', background='#18181f', foreground='#eeeeef',
                            font=('Consolas', 10), padx=12, pady=12)
            panel.pack(fill='both', expand=True)
            panel.insert('1.0', '\n'.join(lines))
            panel.configure(state='disabled')
            found=sum(t['status']=='found' for t in report['textures'])
            self.info.set(f'{self.path} | Material texture matches: {found}/{len(report["textures"])}')
        except Exception as error:
            messagebox.showerror('Could not find model textures',str(error),parent=self.window)

    def export_model(self):
        if self.view_mode != 'model' or not self.path:return
        from tkinter import filedialog
        dest = filedialog.asksaveasfilename(parent=self.window, title='Export native model',
                         initialfile=Path(self.path).name,
                         defaultextension=Path(self.path).suffix,
                         filetypes=[('Native model file', '*' + Path(self.path).suffix)])
        if not dest:return
        try:
            export_model(self.workspace, self.path, dest)
            self.info.set('Exported native model: ' + dest)
        except Exception as error:
            messagebox.showerror('Model export failed',str(error),parent=self.window)

    def import_model(self):
        if self.view_mode != 'model' or not self.path:return
        if self.manager.busy:
            messagebox.showerror('Wait for current task','A manager operation is still running.',parent=self.window);return
        from tkinter import filedialog
        ext=Path(self.path).suffix
        path=filedialog.askopenfilename(parent=self.window, title='Select edited native model',
                       filetypes=[('Native model', '*' + ext)])
        if not path:return
        if not messagebox.askyesno('Experimental model import',
                  'Only same-layout CryEngine model files are accepted. This cannot guarantee in-game compatibility.\n\nImport and keep an original backup?',
                  parent=self.window):return
        try:
            rel = self.path
            changed=replace_model(self.workspace, rel, path, digest(self.raw))
            self.show_model(rel,changed)
            self.info.set('Imported model (experimental): ' + rel + ' | Original in EditorBackups')
        except Exception as error:
            messagebox.showerror('Model import rejected',str(error),parent=self.window)

    def save(self):
        if self.path is None or self.view_mode != 'text':return True
        try:
            if self.manager.busy:raise RuntimeError('Wait for the current manager task before saving.')
            text=self.text.get('1.0','end-1c')
            self.raw=save_text(self.workspace,self.path,text,digest(self.raw),self.raw,self.records[self.path]['format']=='cryxml')
            self.loaded_text=text
            self.info.set('Saved: '+self.path+' | Previous contents kept in EditorBackups')
            return True
        except Exception as e:
            messagebox.showerror('File not saved',str(e),parent=self.window);return False

    def reload(self):
        if self.path and self.confirm():self.load(self.path)

    def find_text(self):
        if self.path is None or self.view_mode != 'text':return
        needle=simpledialog.askstring('Find text','Text to find (case-insensitive):',parent=self.window)
        if not needle:return
        start=self.text.index('insert+1c')
        match=self.text.search(needle,start,stopindex='end',nocase=True) or self.text.search(needle,'1.0',stopindex=start,nocase=True)
        self.text.tag_remove('found','1.0','end')
        if match:
            self.text.tag_configure('found',background='#8a2330',foreground='white')
            self.text.tag_add('found',match,f'{match}+{len(needle)}c');self.text.see(match);self.text.mark_set('insert',match)
        else:messagebox.showinfo('Find text','No match in this file.',parent=self.window)

    def explore(self):
        from pak_manager_gui import open_folder
        try:
            tree=self.trees[self.active_tab]
            selection=tree.selection()
            rel=self.tree_paths[self.active_tab].get(selection[0],'') if selection else ''
            root=(self.workspace/'files').resolve();path=(root/rel).resolve()
            if not path.is_relative_to(root):raise ValueError('Path is outside project.')
            open_folder(path if path.is_dir() else path.parent)
        except Exception as e:messagebox.showerror('Cannot open folder',str(e),parent=self.window)

    def close(self):
        if not self.confirm():return False
        self.window.destroy();return True
