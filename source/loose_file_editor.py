"""Small, isolated editor for loose game-file copies (never live game files)."""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk
import uuid

from loose_game_files import TEXT_EXTENSIONS, copy_to_project

MAX_TEXT = 5 * 1024 * 1024


def save_loose_text(target: Path, backup_root: Path, new_text: str, expected_sha: str) -> None:
    current = target.read_bytes()
    if hashlib.sha256(current).hexdigest() != expected_sha:
        raise ValueError('The file changed outside the editor. Reload before saving.')
    new = new_text.encode('utf-8')
    if len(new) > MAX_TEXT:
        raise ValueError('Text exceeds 5 MiB editor limit')
    if new == current: return
    backup = backup_root / (datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '-' + uuid.uuid4().hex) / target.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(target, backup)
    temp = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as f:
            f.write(new); f.flush(); os.fsync(f.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


class LooseFileEditor:
    def __init__(self, parent, game_root, relative, projects):
        self.relative = relative
        self.projects = Path(projects).resolve()
        self.copy = copy_to_project(game_root, relative, self.projects)
        self.window = tk.Toplevel(parent)
        self.window.title('Game File Copy — ' + self.copy.name)
        self.window.geometry('850x580')
        self.window.minsize(610, 380)
        pane = ttk.Frame(self.window, padding=15)
        pane.pack(fill='both', expand=True)
        ttk.Label(pane, text=self.copy.name, style='Section.TLabel').pack(anchor='w')
        ttk.Label(pane, text=f'{relative}  •  Project copy only; original game unchanged',
                  style='Muted.TLabel', wraplength=800).pack(anchor='w', pady=(3, 12))
        toolbar = ttk.Frame(pane)
        toolbar.pack(fill='x', pady=(0, 8))
        ttk.Button(toolbar, text='Show Project Copy', command=self.reveal).pack(side='left')
        ttk.Button(toolbar, text='Open in External App', command=self.external).pack(side='left', padx=7)
        self.save_button = ttk.Button(toolbar, text='Save Copy', style='Accent.TButton', command=self.save)
        self.save_button.pack(side='right')
        self.text = None
        raw = self.copy.read_bytes() if self.copy.stat().st_size <= MAX_TEXT else None
        if self.copy.suffix.casefold() in TEXT_EXTENSIONS and raw is not None:
            try: decoded = raw.decode('utf-8-sig')
            except UnicodeDecodeError: decoded = None
            if decoded is not None and '\0' not in decoded:
                self.text = tk.Text(pane, wrap='none', undo=True)
                self.text.insert('1.0', decoded)
                self.text.pack(fill='both', expand=True)
                self.sha = hashlib.sha256(raw).hexdigest()
        if self.text is None:
            self.save_button.configure(state='disabled')
            ttk.Label(pane, text='This file can be edited with an external application. '
                      'Open the copy, not the original installed game file.',
                      style='Muted.TLabel', wraplength=700).pack(anchor='w')

    def reveal(self):
        if os.name == 'nt': subprocess.Popen(['explorer.exe', '/select,', str(self.copy)])
        else: subprocess.Popen(['xdg-open', str(self.copy.parent)])

    def external(self):
        if os.name == 'nt': os.startfile(str(self.copy))
        else: subprocess.Popen(['xdg-open', str(self.copy)])

    def save(self):
        if self.text is None: return
        try:
            new = self.text.get('1.0', 'end-1c')
            save_loose_text(self.copy, self.projects / 'LooseGameFileBackups', new, self.sha)
            self.sha = hashlib.sha256(self.copy.read_bytes()).hexdigest()
        except Exception as error:
            messagebox.showerror('Could not save copy', str(error), parent=self.window)
