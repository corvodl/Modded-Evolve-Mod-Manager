"""Integrated browser/editor for existing exported PAK entries."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from evolve_gameplay_editor import parse_cryxml_text
from dds_texture import is_dds, is_split_dds, parse_dds, preview_dds, export_png, replace_dds

MAX_TEXT = 5 * 1024 * 1024
TEXT_SUFFIXES = {'.xml', '.txt', '.cfg', '.ini', '.lua', '.json', '.csv'}

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
    def __init__(self, manager, workspace):
        self.manager = manager
        self.workspace = Path(workspace)
        self.records = {r['path']: r for r in json.loads((self.workspace/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))['entries']}
        self.path = None
        self.raw = b''
        self.loaded_text = ''
        self.view_mode = None
        self.preview_photo = None
        self.window = tk.Toplevel(manager.window)
        self.window.title(manager.t('editor_title'))
        self.window.geometry('1120x730'); self.window.minsize(800, 500)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.query = tk.StringVar()
        bar = ttk.Frame(self.window, padding=8); bar.pack(fill='x')
        ttk.Label(bar, text=manager.t('editor_search')).pack(side='left')
        ttk.Entry(bar, textvariable=self.query, width=40).pack(side='left', padx=8)
        ttk.Button(bar, text=manager.t('editor_open_folder'), command=self.explore).pack(side='left')
        ttk.Button(bar, text=manager.t('editor_save'), command=self.save).pack(side='right')
        self.import_button = ttk.Button(bar, text=manager.t('texture_import'), command=self.import_texture)
        self.import_button.pack(side='right', padx=(0, 8))
        self.export_button = ttk.Button(bar, text=manager.t('texture_export'), command=self.export_texture)
        self.export_button.pack(side='right', padx=(0, 8))
        self.import_button.configure(state='disabled')
        self.export_button.configure(state='disabled')
        ttk.Button(bar, text=manager.t('editor_reload'), command=self.reload).pack(side='right', padx=8)
        pane = ttk.Panedwindow(self.window, orient='horizontal'); pane.pack(fill='both', expand=True, padx=8)
        left=ttk.Frame(pane); right=ttk.Frame(pane); pane.add(left, weight=1); pane.add(right, weight=4)
        self.tree=ttk.Treeview(left, show='tree', selectmode='browse')
        self.tree.pack(side='left', fill='both', expand=True)
        sc=ttk.Scrollbar(left, command=self.tree.yview); sc.pack(side='right', fill='y'); self.tree.configure(yscrollcommand=sc.set)
        self.text=tk.Text(right, wrap='none', undo=True, font=('Consolas',11), background='#18181f', foreground='#eeeeef', insertbackground='white')
        self.text.grid(row=0,column=0,sticky='nsew'); right.rowconfigure(0,weight=1);right.columnconfigure(0,weight=1)
        self.image_label = tk.Label(right, background='#18181f', foreground='#eeeeef', compound='top')
        ys=ttk.Scrollbar(right,command=self.text.yview); ys.grid(row=0,column=1,sticky='ns')
        xs=ttk.Scrollbar(right,orient='horizontal',command=self.text.xview);xs.grid(row=1,column=0,sticky='ew')
        self.text.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        self.info=tk.StringVar(value=manager.t('editor_hint'))
        ttk.Label(self.window,textvariable=self.info,wraplength=1050,padding=8).pack(fill='x')
        ttk.Label(self.window,text=manager.t('editor_footer'),padding=(8,0,8,8)).pack(fill='x')
        self.query.trace_add('write',lambda *_:self.populate())
        self.tree.bind('<<TreeviewSelect>>',self.select)
        self.window.bind('<Control-s>',lambda _:self.save())
        self.window.bind('<Control-f>',lambda _:self.find_text())
        self.text.configure(state='disabled')
        self.populate()

    def dirty(self):
        return self.path is not None and self.view_mode == 'text' and self.text.get('1.0','end-1c') != self.loaded_text

    def confirm(self):
        if not self.dirty(): return True
        answer=messagebox.askyesnocancel('Unsaved changes','Save your edits before continuing?',parent=self.window)
        if answer is None:return False
        return self.save() if answer else True

    def populate(self):
        self.tree.delete(*self.tree.get_children())
        self.node_paths={}
        dirs={'' : ''}; self.nodes={}
        for rel in sorted(self.records,key=str.casefold):
            if self.query.get().casefold() not in rel.casefold():continue
            parts=rel.split('/'); parent=''
            for i,part in enumerate(parts[:-1]):
                folder='/'.join(parts[:i+1])
                if folder not in dirs:
                    dirs[folder]=self.tree.insert(parent,'end',text=part,open=bool(self.query.get()))
                    self.node_paths[dirs[folder]]=folder
                parent=dirs[folder]
            node=self.tree.insert(parent,'end',text=parts[-1]);self.nodes[node]=rel;self.node_paths[node]=rel

    def select(self, _=None):
        chosen=self.tree.selection()
        rel=self.nodes.get(chosen[0]) if chosen else None
        if rel and rel!=self.path and self.confirm():self.load(rel)

    def load(self,rel):
        try:
            root=(self.workspace/'files').resolve(); file=(root/rel).resolve()
            if not file.is_relative_to(root):raise ValueError('File resolves outside project.')
            if is_split_dds(rel):
                raise ValueError('Split DDS streaming pieces (.dds.0, .dds.1, ...) cannot be previewed or replaced independently.')
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
            self.image_label.grid_remove()
            self.text.grid()
            self.preview_photo = None
            self.import_button.configure(state='disabled')
            self.export_button.configure(state='disabled')
            self.path=rel;self.raw=raw;self.loaded_text=text;self.view_mode='text'
            self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',text);self.text.edit_reset()
            self.info.set(rel+' | UTF-8 | '+('CryXmlB: edit existing values only' if self.records[rel]['format']=='cryxml' else 'Text file'))
        except Exception as e:messagebox.showerror('Cannot open as text',str(e),parent=self.window)

    def show_texture(self, rel, raw):
        from PIL import ImageTk
        info = parse_dds(raw)
        image = preview_dds(raw)
        photo = ImageTk.PhotoImage(image, master=self.window)
        self.path = rel
        self.raw = raw
        self.loaded_text = ''
        self.view_mode = 'dds'
        self.text.grid_remove()
        self.image_label.configure(image=photo, text=info.description + '\nPreview shows the largest mip level only')
        self.preview_photo = photo  # Tk images must remain referenced.
        self.image_label.grid(row=0, column=0, sticky='nsew')
        self.import_button.configure(state='normal')
        self.export_button.configure(state='normal')
        self.info.set('DDS texture: ' + rel + ' | ' + info.description)

    def export_texture(self):
        if self.view_mode != 'dds' or not self.path:return
        from tkinter import filedialog
        dest = filedialog.asksaveasfilename(parent=self.window, title='Export DDS as PNG',
                    initialfile=Path(self.path).stem + '.png', defaultextension='.png',
                    filetypes=[('PNG image', '*.png')])
        if not dest:return
        try:
            export_png(self.raw, Path(dest))
            self.info.set('Exported PNG (top mip only): ' + dest)
        except Exception as e:messagebox.showerror('PNG export failed',str(e),parent=self.window)

    def import_texture(self):
        if self.view_mode != 'dds' or not self.path:return
        if self.manager.busy:
            messagebox.showerror('Wait for current task','A manager operation is still running.',parent=self.window);return
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.window, title='Select replacement DDS',
                     filetypes=[('DDS textures', '*.dds')])
        if not path:return
        try:
            result = replace_dds(self.workspace, self.path, path, digest(self.raw))
            rel = self.path
            self.show_texture(rel, result)
            self.info.set('Imported compatible DDS: ' + rel + ' | Original kept in EditorBackups')
        except Exception as e:messagebox.showerror('DDS import rejected',str(e),parent=self.window)

    def save(self):
        if self.path is None or self.view_mode == 'dds':return True
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
        if self.path is None:return
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
            selection=self.tree.selection()
            rel=self.node_paths.get(selection[0],'') if selection else ''
            root=(self.workspace/'files').resolve();path=(root/rel).resolve()
            if not path.is_relative_to(root):raise ValueError('Path is outside project.')
            open_folder(path if path.is_dir() else path.parent)
        except Exception as e:messagebox.showerror('Cannot open folder',str(e),parent=self.window)

    def close(self):
        if not self.confirm():return False
        self.window.destroy();return True
