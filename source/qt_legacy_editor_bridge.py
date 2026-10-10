"""Tk compatibility editor isolated in a separate process during Qt migration.

The editor retains 100% of the existing text, DDS, split DDS, model preview,
asset-linked workspace, replace/restore, and context menu implementation.
Never make Qt and Tk root windows share an event loop.
"""
from __future__ import annotations

import argparse
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from ui_copy import load_text
from dark_theme import apply_theme
from workspace_editor import WorkspaceEditor
from loose_file_editor import LooseFileEditor
from multi_pak_assets import load_collection
from app_runtime import save_settings, settings_source


class BridgeManager:
    busy = False

    def __init__(self, root, args):
        self.window = root
        self.args = args
        self.copy, _ = load_text()
        self.stage = tk.StringVar(master=root, value=args.stage or '')
        self.projects = tk.StringVar(master=root, value=args.projects or '')
        self.current_workspace = tk.StringVar(master=root, value=args.workspace or '')
        self.output_pak = tk.StringVar(master=root, value='')
        self.archive_label = tk.StringVar(master=root, value='')
        self.batch_root = tk.StringVar(master=root, value=args.batch_root or '')

    def t(self, key):
        return self.copy.get(key, key)

    def persist(self):
        source = settings_source()
        try:
            values = json.loads(source.read_text(encoding='utf-8')) if source.is_file() else {}
        except (ValueError, OSError):
            values = {}
        values.update({
            'stage': self.stage.get(),
            'projects': self.projects.get(),
            'current_workspace': self.current_workspace.get(),
            'output_pak': self.output_pak.get(),
            'archive_label': self.archive_label.get(),
            'batch_root': self.batch_root.get(),
        })
        save_settings(values)

    def write(self, line):
        print(str(line).rstrip(), flush=True)


def run_bridge(args):
    app = tk.Tk()
    app.withdraw()
    apply_theme(app)
    manager = BridgeManager(app, args)
    if args.loose_file:
        if not args.game or not args.projects:
            raise ValueError('Loose file edit requires a game folder and a projects folder.')
        editor = LooseFileEditor(app, args.game, args.loose_file, args.projects)
    else:
        if not args.workspace:
            raise ValueError('Choose an extracted workspace first.')
        ws = Path(args.workspace).resolve()
        if not (ws/'.evolve-pak-workspace.json').is_file():
            raise FileNotFoundError('No valid workspace manifest at: ' + str(ws))
        mapping = {}
        if args.batch_root:
            result = load_collection(Path(args.batch_root))
            if Path(result['stage']).resolve() == Path(args.stage).resolve():
                mapping = {
                    item['archive']: Path(args.batch_root)/item['workspace']
                    for item in result['archives']
                }
        editor = WorkspaceEditor(manager, ws, archive_map=mapping)
    # Root stays withdrawn; closing the only editor quits the Tk event loop.
    def monitor():
        if not editor.window.winfo_exists():
            app.quit()
        else:
            app.after(250, monitor)
    app.after(250, monitor)
    app.mainloop()
    app.destroy()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace')
    p.add_argument('--loose-file')
    p.add_argument('--game')
    p.add_argument('--batch-root')
    p.add_argument('--stage')
    p.add_argument('--projects')
    args = p.parse_args()
    run_bridge(args)


if __name__ == '__main__':
    main()
