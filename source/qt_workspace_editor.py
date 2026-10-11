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
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QPixmap, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QTreeWidget, QTreeWidgetItem, QLineEdit, QPushButton, QPlainTextEdit,
    QTabWidget, QSplitter, QStackedWidget, QFileDialog, QMessageBox, QMenu, QScrollArea,
)
from dds_texture import is_dds, is_split_dds, parse_dds, preview_dds, export_png, replace_dds
from dds_streaming import inspect_stream, inspect_whole_part0, replace_stream
from dds_png_import import encode_png_as_dds, compression_for_dds
from model_asset import is_model, inspect_model, export_model, replace_model
from model_preview import find_preview_mesh, render_mesh
from qt_cpu_model_viewer import QtCPUModelCanvas
from model_blender_export import glb_bytes, convert_folder
from model_textured_export import (
    textured_model_from_workspace, export_extracted_folder, weight_stream_status)
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


class QtLooseFileEditor(QMainWindow):
    def __init__(self, parent, game_root, relative, projects):
        super().__init__(parent)
        self.relative = relative
        self.projects = Path(projects).resolve()
        self.copy = copy_to_project(game_root, relative, self.projects)
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle('Game File Copy — ' + self.copy.name)
        self.resize(850, 590)
        self.setMinimumSize(520, 340)
        center = QWidget(self)
        self.setCentralWidget(center)
        self.text = QPlainTextEdit()
        layout = QVBoxLayout(center)
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


class QtWorkspaceEditor(QMainWindow):
    TABS = ('files', 'images', 'models')

    def __init__(self, manager, workspace, archive_map=None):
        super().__init__(manager)
        self.manager = manager
        self.workspace = Path(workspace).resolve(strict=True)
        self.archive_map = {name: Path(p).resolve() for name, p in (archive_map or {}).items()}
        self.records = self._read_records(self.workspace)
        self.current = None
        self.raw = b''
        self.stream = None
        self.loaded_text = ''
        self.mode = None
        self.mesh = None
        self.model_gpu = None
        self.model_panel = None
        self.model_fallback = None
        self.model_cpu = None
        self.texture_folder = None
        self.animation_folder = None
        self.loose_animation_index = None
        self.preview_index = None
        self.character_preview = None
        self.character_skin_choice = None
        self.gpu_windows = []
        self.materials = None
        self.texture_message = 'No model selected'
        # A main window has the standard Win32 maximize/restore non-dialog frame.
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle('Edit Files — Evolve PAK Workspace')
        self.resize(1210, 790)
        self.setMinimumSize(660, 460)
        self.setStyleSheet(manager.styleSheet())
        center = QWidget(self)
        self.setCentralWidget(center)
        main = QVBoxLayout(center)
        self._build_editor_menus()
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search extracted files…')
        self.search.textChanged.connect(self.populate)
        tools.addWidget(self.search, 1)
        tools.addWidget(self._button('Open File Folder', self.explore))
        tools.addWidget(self._button('Reload', self.reload))
        tools.addWidget(self._button('Help', self.help))
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
            widgets = {}
            # Model functions live in the Blender-style menu bar, not a grid
            # of large buttons above the canvas.
            if section in ('files', 'images'):
                toolbar = QHBoxLayout()
                actions = (
                    (('Save', self.save), ('Find Text', self.find_text),
                     ('Import / Replace File', self.import_raw), ('Restore Original', self.restore_original))
                    if section == 'files' else
                    (('Export PNG', self.export_png), ('Import DDS', self.import_dds),
                     ('Import PNG', self.import_png))
                )
                for text, fn in actions:
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
            elif section == 'images':
                view = QLabel('Select a DDS texture.')
                view.setAlignment(Qt.AlignCenter)
                view.setWordWrap(True)
            else:
                view = QStackedWidget()
                self.model_fallback = QLabel('Select a CryEngine model. GPU preview is the default on Windows.')
                self.model_fallback.setAlignment(Qt.AlignCenter)
                self.model_fallback.setWordWrap(True)
                view.addWidget(self.model_fallback)
                self.model_cpu = QtCPUModelCanvas(view)
                self.model_cpu.setObjectName('cpuModelPreview')
                view.addWidget(self.model_cpu)
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
        self.save_shortcut = QShortcut(QKeySequence.Save, self)
        self.save_shortcut.activated.connect(self.save)
        self.find_shortcut = QShortcut(QKeySequence.Find, self)
        self.find_shortcut.activated.connect(self.find_text)
        self.populate()

    def _build_editor_menus(self):
        """Blender-style discoverable menu bar; no oversized model-action rows."""
        bar = self.menuBar()
        bar.setObjectName('editorMenuBar')
        menus = {name: bar.addMenu(name) for name in
                 ('File', 'Edit', 'View', 'Model', 'Textures', 'Animation', 'Help')}
        self.editor_menus = menus

        def action(menu, name, callback, shortcut=None):
            act = menus[menu].addAction(name)
            act.triggered.connect(lambda checked=False, fn=callback: fn())
            if shortcut:
                act.setShortcut(shortcut)
            return act

        action('File', 'Open File Folder', self.explore)
        action('File', 'Export Native Model…', self.export_model)
        action('File', 'Export for Blender (.glb)…', self.export_blender_model)
        action('File', 'Export Model Folder…', self.export_blender_folder)
        action('Edit', 'Save Text', self.save)
        action('Edit', 'Find Text', self.find_text)
        action('Edit', 'Import / Replace File…', self.import_raw)
        action('Edit', 'Restore Original', self.restore_original)
        self.equipment_menu = menus['Model'].addMenu('Equipped Skin')
        self.equipment_menu.setEnabled(False)
        action('Model', 'Import Same-Layout Model…', self.import_model)
        action('Model', 'Open GPU Viewer in New Window', self.open_gpu_viewer)
        action('Model', 'Reset Camera', lambda: self._with_model_panel(
            lambda p: p.reset_view()))
        action('Textures', 'Diagnose Texture Links…', self.find_model_textures)
        action('Textures', 'Texture Preview Status…', self.texture_diagnostics)
        action('Textures', 'Choose Extracted Texture Folder…', self.choose_texture_folder)
        action('Textures', 'Use Automatic Cross-PAK Lookup', self.use_auto_textures)
        action('Textures', 'Reload Model Textures', self.refresh_model_textures)
        action('Animation', 'Find Extracted Animations…', self.choose_animation_folder)
        action('Animation', 'Load Animated GLB…', lambda: self._with_model_panel(
            lambda p: p.load_animation()))
        action('Animation', 'Import Original CAF/DBA…', lambda: self._with_model_panel(
            lambda p: p.import_original()))
        action('Animation', 'Play / Pause', lambda: self._with_model_panel(
            lambda p: p.toggle_play()))
        action('Help', 'Editor Help', self.help)
        self._view_actions = {}
        for name, choice in (
            ('Show Textures', 'texture'), ('Wireframe', 'wireframe'),
            ('Faceted Shading', 'faceted'), ('Smooth Shading', 'smooth'),
            ('High Quality', 'high')):
            a = action('View', name,
                       lambda c=choice: self._change_model_view(c))
            a.setCheckable(True)
            self._view_actions[choice] = a
        self._view_actions['texture'].setChecked(True)
        self._view_actions['faceted'].setChecked(True)
        menus['View'].aboutToShow.connect(self._sync_view_menu)

    def _with_model_panel(self, callback):
        if self.model_panel is None or self.mode != 'models':
            QMessageBox.information(
                self, 'Model viewer', 'Select a supported model in the Models tab first.')
            return
        callback(self.model_panel)

    def _change_model_view(self, choice):
        def apply(panel):
            if choice == 'texture':
                panel.texture_checkbox.setChecked(
                    not panel.texture_checkbox.isChecked())
            elif choice == 'wireframe':
                panel.wire_checkbox.setChecked(
                    not panel.wire_checkbox.isChecked())
            elif choice in ('faceted', 'smooth'):
                panel.shading_combo.setCurrentIndex(0 if choice == 'faceted' else 1)
            elif choice == 'high':
                panel.quality_combo.setCurrentIndex(
                    0 if panel.quality_combo.currentIndex() else 1)
            self._sync_view_menu()
        self._with_model_panel(apply)

    def _sync_view_menu(self):
        p = self.model_panel
        values = {
            'texture': p.texture_checkbox.isChecked() if p else True,
            'wireframe': p.wire_checkbox.isChecked() if p else False,
            'faceted': p.shading_combo.currentIndex() == 0 if p else True,
            'smooth': p.shading_combo.currentIndex() == 1 if p else False,
            'high': p.quality_combo.currentIndex() == 1 if p else False,
        }
        for key, value in values.items():
            self._view_actions[key].setChecked(value)

    def texture_diagnostics(self):
        if self.mode != 'models' or self.mesh is None:
            QMessageBox.information(self, 'Texture preview', 'Select a model first.')
            return
        folder = str(self.texture_folder) if self.texture_folder else 'Automatic cross-PAK lookup'
        loaded = (self.materials.count if self.materials is not None else 0)
        slots = len(self.materials.textures) if self.materials is not None else 0
        problems = '\n'.join(self.materials.issues[:12]) if self.materials else ''
        QMessageBox.information(
            self, 'Texture preview diagnostics',
            'Model: ' + str(self.mesh.source_name or self.current) +
            '\nLookup: ' + folder +
            '\nResolved: %d / %d submaterial diffuse textures' % (loaded, slots) +
            '\nStatus: ' + self.texture_message +
            ('\nProblems:\n' + problems if problems else '') +
            '\n\nFor precise source paths, choose Textures > Diagnose Texture Links.'
        )

    def refresh_model_textures(self):
        if self.mode != 'models' or self.mesh is None:
            QMessageBox.information(self, 'Texture preview', 'Select a model first.')
            return
        self.load(self.current)
        self.status.setText(self.texture_message)

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
        if rel != self.current:
            self.character_skin_choice = None
        self.current = rel
        self.stream = None
        self.mesh = None
        self.materials = None
        self.character_preview = None
        self.texture_message = 'No texture lookup has run'
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
                # A CDF can define a full visible body (and a selected skin).
                # Prefer that over showing an isolated attachment when resolvable.
                from material_preview import _workspace_index
                from character_assembly import assemble_character
                if self.preview_index is None:
                    self.preview_index = _workspace_index(
                        self.workspace, self.manager.projects_path, self.manager.stage_path)
                try:
                    self.character_preview = assemble_character(
                        self.workspace, rel, self.preview_index,
                        texture_folder=self.texture_folder,
                        projects_root=self.manager.projects_path,
                        stage_root=self.manager.stage_path,
                        equipment=self.character_skin_choice)
                except (ValueError, OSError) as exc:
                    self.texture_message = 'Character assembly unavailable: ' + str(exc)
                mesh = (self.character_preview.mesh if self.character_preview else
                        find_preview_mesh(
                            self.workspace, rel, self.raw,
                            projects_root=self.manager.projects_path,
                            stage_root=self.manager.stage_path))
                self.mesh = mesh
                self._update_equipment_menu()
                self._paint_model(view, mesh)
                self.status.setText(
                    rel + ' | ' + report + ' | ' + self.texture_message)
            except (ValueError, OSError) as exc:
                self.model_fallback.setPixmap(QPixmap())
                self.model_fallback.setText(rel + '\n\n' + report +
                                            '\n\n3D preview unavailable: ' + str(exc))
                view.setCurrentWidget(self.model_fallback)
                self.status.setText(rel + ' | Native model inspection')

    def _update_equipment_menu(self):
        self.equipment_menu.clear()
        preview = self.character_preview
        self.equipment_menu.setEnabled(bool(preview and preview.equipment_options))
        if not preview:
            return
        choices = [('Body only', 'body')] + list(preview.equipment_options)
        for label, virtual in choices:
            action = self.equipment_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(virtual.casefold() == preview.selected_equipment.casefold())
            action.triggered.connect(
                lambda checked=False, name=virtual: self._select_character_equipment(name))

    def _select_character_equipment(self, name):
        if self.mode != 'models' or not self.current:
            return
        self.character_skin_choice = name
        self.load(self.current)

    def _paint_model(self, view, mesh):
        """The complete GPU controls are embedded by default in the Models tab."""
        if os.environ.get('QT_QPA_PLATFORM', '').lower() in ('offscreen', 'minimal'):
            self._software_model_preview(mesh)
            return
        try:
            from qt_gpu_viewer import QtGPUModelPanel
            materials = self.character_preview.materials if self.character_preview else None
            try:
                if materials is None:
                    from material_preview import load_preview_materials
                    materials = load_preview_materials(
                        self.workspace, mesh.source_name or self.current,
                        self.texture_folder, projects_root=self.manager.projects_path,
                        stage_root=self.manager.stage_path, index=self.preview_index)
                active = sorted({slot for _, _, slot in mesh.subsets
                                 if slot < len(materials.textures)})
                mapped = sum(materials.textures[slot] is not None
                             for slot in active) if active else 0
                self.texture_message = (
                    f'Textures: {mapped}/{len(active)} used material slots, '
                    f'{materials.count} decoded DDS from {materials.source}'
                    + (' | ' + materials.issues[0][:100] if materials.issues else ''))
                if self.character_preview:
                    self.texture_message += ' | CDF parts: ' + ', '.join(
                        self.character_preview.included)
            except (ValueError, OSError) as error:
                self.texture_message = 'Untextured: ' + str(error)
            self.materials = materials
            if self.model_panel is None:
                self.model_panel = QtGPUModelPanel(view, mesh, materials, compact=True)
                self.model_panel.setObjectName('primaryGpuModelViewer')
                view.addWidget(self.model_panel)
                self.model_gpu = self.model_panel.canvas
                self.model_gpu.setObjectName('defaultGpuModelPreview')
            else:
                self.model_panel.set_static_mesh(mesh, materials)
            view.setCurrentWidget(self.model_panel)
            from character_animation_catalog import discover_animations
            anim_model = (self.character_preview.skeleton if self.character_preview
                          else self.current)
            refs, idle, config = discover_animations(
                anim_model, self.preview_index,
                preferred_workspace=self.workspace,
                loose_index=self.loose_animation_index)
            from native_character_glb import create_character_glb
            # Capture plain paths and immutable preview objects; background
            # decoding never touches a QWidget or live Qt project state.
            source_workspace = self.workspace
            source_relative = self.current
            source_index = self.preview_index
            source_character = self.character_preview
            source_textures = self.texture_folder
            source_projects = self.manager.projects_path
            source_stage = self.manager.stage_path

            def build_native_clip(ref):
                return create_character_glb(
                    source_workspace, source_relative, source_index,
                    character=source_character, texture_folder=source_textures,
                    projects_root=source_projects, stage_root=source_stage, clip=ref)

            self.model_panel.set_native_catalog(refs, idle, config,
                                                loader=build_native_clip)
            self.model_panel._info.setText(
                mesh.source_name + '  •  ' + mesh.description +
                '\n' + self.texture_message[:260])
            self.model_gpu.update()
            QTimer.singleShot(450, self._check_gpu_preview)
        except Exception as exc:
            self._software_model_preview(mesh, reason=str(exc))

    def _check_gpu_preview(self):
        if self.model_gpu is None or not self.model_gpu.isVisible():
            return
        if not self.model_gpu.isValid() and self.mesh is not None:
            self._software_model_preview(
                self.mesh, reason='OpenGL context unavailable')

    def _software_model_preview(self, mesh, reason=''):
        view = self.views['models']
        if self.model_panel is not None:
            view.setCurrentWidget(self.model_cpu)
            self.model_panel.anim_timer.stop()
            self.model_panel.anim_playing = False
            self.model_panel.hide()
            self.model_panel.deleteLater()
            self.model_panel = None
            self.model_gpu = None
        self.model_cpu.set_mesh(mesh, reason)
        view.setCurrentWidget(self.model_cpu)
        if reason:
            self.status.setText(
                'Software preview (GPU unavailable: ' + reason[:140] + ')')

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
        self.preview_index = None
        self.character_preview = None
        self.character_skin_choice = None
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
            self.preview_index = None
            self.load(self.current)

    def explore(self):
        try:
            path = checked_path(self.workspace, self.current) if self.current else self.workspace/'files'
            if os.name == 'nt' and path.is_file():
                subprocess.Popen(['explorer.exe','/select,',str(path)])
            else:
                QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(path.parent if path.is_file() else path)))
        except Exception as exc:
            QMessageBox.critical(self, 'Could not open folder', str(exc))

    def open_extracted_external(self):
        try:
            path = checked_path(self.workspace, self.selected())
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:
            QMessageBox.critical(self, 'Could not open extracted file', str(exc))

    def copy_extracted_path(self):
        try:
            QApplication.clipboard().setText(str(checked_path(self.workspace,self.selected())))
        except Exception as exc:
            QMessageBox.critical(self, 'Could not copy path', str(exc))

    def copy_game_path(self):
        try:
            rel = self.selected()
            game = self.manager.game_root or self.manager._find_game_root()
            if not game:
                raise ValueError('The installed EvolveGame directory is not configured.')
            QApplication.clipboard().setText(str(Path(game)/Path(*rel.split('/'))))
        except Exception as exc:
            QMessageBox.critical(self, 'Could not copy game path', str(exc))

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
        menu.addAction('Reveal Extracted File in Explorer', self.explore)
        menu.addAction('Open Extracted File in External App', self.open_extracted_external)
        menu.addAction('Copy Extracted Absolute Path', self.copy_extracted_path)
        menu.addAction('Copy Installed Game Path', self.copy_game_path)
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

    def export_blender_model(self):
        """Export current mesh and diffuse textures from compatible unpacked PAKs."""
        if self.mode != 'models' or not self.current or self.mesh is None:
            QMessageBox.information(self, 'Export for Blender',
                                    'Select a supported CryEngine model first.')
            return
        name = Path(self.current).stem + '.glb'
        destination, _ = QFileDialog.getSaveFileName(
            self, 'Export Blender model (geometry and available textures)',
            str(Path(self.manager.projects_path) / name),
            'Blender glTF 2.0 (*.glb)')
        if not destination:
            return
        try:
            target = Path(destination)
            if target.suffix.casefold() != '.glb':
                target = target.with_suffix('.glb')
            if not target.parent.is_dir():
                raise FileNotFoundError('Choose an existing export folder.')
            # Native GLB skin weights correspond to the individual .skinm,
            # not the assembled preview vertex order. Preserve rigged export
            # semantics by exporting the original model, never the composite.
            mesh_source = self.current
            export_mesh = (find_preview_mesh(
                self.workspace, self.current, self.raw,
                projects_root=self.manager.projects_path,
                stage_root=self.manager.stage_path) if self.character_preview else self.mesh)
            payload, report = textured_model_from_workspace(
                export_mesh, self.workspace, mesh_source,
                texture_folder=self.texture_folder,
                projects_root=self.manager.projects_path,
                stage_root=self.manager.stage_path)
            target.write_bytes(payload)
            message = ('Exported %d vertices, %d triangles, %d diffuse textures. %s.' %
                       (len(export_mesh.vertices), len(export_mesh.triangles),
                        report['texture_count'], report['rig_status']))
            self.status.setText(message)
            if report['texture_count'] == 0:
                self.status.setText(message + ' Missing: ' + report['texture_status'][:180])
        except Exception as exc:
            QMessageBox.critical(self, 'Blender export failed', str(exc))

    def export_blender_folder(self):
        """Export supported meshes with textures from other unpacked PAK workspaces."""
        source = QFileDialog.getExistingDirectory(
            self, 'Choose unpacked model folder', str(self.workspace / 'files'))
        if not source:
            return
        destination = QFileDialog.getExistingDirectory(
            self, 'Choose a separate Blender export folder',
            str(Path(self.manager.projects_path)))
        if not destination:
            return
        try:
            results, skipped = export_extracted_folder(
                source, destination, projects_root=self.manager.projects_path,
                stage_root=self.manager.stage_path, texture_folder=self.texture_folder)
            textured = sum(bool(details['texture_count']) for _, _, _, details in results)
            rigged = sum(bool(details.get('bone_count')) for _, _, _, details in results)
            note = ('%d GLB exports (%d textured, %d rigged); %d skipped. '
                    'CAF/DBA animation tracks are not exported.' %
                    (len(results), textured, rigged, len(skipped)))
            if skipped:
                (Path(destination) / 'Blender_export_report.txt').write_text(
                    '\n'.join(name + ': ' + reason for name, reason in skipped),
                    encoding='utf-8')
            self.status.setText(note)
            QMessageBox.information(self, 'Blender export', note)
        except Exception as exc:
            QMessageBox.critical(self, 'Folder export failed', str(exc))

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

    def choose_animation_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, 'Choose extracted game animation root (containing characters/...)')
        if not folder:
            return
        from character_animation_catalog import asset_directory_index
        try:
            self.loose_animation_index = asset_directory_index(folder)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Animation scan', str(exc))
            return
        self.animation_folder = Path(folder).resolve()
        if self.current and self.mode == 'models':
            self.load(self.current)
        else:
            self.status.setText('Extracted animation directory indexed: ' + folder)

    def choose_texture_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, 'Choose unpacked TS texture folder (recursive lookup)')
        if not folder:
            return
        selected = Path(folder).resolve()
        # A common error is selecting the empty data-PAK textures directory
        # while the matching TS archive was extracted in the same batch.
        current_archive = next((name for name, ws in self.archive_map.items()
                                if Path(ws).resolve() == self.workspace), '')
        if current_archive.casefold().endswith('_data.pak'):
            companion_name = current_archive[:-9] + '_ts.pak'
            other = next((Path(ws).resolve() for name, ws in self.archive_map.items()
                          if name.casefold() == companion_name.casefold()), None)
            files_root = (self.workspace / 'files').resolve()
            if other is not None and selected.is_relative_to(files_root):
                relative = selected.relative_to(files_root)
                candidate = other / 'files' / relative
                if not candidate.is_dir():
                    candidate = other / 'files'
                if candidate.is_dir() and selected != candidate:
                    answer = QMessageBox.question(
                        self, 'Use companion texture archive?',
                        'This folder is inside the character DATA PAK. '
                        'The companion TS PAK is also unpacked.\n\n'
                        'Use the matching TS archive folder instead?\n' + str(candidate),
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                    if answer == QMessageBox.Yes:
                        selected = candidate
        self.texture_folder = selected
        self.materials = None
        if self.mesh is not None and self.current:
            self.refresh_model_textures()
        else:
            self.status.setText('Texture folder: ' + str(selected))

    def use_auto_textures(self):
        self.texture_folder = None
        self.refresh_model_textures()

    def open_gpu_viewer(self):
        if self.mode != 'models' or not self.current or self.mesh is None:
            QMessageBox.information(self, 'GPU Model Viewer',
                                    'Select a supported CryEngine model to load a 3D mesh first.')
            return
        try:
            from material_preview import load_preview_materials
            from qt_gpu_viewer import QtGPUModelDialog
            material_note = ''
            try:
                materials = self.materials
                if materials is None:
                    materials = load_preview_materials(
                        self.workspace, self.current,
                        self.texture_folder,
                        projects_root=self.manager.projects_path,
                        stage_root=self.manager.stage_path, index=self.preview_index)
                material_note = (f' | GPU diffuse textures: {materials.count}'
                                 f' | source: {materials.source}')
            except (ValueError, OSError) as error:
                # Models must still be inspectable without a texture archive.
                materials = None
                material_note = ' | Untextured: ' + str(error)[:180]
            dialog = QtGPUModelDialog(self, self.mesh, materials)
            if self.model_panel is not None:
                from character_animation_catalog import discover_animations
                anim_model = (self.character_preview.skeleton if self.character_preview
                              else self.current)
                refs, idle, _config = discover_animations(
                    anim_model, self.preview_index, preferred_workspace=self.workspace,
                    loose_index=self.loose_animation_index)
                from native_character_glb import create_character_glb
                source_workspace = self.workspace
                source_relative = self.current
                source_index = self.preview_index
                source_character = self.character_preview
                source_textures = self.texture_folder
                source_projects = self.manager.projects_path
                source_stage = self.manager.stage_path
                def popout_loader(ref):
                    return create_character_glb(
                        source_workspace, source_relative, source_index,
                        character=source_character, texture_folder=source_textures,
                        projects_root=source_projects, stage_root=source_stage, clip=ref)
                dialog.panel.set_native_catalog(refs, idle, loader=popout_loader)
            dialog.setAttribute(Qt.WA_DeleteOnClose, False)
            self.gpu_windows.append(dialog)
            self.status.setText(self.current + material_note)
            dialog.show()
            dialog.raise_()
        except Exception as error:
            QMessageBox.warning(
                self, 'GPU Model Viewer unavailable',
                str(error) + '\\n\\nThe static Qt model preview is still available. '
                'On Windows, verify that an OpenGL-capable graphics driver is installed.')

    def find_model_textures(self):
        if self.mode != 'models' or self.mesh is None:
            return
        try:
            result = model_material_links(
                self.workspace, self.mesh.source_name or self.current,
                projects_root=self.manager.projects_path,
                stage_root=self.manager.stage_path)
            lines = [
                'Model: '+str(self.mesh.source_name or self.current),
                'Material: '+str(result.get('material')),
                'Status: '+str(result.get('status')),
                'Cross-PAK links: '+str(result.get('collection')),
            ]
            for record in result.get('textures', []):
                lines.append('\n'+str(record.get('map'))+': '+str(record.get('reference')))
                lines.append('  '+str(record.get('status')))
                lines.append('  Paths: '+', '.join(record.get('paths', [])))
                if record.get('archives'):
                    lines.append('  Archives: '+', '.join(record.get('archives', [])))
            if result.get('status') == 'ambiguous':
                lines.append('\nMultiple different versions of the material were found:')
                for candidate in result.get('candidates', []):
                    lines.append('  '+str(candidate.get('archive',''))+' | '+
                                 str(candidate.get('workspace','')))
            elif result.get('status') == 'missing':
                lines.append('\nMaterial missing: unpack the PAK containing its .mtl file.')
            lines.append('\nCryEngine .tif references may correspond to .dds/.dds.0 textures.')
            QMessageBox.information(self,'Model textures','\n'.join(lines))
        except Exception as exc:
            QMessageBox.critical(self,'Model textures',str(exc))

    def help(self):
        QMessageBox.information(
            self,'Editor help',
            'Files / Text: select manifest-listed files, edit, Save with checksum validation, '
            'and use the right-click import/restore actions.\n\n'
            'Images / Textures: preview DDS, export PNG and import compatible DDS/PNG. '
            'Streaming .dds.0/.1 files are reassembled and replaced together.\n\n'
            'Models: GPU rendering opens by default on supported Windows systems; '
            'software preview is the fallback. Export for Blender writes geometry, '
            'UVs and diffuse textures from matching unpacked PAK workspaces. '
            'When compatible .skin/.chr companions exist, original bone hierarchy '
            'and skin weights are exported as a poseable GLB armature. '
            'Model/skeleton data comes from the character Data PAK; texture '
            'fragments typically come from the companion TS PAK. '
            'Animation definitions can accompany models, while actual CAF/DBA '
            'tracks must be present separately. Unsupported rig formats are '
            'reported as unrigged rather than silently inventing bones.\n\n'
            'Native export and same-layout native import remain available. '
            'Viewer: left-drag rotates, right/middle-drag pans, wheel zooms. '
            'Faceted shading preserves armor edges; smooth shading is optional. '
            'The Models tab contains the full GPU viewer and animation timeline. '
            'Open GPU Viewer optionally pops out the same controls. '
            'Original CAF/DBA clips can be converted by a separately installed '
            'CryEngine converter, then imported as animated GLB files.')

    def closeEvent(self, event):
        if not self._confirm_buffer():
            event.ignore()
            return
        if self.model_panel is not None and not self.model_panel.close():
            event.ignore()
            return
        super().closeEvent(event)
