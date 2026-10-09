"""Integrated browser/editor for existing exported PAK entries."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import re
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from evolve_gameplay_editor import parse_cryxml_text
from dds_texture import is_dds, is_split_dds, parse_dds, preview_dds, export_png, replace_dds
from dds_streaming import inspect_stream, replace_stream
from model_asset import is_model, inspect_model, export_model, replace_model
from dds_png_import import encode_png_as_dds, compression_for_dds

MAX_TEXT = 5 * 1024 * 1024
TEXT_SUFFIXES = {'.xml', '.txt', '.cfg', '.ini', '.lua', '.json', '.csv', '.mtl', '.chrparams'}

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
            candidates = (r for r in candidates if not is_split_dds(r) or r.casefold().endswith('.dds.0'))
        return sorted((r for r in candidates if query.casefold() in r.casefold()), key=str.casefold)

    def __init__(self, manager, workspace):
        self.manager = manager
        self.workspace = Path(workspace)
        self.records = {r['path']: r for r in json.loads((self.workspace/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))['entries']}
        self.path = None
        self.raw = b''
        self.loaded_text = ''
        self.view_mode = None
        self.preview_photo = None
        self.streaming = None
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

    def reset_views(self):
        self.preview_photo = None
        self.streaming = None
        self.image_label.configure(image='', text='Select a DDS texture to preview.')
        self.model_label.configure(text='Select a CryTek model to inspect.')
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
        except Exception as e:messagebox.showerror('Cannot open as text',str(e),parent=self.window)

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
        self.view_mode = 'dds_stream' if stream is not None else 'dds'
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
        if self.view_mode not in ('dds', 'dds_stream') or not self.path:return
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
        if self.view_mode not in ('dds', 'dds_stream') or not self.path:return
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
        if self.active_tab != 'images' or self.view_mode not in ('dds', 'dds_stream') or not self.path:
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

    def show_model(self, rel, raw):
        self.reset_views()
        self.path = rel
        self.raw = raw
        self.loaded_text = ''
        self.view_mode = 'model'
        self.model_export_button.configure(state='normal')
        try:
            info = inspect_model(raw)
            report = info.description + '\nChunked CryTek model recognized.\nImport requires identical chunk table and binary length.'
            self.model_import_button.configure(state='normal')
        except ValueError as error:
            report = 'Experimental model support\n' + str(error) + '\nExport the native file to inspect it with an external application.'
        self.model_label.configure(text=rel + '\n\n' + report + '\n\nThere is no built-in 3D mesh preview or Blender converter.')
        self.info.set('Model: ' + rel + ' | Native export available; replacement is experimental')

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
                  'Only same-layout CryTek model files are accepted. This cannot guarantee in-game compatibility.\n\nImport and keep an original backup?',
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
