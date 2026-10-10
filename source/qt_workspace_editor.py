"""Native Qt editor for unpacked Evolve PAK workspaces.

Conservative file writes reuse existing reviewed/hash-checked backend modules.
Specialized GPU CryEngine rendering remains accessible via a separate legacy
editor during migration; no model/texture control disappears from the UI.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QTreeWidget, QTreeWidgetItem, QLineEdit, QPushButton, QPlainTextEdit,
    QTabWidget, QSplitter, QFileDialog, QMessageBox, QMenu, QScrollArea,
)
from dds_texture import is_dds, is_split_dds, parse_dds, preview_dds, export_png, replace_dds
from dds_streaming import inspect_stream, inspect_whole_part0, replace_stream
from dds_png_import import encode_png_as_dds, compression_for_dds
from model_asset import is_model, inspect_model, export_model, replace_model
from model_preview import find_preview_mesh, render_mesh
from multi_pak_assets import model_material_links
from workspace_file_actions import (
    checked_path, sha_file, replace_raw_file, extracted_original_backup,
    restore_extracted_original,
)
from workspace_editor import save_text, validate_xml, digest, TEXT_SUFFIXES, MAX_TEXT
from loose_file_editor import save_loose_text
from loose_game_files import TEXT_EXTENSIONS, copy_to_project
from evolve_pak_workspace import normalized_name


def _image_pixmap(image):
    with BytesIO() as output:
        image.convert('RGBA').save(output, format='PNG')
        pixmap = QPixmap()
        if not pixmap.loadFromData(output.getvalue(), 'PNG'):
            raise ValueError('Qt could not decode the prepared texture preview.')
        return pixmap


class QtLooseFileEditor(QDialog):
    def __init__(self, parent, game_root, relative, projects):
        super().__init__(parent)
        self.relative = relative
        self.projects = Path(projects).resolve()
        self.copy = copy_to_project(game_root, relative, self.projects)
        self.setWindowTitle('Game File Copy — ' + self.copy.name)
        self.resize(850, 590)
        self.text = QPlainTextEdit()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(str(self.copy)))
        layout.addWidget(QLabel('Private project copy — the installed game file is never modified.'))
        top = QHBoxLayout()
        for name, fn in (('Show Project Copy', self.reveal),
                         ('Open in External App', self.external), ('Save Copy', self.save)):
            top.addWidget(self._button(name, fn))
        layout.addLayout(top)
        layout.addWidget(self.text, 1)
        original = self.copy.read_bytes() if self.copy.stat().st_size <= 5*1024*1024 else None
        if self.copy.suffix.casefold() in TEXT_EXTENSIONS and original is not None:
            try:
                decoded = original.decode('utf-8-sig')
                if '\x00' in decoded:
                    raise ValueError('Binary data')
            except (UnicodeDecodeError, ValueError):
                decoded = None
            if decoded is not None:
                self.text.setPlainText(decoded)
                self._hash = hashlib.sha256(original).hexdigest()
                return
        self.text.setReadOnly(True)
        self.text.setPlainText('Unsupported/binary file: open your PRIVATE project copy in an external editor.')

    @staticmethod
    def _button(text, fn):
        button = QPushButton(text)
        button.clicked.connect(fn)
        return button

    def save(self):
        if self.text.isReadOnly():
            return
        try:
            save_loose_text(self.copy, self.projects/'LooseGameFileBackups',
                            self.text.toPlainText(), self._hash)
            self._hash = hashlib.sha256(self.copy.read_bytes()).hexdigest()
        except Exception as exc:
            QMessageBox.critical(self, 'Could not save copy', str(exc))

    def reveal(self):
        self._open(Path(self.copy).parent)

    def external(self):
        self._open(self.copy)

    @staticmethod
    def _open(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class QtWorkspaceEditor(QDialog):
    TABS = ('files', 'images', 'models')

    def __init__(self, manager, workspace, archive_map=None, advanced=None):
        super().__init__(manager)
        self.manager = manager
        self.workspace = Path(workspace).resolve(strict=True)
        self.archive_map = {name: Path(p).resolve() for name, p in (archive_map or {}).items()}
        self.advanced = advanced
        self.records = self._read_records(self.workspace)
        self.current = None
        self.raw = b''
        self.stream = None
        self.loaded_text = ''
        self.mode = None
        self.mesh = None
        self.texture_folder = None
        self.setWindowTitle('Edit Files — Evolve PAK Workspace')
        self.resize(1210, 790)
        self.setMinimumSize(780, 530)
        self.setStyleSheet(manager.styleSheet())
        main = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search extracted files…')
        self.search.textChanged.connect(self.populate)
        tools.addWidget(self.search, 1)
        tools.addWidget(self._button('Open File Folder', self.explore))
        tools.addWidget(self._button('Reload', self.reload))
        tools.addWidget(self._button('Help', self.help))
        tools.addWidget(self._button('Advanced GPU / full editor', self.open_advanced))
        main.addLayout(tools)

        self.archive_select = None
        if len(self.archive_map) > 1:
            self.archive_select = QComboBox()
            self.archive_select.addItems(list(self.archive_map))
            current = next((name for name, p in self.archive_map.items()
                            if p == self.workspace), '')
            self.archive_select.setCurrentText(current)
            self.archive_select.currentTextChanged.connect(self.switch_archive)
            main.addWidget(self.archive_select)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.change_tab)
        self.trees, self.views, self.actions = {}, {}, {}
        for section, label in zip(self.TABS, ('Files / Text', 'Images / Textures', 'Models')):
            tab = QWidget()
            box = QVBoxLayout(tab)
            toolbar = QHBoxLayout()
            widgets = {}
            if section == 'files':
                for text, fn in (('Save', self.save), ('Find Text', self.find_text),
                                 ('Import / Replace File', self.import_raw), ('Restore Original', self.restore_original)):
                    widgets[text] = self._button(text, fn)
                    toolbar.addWidget(widgets[text])
            elif section == 'images':
                for text, fn in (('Export PNG', self.export_png), ('Import DDS', self.import_dds),
                                 ('Import PNG', self.import_png)):
                    widgets[text] = self._button(text, fn)
                    toolbar.addWidget(widgets[text])
            else:
                for text, fn in (('Export Native Model', self.export_model),
                                 ('Import Same-Layout Model', self.import_model),
                                 ('Find Model Textures', self.find_model_textures),
                                 ('Choose Texture Folder', self.choose_texture_folder)):
                    widgets[text] = self._button(text, fn)
                    toolbar.addWidget(widgets[text])
            toolbar.addStretch()
            box.addLayout(toolbar)
            split = QSplitter(Qt.Horizontal)
            tree = QTreeWidget()
            tree.setHeaderHidden(True)
            tree.itemSelectionChanged.connect(self.load_selection)
            tree.setContextMenuPolicy(Qt.CustomContextMenu)
            tree.customContextMenuRequested.connect(lambda position, s=section:
                                                     self.show_context_menu(s, position))
            split.addWidget(tree)
            if section == 'files':
                view = QPlainTextEdit()
                view.setReadOnly(True)
                view.setTabStopDistance(35)
            else:
                view = QLabel('Select a '+('DDS texture' if section == 'images' else 'CryEngine model')+'.')
                view.setAlignment(Qt.AlignCenter)
                view.setWordWrap(True)
            split.addWidget(view)
            split.setStretchFactor(0, 1)
            split.setStretchFactor(1, 4)
            box.addWidget(split, 1)
            self.tabs.addTab(tab, label)
            self.trees[section] = tree
            self.views[section] = view
            self.actions[section] = widgets
        main.addWidget(self.tabs, 1)
        self.status = QLabel('Files remain inside the extracted project; build and add each PAK separately.')
        self.status.setWordWrap(True)
        main.addWidget(self.status)
        self.save_shortcut = self._button('', self.save)
        self.save_shortcut.setShortcut(QKeySequence.Save)
        self.save_shortcut.setVisible(False)
        main.addWidget(self.save_shortcut)
        self.populate()

    @staticmethod
    def _button(name, callback):
        button = QPushButton(name)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _read_records(workspace):
        path = Path(workspace)/'.evolve-pak-workspace.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data.get('entries'), list):
            raise ValueError('Invalid workspace inventory.')
        entries = {}
        for entry in data['entries']:
            rel = normalized_name(entry['path'])
            if rel != entry['path'] or rel.casefold() in (p.casefold() for p in entries):
                raise ValueError('Unsafe or duplicated extracted path: '+rel)
            entries[rel] = entry
        return entries

    def _category(self, rel):
        if is_dds(rel) or is_split_dds(rel):
            return 'images'
        if is_model(rel):
            return 'models'
        return 'files'

    def populate(self):
        needle = self.search.text().casefold()
        for section, tree in self.trees.items():
            tree.blockSignals(True)
            tree.clear()
            folders = {}
            entries = [rel for rel in sorted(self.records, key=str.casefold)
                       if self._category(rel) == section and needle in rel.casefold()]
            if section == 'images':
                first = {}
                for rel in entries:
                    if is_split_dds(rel):
                        base, suffix = rel.rsplit('.', 1)
                        first[base.casefold()] = min(first.get(base.casefold(), 99),
                                                     int(suffix))
                entries = [rel for rel in entries if not is_split_dds(rel)
                           or int(rel.rsplit('.', 1)[1]) == first[rel.rsplit('.', 1)[0].casefold()]]
            for rel in entries:
                parent = None
                parts = rel.split('/')
                for i, part in enumerate(parts[:-1]):
                    key = '/'.join(parts[:i+1])
                    if key not in folders:
                        folder = QTreeWidgetItem([part])
                        if parent:
                            parent.addChild(folder)
                        else:
                            tree.addTopLevelItem(folder)
                        folders[key] = folder
                    parent = folders[key]
                item = QTreeWidgetItem([parts[-1]])
                item.setData(0, Qt.UserRole, rel)
                if parent:
                    parent.addChild(item)
                else:
                    tree.addTopLevelItem(item)
            tree.blockSignals(False)

    def selected(self):
        section = self.TABS[self.tabs.currentIndex()]
        items = self.trees[section].selectedItems()
        if not items:
            raise ValueError('Choose an extracted file first.')
        rel = items[0].data(0, Qt.UserRole)
        if not rel:
            raise ValueError('Choose a file, not a directory.')
        return rel

    def _dirty(self):
        return (self.mode == 'text' and self.current and
                self.views['files'].toPlainText() != self.loaded_text)

    def _confirm_buffer(self):
        return not self._dirty() or QMessageBox.question(
            self, 'Unsaved changes', 'Discard unsaved text edits?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def load_selection(self):
        if not self._confirm_buffer():
            return
        try:
            self.load(self.selected())
        except ValueError as exc:
            self.status.setText(str(exc))

    def load(self, rel):
        self.current = rel
        self.stream = None
        self.mode = self._category(rel)
        path = checked_path(self.workspace, rel)
        self.raw = path.read_bytes()
        if self.mode == 'files':
            control = self.views['files']
            control.setReadOnly(True)
            if path.stat().st_size > MAX_TEXT:
                self.loaded_text = ''
                control.setPlainText('Large binary file. Use Import/Export or external editor.')
            else:
                try:
                    plain = self.raw.decode('utf-8-sig')
                    if '\0' in plain or path.suffix.casefold() not in TEXT_SUFFIXES:
                        raise UnicodeError('Not editable text')
                except UnicodeError:
                    self.loaded_text = ''
                    control.setPlainText('Binary file. Use Import/Export or external editor.')
                else:
                    control.setPlainText(plain)
                    control.setReadOnly(False)
                    self.loaded_text = plain
            self.status.setText(rel + ' | project copy only')
        elif self.mode == 'images':
            if is_split_dds(rel):
                try:
                    self.stream = inspect_stream(self.workspace, rel)
                    self.raw = self.stream.merged
                except ValueError:
                    self.raw = inspect_whole_part0(self.workspace, rel)
            info = parse_dds(self.raw)
            image = preview_dds(self.raw)
            view = self.views['images']
            view.setPixmap(_image_pixmap(image).scaled(720, 480,
                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            view.setAlignment(Qt.AlignCenter)
            self.status.setText(rel + ' | ' + info.description +
                                ' | largest mip only' +
                                (' | streaming fragments' if self.stream else ''))
        else:
            view = self.views['models']
            try:
                info = inspect_model(self.raw)
                report = info.description
            except ValueError as exc:
                report = str(exc) + '\nNative export remains available.'
            try:
                mesh = find_preview_mesh(
                    self.workspace, rel, self.raw,
                    projects_root=self.manager.projects_path,
                    stage_root=self.manager.stage_path)
                self.mesh = mesh
                self._paint_model(view, mesh)
                self.status.setText(rel + ' | ' + report +
                                    ' | Qt software model preview (GPU preview in Advanced)')
            except (ValueError, OSError) as exc:
                view.setPixmap(QPixmap())
                view.setText(rel + '\n\n' + report +
                             '\n\n3D preview unavailable: ' + str(exc))
                self.status.setText(rel + ' | Native model inspection')

    def _paint_model(self, view, mesh):
        result = render_mesh(mesh, width=720, height=490)
        view.setPixmap(_image_pixmap(result))
        view.setAlignment(Qt.AlignCenter)

    def switch_archive(self, name):
        if name not in self.archive_map or self.archive_map[name] == self.workspace:
            return
        if self.manager.busy or not self._confirm_buffer():
            self.archive_select.blockSignals(True)
            old = next((k for k, v in self.archive_map.items() if v == self.workspace), '')
            self.archive_select.setCurrentText(old)
            self.archive_select.blockSignals(False)
            return
        target = self.archive_map[name]
        new_records = self._read_records(target)
        self.workspace, self.records = target, new_records
        self.current, self.loaded_text, self.raw = None, '', b''
        self.manager.current_workspace = str(target)
        self.manager.archive_label = name
        self.manager.output_pak = ''
        self.manager.persist()
        self.populate()
        self.status.setText('Browsing ' + name + ' | Build Mod applies only to this PAK.')

    def change_tab(self, index):
        if self._dirty():
            # QTabWidget emits after a change. Revert if user keeps edits.
            if not self._confirm_buffer():
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(0)
                self.tabs.blockSignals(False)
                return
        self.current, self.mode = None, None

    def save(self):
        if self.mode != 'files' or not self.current:
            return
        control = self.views['files']
        if control.isReadOnly():
            return
        if self.manager.busy:
            QMessageBox.warning(self,'Task running','Wait for current manager task.')
            return
        try:
            new = save_text(self.workspace, self.current, control.toPlainText(),
                            digest(self.raw), self.raw,
                            self.records[self.current].get('format') == 'cryxml')
            self.raw = new
            self.loaded_text = control.toPlainText()
            self.status.setText('Saved ' + self.current +
                                ' | previous contents in EditorBackups')
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, 'Could not save text', str(exc))

    def find_text(self):
        if self.mode != 'files':
            return
        text, ok = __import__('PySide6.QtWidgets',fromlist=['QInputDialog']).QInputDialog.getText(
            self, 'Find text','Search (case-insensitive):')
        if not ok or not text:
            return
        control = self.views['files']
        control.moveCursor(__import__('PySide6.QtGui',fromlist=['QTextCursor']).QTextCursor.Start)
        if not control.find(text):
            self.status.setText('No text match found for: '+text)

    def reload(self):
        if self.current and self._confirm_buffer():
            self.load(self.current)

    def explore(self):
        try:
            path = checked_path(self.workspace, self.current) if self.current else self.workspace/'files'
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if path.is_file() else path)))
        except Exception as exc:
            QMessageBox.critical(self, 'Could not open folder', str(exc))

    def _import_and_reload(self, action):
        try:
            if self.manager.busy:
                raise RuntimeError('Wait until current manager task finishes.')
            rel = self.selected()
            action(rel)
            self.load(rel)
        except Exception as exc:
            QMessageBox.critical(self, 'File edit rejected', str(exc))

    def import_raw(self):
        def replace(rel):
            target, _ = QFileDialog.getOpenFileName(self,'Replace extracted file')
            if not target:
                return
            if is_split_dds(rel):
                raise ValueError('Use Import DDS / Import PNG on the Images tab to replace ALL streaming DDS fragments together.')
            source = checked_path(self.workspace, rel)
            expected = sha_file(source)
            # Respect CryXML structural validation and UTF-8 text constraints.
            if source.suffix.casefold() in TEXT_SUFFIXES and source.stat().st_size <= MAX_TEXT:
                raw = source.read_bytes()
                imported = Path(target).read_bytes()
                if len(imported) > MAX_TEXT:
                    raise ValueError('Imported text exceeds the 5 MiB limit.')
                try:
                    text = imported.decode('utf-8-sig')
                except UnicodeError as error:
                    raise ValueError('Imported bytes are not UTF-8 text.') from error
                if '\0' in text:
                    raise ValueError('Imported text contains a NUL byte.')
                save_text(self.workspace, rel, text, expected, raw,
                          self.records[rel].get('format') == 'cryxml')
            else:
                if QMessageBox.question(
                    self, 'Replace binary extracted file?',
                    'Keep a previous-byte backup before importing an unknown binary file?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
                replace_raw_file(self.workspace, rel, target, expected)
        self._import_and_reload(replace)

    def restore_original(self):
        def restore(rel):
            entry = self.records[rel]
            original_sha = entry.get('sha256') or entry.get('hash')
            if is_split_dds(rel):
                raise ValueError('Split DDS fragment restore requires restoring the entire stream. Use Advanced GPU / full editor for safe stream operations.')
            if not original_sha:
                raise ValueError('The extraction manifest has no trusted original file checksum.')
            if QMessageBox.question(self, 'Restore extracted original?',
                 'Save current modified bytes in EditorBackups and restore the verified original?',
                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            restore_extracted_original(
                self.workspace, rel, original_sha,
                sha_file(checked_path(self.workspace, rel)))
        self._import_and_reload(restore)

    def export_raw(self):
        try:
            rel = self.selected()
            source = checked_path(self.workspace, rel)
            dest, _ = QFileDialog.getSaveFileName(self,'Export raw file',source.name)
            if dest:
                shutil.copyfile(source, dest)
        except Exception as exc:
            QMessageBox.critical(self, 'Export failed', str(exc))

    def show_context_menu(self, section, position):
        tree = self.trees[section]
        item = tree.itemAt(position)
        if item is None:
            return
        tree.setCurrentItem(item)
        if not item.data(0, Qt.UserRole):
            return
        menu = QMenu(tree)
        menu.addAction('Preview / Open', lambda: self.load(self.selected()))
        menu.addAction('Import Over / Replace', self.import_raw)
        menu.addAction('Restore Original Extraction', self.restore_original)
        menu.addAction('Export Raw File', self.export_raw)
        menu.addAction('Open Original File Location', self.explore)
        menu.addAction('Copy Relative Path', lambda: QApplication.clipboard().setText(self.selected()))
        menu.exec(tree.mapToGlobal(position))

    def export_png(self):
        if self.mode != 'images' or not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(self,'Export as PNG',
                            re.sub(r'(?i)\.dds(?:\.\d+)?$', '', Path(self.current).name)+'.png',
                            'PNG (*.png)')
        if path:
            try:
                export_png(self.raw, Path(path))
                self.status.setText('Exported PNG: '+path)
            except Exception as exc:
                QMessageBox.critical(self,'Could not export PNG',str(exc))

    def import_dds(self):
        if self.mode != 'images':
            return
        selected, _ = QFileDialog.getOpenFileName(self,'Choose replacement DDS','','DDS (*.dds)')
        if selected:
            self._replace_texture(selected)

    def import_png(self):
        if self.mode != 'images':
            return
        selected, _ = QFileDialog.getOpenFileName(self,'Choose replacement PNG','','PNG (*.png)')
        if not selected:
            return
        try:
            data = encode_png_as_dds(self.raw, selected)
            with tempfile.TemporaryDirectory(prefix='evolve-qt-png-') as folder:
                path = Path(folder)/'replacement.dds'
                path.write_bytes(data)
                self._replace_texture(path)
        except Exception as exc:
            QMessageBox.critical(self,'PNG import rejected',str(exc))

    def _replace_texture(self, path):
        try:
            rel = self.current
            if self.manager.busy:
                raise RuntimeError('Wait for current manager task.')
            if self.stream:
                replace_stream(self.workspace, rel, path, self.stream.hashes)
            else:
                replace_dds(self.workspace, rel, path,
                            sha_file(checked_path(self.workspace, rel)))
            self.load(rel)
            self.status.setText('Imported compatible DDS, backups retained: '+rel)
        except Exception as exc:
            QMessageBox.critical(self,'DDS import rejected',str(exc))

    def export_model(self):
        if self.mode != 'models' or not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(self,'Export native CryEngine model',
                                            Path(self.current).name)
        if path:
            try:
                export_model(self.workspace,self.current,path)
            except Exception as exc:
                QMessageBox.critical(self,'Model export failed',str(exc))

    def import_model(self):
        if self.mode != 'models' or not self.current:
            return
        target, _ = QFileDialog.getOpenFileName(self,'Import same-layout native model',
                                                '', 'Native models (*'+Path(self.current).suffix+')')
        if not target:
            return
        if QMessageBox.question(self,'Experimental model import',
             'Only verified same-layout model binaries are accepted. Keep backup and import?',
             QMessageBox.Yes|QMessageBox.No,QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            replace_model(self.workspace,self.current,target,digest(self.raw))
            self.load(self.current)
        except Exception as exc:
            QMessageBox.critical(self,'Model import rejected',str(exc))

    def choose_texture_folder(self):
        folder = QFileDialog.getExistingDirectory(self,'Choose an extracted texture folder')
        if folder:
            self.texture_folder = Path(folder)
            self.status.setText('Model preview textures may be found under: '+folder)

    def find_model_textures(self):
        if self.mode != 'models':
            return
        try:
            result = model_material_links(
                self.workspace, self.current,
                projects_root=self.manager.projects_path,
                stage_root=self.manager.stage_path)
            lines = [
                'Model: '+self.current,
                'Material: '+str(result.get('material')),
                'Status: '+str(result.get('status')),
                'Cross-PAK links: '+str(result.get('collection')),
            ]
            for record in result.get('textures', []):
                lines.append('\n'+str(record.get('map'))+': '+str(record.get('reference')))
                lines.append('  '+str(record.get('status')))
                lines.append('  Paths: '+', '.join(record.get('paths', [])))
            QMessageBox.information(self,'Model textures','\n'.join(lines))
        except Exception as exc:
            QMessageBox.critical(self,'Model textures',str(exc))

    def open_advanced(self):
        if self.advanced:
            self.advanced()

    def help(self):
        QMessageBox.information(
            self,'Editor help',
            'Files / Text: select manifest-listed files, edit, Save with checksum validation, '
            'and use the right-click import/restore actions.\n\n'
            'Images / Textures: preview DDS, export PNG and import compatible DDS/PNG. '
            'Streaming .dds.0/.1 files are reassembled and replaced together.\n\n'
            'Models: inspect CryEngine structure, export native files, import only '
            'same-layout replacements, find linked textures and preview supported meshes.\n\n'
            'Advanced GPU / full editor opens the proven existing model/texture editor '
            'in a separate process during Qt migration.')

    def closeEvent(self, event):
        if not self._confirm_buffer():
            event.ignore()
            return
        super().closeEvent(event)
