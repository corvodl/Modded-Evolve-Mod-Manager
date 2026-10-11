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
import time

from PIL import Image
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QPixmap, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QTreeWidget, QTreeWidgetItem, QLineEdit, QPushButton, QPlainTextEdit,
    QTabWidget, QSplitter, QStackedWidget, QFileDialog, QMessageBox, QMenu, QScrollArea,
    QDockWidget, QTreeWidgetItemIterator, QFrame,
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
from version_info import app_version
from studio_polish_117 import (prepare_model_cached, clear_model_cache,
    model_cache_stats, export_preflight, project_session_key,
    sanitized_project_session)


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
        stamp = QLabel(app_version())
        stamp.setObjectName('version')
        stamp.setAlignment(Qt.AlignRight)
        layout.addWidget(stamp)
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
        self.favorites = set(manager.settings.get('favorite_assets',[]))
        self.current = None
        self.raw = b''
        self.stream = None
        self.loaded_text = ''
        self.mode = None
        self.mesh = None
        self.model_gpu = None
        self.model_panel = None
        self._sidebar_visibility = {'files': True, 'images': True, 'models': False}
        self.model_fallback = None
        self.model_cpu = None
        self.texture_folder = None
        self.animation_folder = None
        self.loose_animation_index = None
        self.preview_index = None
        self.character_preview = None
        self.character_skin_choice = None
        self._prepared_animation = None
        self.gpu_windows = []
        self._export_native_pool = None
        self._export_native_signals = None
        self._export_native_target = None
        self._model_pool = None
        self._model_signals = None
        self._model_generation = 0
        self._model_prepared = False
        self._prepared_geometry = None
        self._prepared_animation = None
        self._materials_error = ''
        self.materials = None
        self.texture_message = 'No model selected'
        self.model_load_ms = None
        self.model_cache_hit = False
        self._model_started_at = None
        self._preflight_token = 0
        self._preflight_pool = None
        self._preflight_signals = None
        self._diagnostics_dock = None
        self._diagnostics_timer = None
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
        # One always-present explorer drives the three internal viewers.
        # Category tabs are implementation details, never exposed to users.
        top = QHBoxLayout()
        self.current_file_label = QLabel('Select a file from File Explorer')
        self.current_file_label.setObjectName('currentWorkspaceAsset')
        top.addWidget(self.current_file_label, 1)
        self.sidebar_button = QPushButton('☰ Hide File Explorer')
        self.sidebar_button.setObjectName('fileExplorerToggle')
        self.sidebar_button.setCheckable(True)
        self.sidebar_button.setChecked(True)
        self.sidebar_button.setToolTip('Show / hide the asset explorer (Ctrl+B)')
        self.sidebar_button.toggled.connect(self.toggle_file_explorer)
        top.addWidget(self.sidebar_button)
        main.addLayout(top)

        self.editor_split = QSplitter(Qt.Horizontal)
        self.editor_split.setObjectName('unifiedEditorSplitter')
        self.tabs = QTabWidget()
        self.tabs.setObjectName('internalFileTypeViewer')
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet('QTabWidget::pane { border: 0; }')
        self.trees, self.views, self.actions = {}, {}, {}
        self.splitters = {section: self.editor_split for section in self.TABS}
        for section, label in zip(self.TABS, ('Text', 'Textures', '3D Model')):
            tab = QWidget()
            box = QVBoxLayout(tab)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            widgets = {}
            if section in ('files', 'images'):
                bar = QHBoxLayout()
                actions = (
                    (('Save', self.save), ('Find Text', self.find_text),
                     ('Import / Replace File', self.import_raw),
                     ('Restore Original', self.restore_original))
                    if section == 'files' else
                    (('Export PNG', self.export_png), ('Import DDS', self.import_dds),
                     ('Import PNG', self.import_png))
                )
                for text, callback in actions:
                    widgets[text] = self._button(text, callback)
                    bar.addWidget(widgets[text])
                bar.addStretch(1)
                box.addLayout(bar)
            if section == 'files':
                view = QPlainTextEdit()
                view.setReadOnly(True)
                view.setTabStopDistance(35)
            elif section == 'images':
                view = QLabel('Choose a DDS texture in File Explorer.')
                view.setAlignment(Qt.AlignCenter)
                view.setWordWrap(True)
            else:
                view = QStackedWidget()
                self.model_fallback = QLabel(
                    'Choose a CryEngine character or model in File Explorer.')
                self.model_fallback.setAlignment(Qt.AlignCenter)
                self.model_fallback.setWordWrap(True)
                view.addWidget(self.model_fallback)
                self.model_cpu = QtCPUModelCanvas(view)
                self.model_cpu.setObjectName('cpuModelPreview')
                view.addWidget(self.model_cpu)
            box.addWidget(view, 1)
            self.tabs.addTab(tab, label)
            self.views[section] = view
            self.actions[section] = widgets
        self.editor_split.addWidget(self.tabs)

        self.explorer = QFrame()
        self.explorer.setObjectName('fileExplorerPane')
        self.explorer.setStyleSheet(
            'QFrame#fileExplorerPane { background: #222128; '
            'border-left: 2px solid #62545e; }'
            'QLabel#fileExplorerHeading { font-weight: bold; '
            'font-size: 13px; color: #f3edf2; }')
        explorer_box = QVBoxLayout(self.explorer)
        explorer_box.setContentsMargins(8, 8, 8, 8)
        explorer_box.setSpacing(7)
        explorer_head = QHBoxLayout()
        title = QLabel('▤  FILE EXPLORER')
        title.setObjectName('fileExplorerHeading')
        explorer_head.addWidget(title, 1)
        self.explorer_count = QLabel('')
        explorer_head.addWidget(self.explorer_count)
        explorer_box.addLayout(explorer_head)

        self.archive_select = None
        if len(self.archive_map) > 1:
            explorer_box.addWidget(QLabel('PAK / PROJECT'))
            self.archive_select = QComboBox()
            self.archive_select.setObjectName('explorerPakSelect')
            self.archive_select.setToolTip('Change the extracted PAK opened in this editor')
            self.archive_select.addItems(list(self.archive_map))
            current_archive = next(
                (name for name, path in self.archive_map.items()
                 if path == self.workspace), '')
            self.archive_select.setCurrentText(current_archive)
            self.archive_select.currentTextChanged.connect(self.switch_archive)
            explorer_box.addWidget(self.archive_select)

        self.search = QLineEdit()
        self.search.setObjectName('fileExplorerSearch')
        self.search.setPlaceholderText('Search files and folders…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.populate)
        explorer_box.addWidget(self.search)
        explorer_controls = QHBoxLayout()
        self.favorites_only = QPushButton('★ Favorites')
        self.favorites_only.setCheckable(True)
        self.favorites_only.toggled.connect(self.populate)
        explorer_controls.addWidget(self.favorites_only)
        explorer_controls.addWidget(self._button('Open Folder', self.explore))
        explorer_controls.addStretch()
        explorer_box.addLayout(explorer_controls)

        self.file_tree = QTreeWidget()
        self.file_tree.setObjectName('unifiedAssetExplorerTree')
        self.file_tree.setColumnCount(2)
        self.file_tree.setHeaderLabels(('File / Folder', 'Size'))
        self.file_tree.setSortingEnabled(True)
        self.file_tree.setColumnWidth(0, 260)
        self.file_tree.setColumnWidth(1, 75)
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.itemSelectionChanged.connect(self.load_selection)
        self.file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(
            lambda point: self.show_context_menu('files', point))
        explorer_box.addWidget(self.file_tree, 1)
        self.trees = {section: self.file_tree for section in self.TABS}
        self._explorer_visible = True
        self.editor_split.addWidget(self.explorer)
        self.editor_split.setStretchFactor(0, 5)
        self.editor_split.setStretchFactor(1, 2)
        self.editor_split.setSizes([940, 310])
        main.addWidget(self.editor_split, 1)
        self.status = QLabel('Files remain inside the extracted project; build and add each PAK separately.')
        self.status.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        self.dependency_button=QPushButton('Dependencies…')
        self.dependency_button.setEnabled(False)
        menu=QMenu(self.dependency_button)
        menu.addAction('Asset Dependency Graph',self.open_dependency_graph)
        menu.addAction('Find Missing Textures',self.open_texture_resolver)
        menu.addAction('Validate Blender Export',self.inspect_blender_export)
        self.dependency_button.setMenu(menu)
        bottom.addWidget(self.dependency_button)
        self.version_label = QLabel(app_version())
        self.version_label.setObjectName('version')
        self.version_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.version_label)
        main.addLayout(bottom)
        QTimer.singleShot(0, self._restore_workspace_layout)
        QTimer.singleShot(60, self._restore_project_session)
        self.save_shortcut = QShortcut(QKeySequence.Save, self)
        self.save_shortcut.activated.connect(self.save)
        self.find_shortcut = QShortcut(QKeySequence.Find, self)
        self.find_shortcut.activated.connect(self.find_text)
        self.populate()
        self._sync_file_explorer_button()
        self.recovery_timer=QTimer(self)
        self.recovery_timer.setInterval(30000)
        self.recovery_timer.timeout.connect(self._autosave_recovery)
        self.recovery_timer.start()

    def _build_editor_menus(self):
        """Blender-style discoverable menu bar; no oversized model-action rows."""
        bar = self.menuBar()
        bar.setObjectName('editorMenuBar')
        menus = {name: bar.addMenu(name) for name in
                 ('File', 'Edit', 'View', 'Model', 'Textures', 'Animation', 'Tools', 'Help')}
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
        action('File', 'Export Animated Character (.glb)…', self.export_animated_character)
        action('File', 'Export Model Folder…', self.export_blender_folder)
        action('File', 'Batch Asset Export…', self.open_asset_search)
        action('File', 'Project History & Undo…', self.open_project_history)
        action('Edit', 'Save Text', self.save)
        action('Edit', 'Find Text', self.find_text)
        action('Edit', 'Visual Gameplay Values…', self.open_gameplay_editor)
        action('Edit', 'Gameplay Presets…', self.open_gameplay_presets)
        action('Edit', 'Global Search & Replace…', self.open_global_search)
        action('Edit', 'Material Properties 2.0…', self.open_material_properties)
        action('Edit', 'Import / Replace File…', self.import_raw)
        action('Edit', 'Restore Original', self.restore_original)
        self.equipment_menu = menus['Model'].addMenu('Equipped Skin')
        self.equipment_menu.setEnabled(False)
        action('Model', 'Import Same-Layout Model…', self.import_model)
        action('Model', 'Open GPU Viewer in New Window', self.open_gpu_viewer)
        action('Model', 'Compare Models Side by Side…', self.open_model_compare)
        action('Model', 'Validate Blender Export…', self.inspect_blender_export)
        action('Model', 'Asset Dependency Graph…', self.open_dependency_graph)
        action('Model', 'Reset Camera', lambda: self._with_model_panel(
            lambda p: p.reset_view()))
        action('Textures', 'Diagnose Texture Links…', self.find_model_textures)
        action('Textures', 'Texture Preview Status…', self.texture_diagnostics)
        action('Textures', 'Resolve Missing Textures…', self.open_texture_resolver)
        action('Textures', 'Advanced Texture Adjustments…', self.open_texture_editor)
        action('Textures', 'Choose Extracted Texture Folder…', self.choose_texture_folder)
        action('Textures', 'Use Automatic Cross-PAK Lookup', self.use_auto_textures)
        action('Textures', 'Reload Model Textures', self.refresh_model_textures)
        action('Tools', 'Validate Project Mod…', self.open_mod_validator)
        action('Tools', 'Performance Monitor', self.toggle_diagnostics)
        action('Tools', 'Global Search & Replace…', self.open_global_search)
        action('Tools', 'Recovery Snapshot Now', self._autosave_recovery)
        action('Tools', 'Review Project History…', self.open_project_history)
        action('Tools', 'PAK Difference Inspector…', self.manager.open_pak_diff)
        action('Tools', 'Open Level Editor 1.0…', lambda: self.manager.open_level_editor(self.workspace))
        action('Tools', 'Search All Extracted Assets…', self.open_asset_search)
        action('Tools', 'Batch Export Assets…', self.open_asset_search)
        action('Tools', 'Visual Gameplay Editor…', self.open_gameplay_editor)
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
        menus['View'].addSeparator()
        explorer_action = menus['View'].addAction('File Explorer')
        explorer_action.setCheckable(True)
        explorer_action.setShortcut('Ctrl+B')
        explorer_action.setChecked(True)
        explorer_action.toggled.connect(self.toggle_file_explorer)
        self._file_explorer_action = explorer_action
        for name in ('Balanced', 'Model Focus', 'Files First'):
            action('View', 'Layout: ' + name, lambda n=name: self.set_workspace_layout(n))
        action('View', 'Save Current Layout Preset', self.save_workspace_layout)
        action('View', 'Restore Saved Layout', self._restore_workspace_layout)
        self._view_actions['texture'].setChecked(True)
        self._view_actions['faceted'].setChecked(True)
        menus['View'].aboutToShow.connect(self._sync_view_menu)

    def _sync_file_explorer_button(self):
        showing = self._explorer_visible
        self.sidebar_button.blockSignals(True)
        self.sidebar_button.setChecked(showing)
        self.sidebar_button.setText('☰ Hide File Explorer' if showing else '☰ Show File Explorer')
        self.sidebar_button.blockSignals(False)
        if hasattr(self, '_file_explorer_action'):
            self._file_explorer_action.blockSignals(True)
            self._file_explorer_action.setChecked(showing)
            self._file_explorer_action.blockSignals(False)

    def toggle_file_explorer(self, visible):
        self._explorer_visible = bool(visible)
        self.explorer.setVisible(bool(visible))
        if visible:
            self.editor_split.setSizes([940, 310])
        self._sync_file_explorer_button()

    def set_workspace_layout(self, name):
        options = {'Balanced': (1, 4), 'Model Focus': (1, 7),
                   'Files First': (3, 2)}
        if name not in options:
            raise ValueError('Unknown layout preset.')
        explorer_width, viewport_width = options[name]
        self.toggle_file_explorer(True)
        self.editor_split.setSizes([viewport_width * 120, explorer_width * 120])

    def save_workspace_layout(self):
        self.manager.settings['editor_workspace_layout'] = {
            'tab': self.tabs.currentIndex(),
            'sizes': {section: split.sizes() for section, split in self.splitters.items()},
        }
        try:
            self.manager.persist()
            self.status.setText('Workspace layout preset saved to manager settings.')
        except OSError as exc:
            QMessageBox.warning(self, 'Layout not saved', str(exc))

    def _restore_workspace_layout(self):
        settings = self.manager.settings.get('editor_workspace_layout', {})
        if not isinstance(settings, dict):
            return
        tab = settings.get('tab', 0)
        if isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)
        sizes = settings.get('sizes', {})
        if isinstance(sizes, dict):
            pair = sizes.get('models') or sizes.get('files')
            if (isinstance(pair, list) and len(pair) == 2 and
                    all(isinstance(n, int) and n > 0 for n in pair)):
                self.editor_split.setSizes(pair)

    def _capture_playback_preferences(self):
        if self.current and self.mode=='models' and self.model_panel is not None:
            prefs=self.manager.settings.get('animation_playback_preferences',{})
            if not isinstance(prefs,dict):
                prefs={}
            key=project_session_key(self.workspace)+'|'+self.current.casefold()
            prefs[key]=self.model_panel.playback_preferences()
            while len(prefs)>200:
                prefs.pop(next(iter(prefs)))
            self.manager.settings['animation_playback_preferences']=prefs

    def _save_project_session(self):
        self._capture_playback_preferences()
        if self._dirty():
            return
        selected = ({self.mode: self.current}
                    if self.current in self.records and self.mode in self.TABS
                    else {})
        camera={}
        if self.model_gpu is not None:
            camera={'rotation':list(self.model_gpu.rotation),
                    'pan':list(self.model_gpu.pan),'zoom':self.model_gpu.zoom}
        data={'tab':self.tabs.currentIndex(),'selected':selected,
              'sizes':{key:split.sizes() for key,split in self.splitters.items()},
              'camera':camera}
        sessions=self.manager.settings.get('editor_project_sessions',{})
        if not isinstance(sessions,dict):
            sessions={}
        sessions[project_session_key(self.workspace)]=sanitized_project_session(data,self.records)
        while len(sessions)>80:
            sessions.pop(next(iter(sessions)))
        self.manager.settings['editor_project_sessions']=sessions
        try:
            self.manager.persist()
        except OSError:
            pass

    def _restore_project_session(self):
        sessions=self.manager.settings.get('editor_project_sessions',{})
        if not isinstance(sessions,dict):
            return
        session=sanitized_project_session(
            sessions.get(project_session_key(self.workspace)),self.records)
        if not session:
            return
        for name,pair in session.get('sizes',{}).items():
            if name in self.splitters:
                self.splitters[name].setSizes(pair)
        self._restored_camera=session.get('camera',{})
        selected = session.get('selected', {})
        tab = session.get('tab', 0)
        choice = selected.get(self.TABS[tab]) if 0 <= tab < len(self.TABS) else None
        if choice not in self.records:
            choice = next((r for r in selected.values() if r in self.records), None)
        self._restored_camera = session.get('camera', {})
        if choice is not None:
            it = QTreeWidgetItemIterator(self.file_tree)
            while it.value():
                item = it.value()
                if item.data(0, Qt.UserRole) == choice:
                    self.file_tree.blockSignals(True)
                    self.file_tree.setCurrentItem(item)
                    self.file_tree.blockSignals(False)
                    break
                it += 1
            try:
                self.load(choice)
            except (ValueError, OSError) as error:
                self.status.setText('Could not restore last file: ' + str(error)[:160])

    def inspect_blender_export(self):
        if self.mode!='models' or self.mesh is None or not self.current:
            QMessageBox.information(self,'Blender Export Validator',
                                    'Select a supported model in the Models tab.')
            return
        from qt_gpu_viewer import _NativeConversion,_NativeResult
        from PySide6.QtCore import QThreadPool
        if self._preflight_pool is None:
            self._preflight_pool=QThreadPool(self)
            self._preflight_pool.setMaxThreadCount(1)
            self._preflight_signals=_NativeResult(self)
            self._preflight_signals.completed.connect(self._preflight_ready)
        self._preflight_token+=1
        token=(self._preflight_token,str(self.workspace),self.current)
        workspace,relative=self.workspace,self.current
        mesh=self.mesh
        raw=self.raw
        character=self.character_preview
        references=(self._prepared_animation[0] if self._prepared_animation else ())
        projects,stage=self.manager.projects_path,self.manager.stage_path
        textures=self.texture_folder
        def work(_ignored):
            source_mesh=(find_preview_mesh(workspace,relative,raw,
                projects_root=projects,stage_root=stage) if character else mesh)
            return (export_preflight(source_mesh,workspace,relative,
                texture_folder=textures,projects_root=projects,
                stage_root=stage,animation_refs=references),None)
        self.status.setText('Validating Blender export in background…')
        self._preflight_pool.start(
            _NativeConversion(work,None,token,self._preflight_signals))

    def _preflight_ready(self,token,result,error):
        if token!=(self._preflight_token,str(self.workspace),self.current):
            return
        if error:
            self.status.setText('Export validation failed: '+str(error)[:180])
            QMessageBox.warning(self,'Blender Export Validator',str(error))
            return
        report,_unused=result
        self.status.setText('Blender export checked: %s, %d diffuse textures, %d animation candidates.'%
                            (report['rig'],report['textures'],report['animation_candidates']))
        QMessageBox.information(self,'Blender Export Validator',json.dumps(report,indent=2))

    def toggle_diagnostics(self):
        if self._diagnostics_dock is None:
            dock=QDockWidget('Performance Monitor',self)
            dock.setObjectName('studioPerformanceMonitor')
            dock.setAllowedAreas(Qt.LeftDockWidgetArea|Qt.RightDockWidgetArea)
            info=QLabel()
            info.setWordWrap(True)
            info.setMargin(12)
            dock.setWidget(info)
            self.addDockWidget(Qt.RightDockWidgetArea,dock)
            dock.hide()
            self._diagnostics_dock=dock
            self._diagnostics_label=info
            self._diagnostics_timer=QTimer(self)
            self._diagnostics_timer.setInterval(1000)
            self._diagnostics_timer.timeout.connect(self._update_diagnostics)
            self._diagnostics_timer.start()
        self._diagnostics_dock.setVisible(not self._diagnostics_dock.isVisible())
        self._update_diagnostics()

    def _update_diagnostics(self):
        if self._diagnostics_dock is None or not self._diagnostics_dock.isVisible():
            return
        materials=self.materials
        gpu_bytes=0
        if materials is not None:
            for group in ('textures','normal_maps','specular_maps'):
                for image in getattr(materials,group,()):
                    if image is not None:
                        gpu_bytes+=image.width*image.height*len(image.getbands())
        from studio_polish_117 import model_cache_stats
        self._diagnostics_label.setText(
            'Last model preparation: %s ms\nCache hit: %s\nDecoded texture allocation estimate: %.2f MiB\n'
            'Editor file-tree refresh: %.1f ms\nCache: %s\n'
            'GPU texture upload (last event): %s ms\nFPS: not measured.' %
            ('%.1f'%self.model_load_ms if self.model_load_ms is not None else 'Not sampled',
             'yes' if self.model_cache_hit else 'no',gpu_bytes/1048576,
             getattr(self,'last_populate_ms',0),model_cache_stats(),
             getattr(self.model_gpu,'gpu_upload_ms',None) if self.model_gpu else 'Not sampled'))

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

    def toggle_favorite(self):
        try:
            key = str(self.workspace)+'|'+self.selected()
            if key in self.favorites:
                self.favorites.remove(key)
            else:
                if len(self.favorites)>=1500:
                    raise ValueError('Favorite bookmark limit reached.')
                self.favorites.add(key)
            self.manager.settings['favorite_assets']=sorted(self.favorites)
            self.manager.persist()
            self.populate()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Favorites',str(exc))

    def open_project_history(self):
        try:
            from qt_asset_intelligence import ProjectHistoryDialog
            ProjectHistoryDialog(self).exec()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Project History',str(exc))

    def open_dependency_graph(self):
        try:
            from qt_asset_intelligence import DependencyDialog
            DependencyDialog(self).exec()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Asset Dependencies',str(exc))

    def open_texture_resolver(self):
        try:
            from qt_asset_intelligence import TextureResolverDialog
            TextureResolverDialog(self).exec()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Texture Resolution',str(exc))

    def open_mod_validator(self):
        try:
            from qt_asset_intelligence import ValidationDialog
            ValidationDialog(self).exec()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Mod Validation',str(exc))

    def open_gameplay_presets(self):
        try:
            from qt_workflow_lab import GameplayPresetDialog
            GameplayPresetDialog(self).exec()
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Gameplay Presets',str(exc))

    def open_global_search(self):
        try:
            from qt_workflow_lab import GlobalSearchDialog
            GlobalSearchDialog(self).exec()
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Global Search',str(exc))

    def open_material_properties(self):
        try:
            from qt_workflow_lab import MaterialEditorV2
            MaterialEditorV2(self).exec()
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Material Properties',str(exc))

    def _autosave_recovery(self):
        if not self._dirty() or not self.current:
            return
        control=self.views['files']
        if control.isReadOnly():
            return
        try:
            from workflow_lab import save_recovery
            save_recovery(self.workspace,self.current,control.toPlainText(),self.raw)
        except (OSError,ValueError) as exc:
            self.status.setText('Autosave recovery could not be written: '+str(exc)[:150])

    def _load_recovery(self):
        if self.mode!='files' or not self.current or self.views['files'].isReadOnly():
            return
        from workflow_lab import read_recovery,clear_recovery
        try:
            text=read_recovery(self.workspace,self.current,self.raw)
            if text is None or text==self.loaded_text:
                return
            if QMessageBox.question(self,'Recover unsaved changes',
                'Unsaved text from an earlier edit session is available. '
                'Restore it into the editor (without modifying the project file yet)?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)==QMessageBox.Yes:
                self.views['files'].setPlainText(text)
                self.status.setText('Recovered unsaved edits. Save to commit them.')
            else:
                clear_recovery(self.workspace,self.current)
        except (OSError,ValueError):
            pass

    def open_gameplay_editor(self):
        try:
            from qt_asset_studio import GameplayValuesDialog
            GameplayValuesDialog(self).exec()
        except (ValueError,OSError,RuntimeError) as exc:
            QMessageBox.warning(self,'Visual Gameplay Editor',str(exc))

    def open_texture_editor(self):
        try:
            from qt_asset_studio import TextureAdjustDialog
            TextureAdjustDialog(self).exec()
        except (ValueError,OSError,RuntimeError) as exc:
            QMessageBox.warning(self,'Advanced Texture Editor',str(exc))

    def open_model_compare(self):
        try:
            from qt_asset_studio import ModelCompareDialog
            ModelCompareDialog(self).exec()
        except (ValueError,OSError,RuntimeError) as exc:
            QMessageBox.warning(self,'Compare Models',str(exc))

    def open_asset_search(self):
        try:
            from qt_asset_studio import AssetSearchDialog
            AssetSearchDialog(self).exec()
        except (ValueError,OSError,RuntimeError) as exc:
            QMessageBox.warning(self,'Asset Search & Batch Export',str(exc))

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
        clear_model_cache(self.workspace)
        self.preview_index = None
        self.load(self.current)
        self.status.setText('Reloading materials and textures…')

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
        seen = set()
        for entry in data['entries']:
            rel = normalized_name(entry['path'])
            folded = rel.casefold()
            if rel != entry['path'] or folded in seen:
                raise ValueError('Unsafe or duplicated extracted path: '+rel)
            seen.add(folded)
            entries[rel] = entry
        return entries

    def _category(self, rel):
        if is_dds(rel) or is_split_dds(rel):
            return 'images'
        if is_model(rel):
            return 'models'
        return 'files'

    def populate(self):
        """One searchable PAK tree; entries open the matching viewer on click."""
        started = time.perf_counter()
        needle = self.search.text().casefold()
        tree = self.file_tree
        prior = tree.currentItem()
        previous = prior.data(0, Qt.UserRole) if prior is not None else None
        tree.blockSignals(True)
        try:
            tree.clear()
            folders = {}
            entries = [rel for rel in sorted(self.records, key=str.casefold)
                       if needle in rel.casefold() and
                       (not self.favorites_only.isChecked()
                        or str(self.workspace) + '|' + rel in self.favorites)]
            first = {}
            for rel in entries:
                if is_split_dds(rel):
                    base, suffix = rel.rsplit('.', 1)
                    first[base.casefold()] = min(first.get(base.casefold(), 99),
                                                 int(suffix))
            entries = [rel for rel in entries if not is_split_dds(rel) or
                       int(rel.rsplit('.', 1)[1]) ==
                       first[rel.rsplit('.', 1)[0].casefold()]]
            restored = None
            for rel in entries:
                parent = None
                parts = rel.split('/')
                for i, part in enumerate(parts[:-1]):
                    key = '/'.join(parts[:i + 1])
                    if key not in folders:
                        folder = QTreeWidgetItem([part, ''])
                        folder.setToolTip(0, key)
                        if parent is not None:
                            parent.addChild(folder)
                        else:
                            tree.addTopLevelItem(folder)
                        folders[key] = folder
                    parent = folders[key]
                key = str(self.workspace) + '|' + rel
                symbol = '★ ' if key in self.favorites else ''
                path = self.workspace / 'files' / rel
                try:
                    stat = path.stat()
                    size = ('%.1f MiB' % (stat.st_size / 1048576)
                            if stat.st_size > 1048576 else
                            '%.0f KiB' % (stat.st_size / 1024))
                except OSError:
                    size = 'Missing'
                category = self._category(rel)
                item = QTreeWidgetItem([symbol + parts[-1], size])
                item.setData(0, Qt.UserRole, rel)
                item.setToolTip(0, category.title() + ' • ' + rel)
                if parent is not None:
                    parent.addChild(item)
                else:
                    tree.addTopLevelItem(item)
                if rel == previous:
                    restored = item
            if restored is not None:
                tree.setCurrentItem(restored)
            if needle:
                tree.expandAll()
            self.explorer_count.setText(str(len(entries)) + ' files')
        finally:
            tree.blockSignals(False)
        self.last_populate_ms = round((time.perf_counter() - started) * 1000, 3)

    def selected(self):
        items = self.file_tree.selectedItems()
        if not items:
            raise ValueError('Choose an extracted file first.')
        rel = items[0].data(0, Qt.UserRole)
        if not rel:
            raise ValueError('Choose a file, not a directory.')
        return rel

    def _dirty(self):
        return (self.mode == 'files' and self.current and
                self.views['files'].toPlainText() != self.loaded_text)

    def _confirm_buffer(self):
        return not self._dirty() or QMessageBox.question(
            self, 'Unsaved changes', 'Discard unsaved text edits?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def load_selection(self):
        if not self._confirm_buffer():
            return
        try:
            chosen = self.selected()
            if chosen.casefold().endswith('.lyr'):
                self.manager.open_level_editor(self.workspace)
            else:
                self.load(chosen)
        except ValueError as exc:
            self.status.setText(str(exc))

    def load(self, rel):
        if rel not in self.records:
            raise ValueError('This file is no longer in the extracted PAK.')
        # The internal pages are selected by file type; their tab bar is hidden.
        target_mode = self._category(rel)
        self.tabs.setCurrentIndex(self.TABS.index(target_mode))
        self.current_file_label.setText(rel)
        self.current_file_label.setToolTip(rel)
        if rel != self.current:
            self._capture_playback_preferences()
        if rel != self.current:
            self.character_skin_choice = None
        self.current = rel
        self.stream = None
        self.mesh = None
        self.materials = None
        self.character_preview = None
        self.texture_message = 'No texture lookup has run'
        self.mode = self._category(rel)
        self.dependency_button.setEnabled(self.mode=='models')
        if self.mode != 'models':
            self.dependency_button.setText('Dependencies…')
        self._model_generation += 1
        path = checked_path(self.workspace, rel)
        if self.mode == 'models':
            self._load_model_background(rel)
            return
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
            self._load_recovery()
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

    def _load_model_background(self, rel):
        """Load heavy CryEngine files and textures away from the GUI thread."""
        self._model_prepared = False
        self._model_started_at = time.perf_counter()
        view = self.views['models']
        self.model_fallback.setPixmap(QPixmap())
        self.model_fallback.setText('Loading character and textures…')
        view.setCurrentWidget(self.model_fallback)
        self.status.setText(rel + ' | Preparing GPU model…')
        source_workspace = self.workspace
        index = self.preview_index
        projects = self.manager.projects_path
        stage = self.manager.stage_path
        # Character-specific animation CAFs may live in their own signed PAK,
        # separate from the six shared texture/skeleton packages.
        try:
            from shared_pak_cache import start_character_catalog
            start_character_catalog(stage, rel)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText('Character index unavailable: ' + str(exc)[:120])
        tex = self.texture_folder
        ani = self.loose_animation_index
        equipment = self.character_skin_choice
        token = self._model_generation

        def resolve(_unused):
            data,cache_hit,load_ms = prepare_model_cached(source_workspace, rel,
                projects_root=projects, stage_root=stage,
                texture_folder=tex, animation_index=ani,
                equipment=equipment, index=index)
            return (data, cache_hit, load_ms), None

        # Tests and headless software previews retain deterministic synchronous
        # behavior. Real GPU viewing runs all expensive preparation in a worker.
        platform = os.environ.get('QT_QPA_PLATFORM','').casefold()
        if platform in ('offscreen','minimal') or not self.isVisible():
            try:
                self._model_ready(token, resolve(None), None)
            except (ValueError, OSError) as exc:
                self._model_ready(token, None, str(exc))
            return
        from qt_gpu_viewer import _NativeConversion, _NativeResult
        from PySide6.QtCore import QThreadPool
        if self._model_pool is None:
            self._model_pool = QThreadPool(self)
            self._model_pool.setMaxThreadCount(2)
            self._model_signals = _NativeResult(self)
            self._model_signals.completed.connect(self._model_ready)
        self._model_pool.start(
            _NativeConversion(resolve, None, token, self._model_signals))

    def _model_ready(self, token, result, error):
        if token != self._model_generation or self.mode != 'models':
            return
        view = self.views['models']
        if error:
            self.model_fallback.setPixmap(QPixmap())
            self.model_fallback.setText(self.current + '\\n\\n3D preview unavailable: ' + error)
            view.setCurrentWidget(self.model_fallback)
            self.status.setText('3D preview unavailable: ' + error[:260])
            return
        data, cache_hit, load_ms = result[0]
        self.model_cache_hit = cache_hit
        self.model_load_ms = round(load_ms, 2)
        self.raw = data['raw']
        self.mesh = data['mesh']
        self.materials = data['materials']
        self.preview_index = data['index']
        self.character_preview = data['character']
        self._prepared_geometry = data['geometry']
        self._prepared_animation = data['animation']
        self._materials_error = data['materials_error']
        self._model_prepared = True
        refs=data['animation'][0]
        mapped=(self.materials.count if self.materials is not None else 0)
        slots=(len(self.materials.textures) if self.materials is not None else 0)
        count=sum(getattr(ref,'available',False) for ref in refs)
        total=sum(getattr(ref,'kind',None)=='caf' for ref in refs)
        self.dependency_button.setText(
            'Assets: %d/%d textures · %d/%d CAF clips' %
            (mapped, slots, count, total))
        issues=(self.materials.issues if self.materials is not None else ())
        self.dependency_button.setToolTip(
            'CAF mappings: %d • extracted: %d\n'
            'Texture diagnostic issues:\n%s\n'
            'Open Assets for full texture/animation diagnostics.' %
            (total, count, '\n'.join(issues[:8]) or
             'No unresolved DDS references reported.'))
        self._update_equipment_menu()
        self._paint_model(view, self.mesh)
        self.status.setText(
            self.current + ' | ' + data['report'] + ' | ' + self.texture_message)

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
            materials = self.materials if self._model_prepared else (
                self.character_preview.materials if self.character_preview else None)
            try:
                if materials is None and self._model_prepared:
                    raise ValueError(self._materials_error or 'No model diffuse textures available')
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
                self.model_panel = QtGPUModelPanel(view, mesh, materials, compact=True,
                                                  prepared_geometry=self._prepared_geometry)
                self.model_panel.setObjectName('primaryGpuModelViewer')
                view.addWidget(self.model_panel)
                self.model_gpu = self.model_panel.canvas
                self.model_gpu.setObjectName('defaultGpuModelPreview')
            else:
                self.model_panel.set_static_mesh(mesh, materials,
                                                 prepared_geometry=self._prepared_geometry)
            view.setCurrentWidget(self.model_panel)
            camera=getattr(self,'_restored_camera',None)
            if isinstance(camera,dict):
                for name in ('rotation','pan'):
                    if name in camera:
                        setattr(self.model_gpu,name,list(camera[name]))
                if 'zoom' in camera:
                    self.model_gpu.zoom=camera['zoom']
                self.model_gpu.update()
                self._restored_camera=None
            from character_animation_catalog import discover_animations
            anim_model = (self.character_preview.skeleton if self.character_preview
                          else self.current)
            refs, idle, config = (
                self._prepared_animation if self._prepared_animation is not None
                else discover_animations(
                    anim_model, self.preview_index,
                    preferred_workspace=self.workspace,
                    loose_index=self.loose_animation_index))
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
            source_game = self.manager.game_root or self.manager._find_game_root()
            source_loose = self.loose_animation_index

            def build_native_clip(ref):
                # QThreadPool worker: verify/index archive metadata and decode
                # ONLY this CAF when it is first requested by the user.
                from animation_on_demand import resolve_caf_for_play
                selected, extracted, origin = resolve_caf_for_play(
                    ref, stage_root=source_stage, game_root=source_game,
                    asset_index=source_index,
                    preferred_workspace=source_workspace,
                    loose_index=source_loose)
                blob, details = create_character_glb(
                    source_workspace, source_relative, source_index,
                    character=source_character, texture_folder=source_textures,
                    projects_root=source_projects, stage_root=source_stage,
                    clip=selected)
                from model_animation import AnimatedGLB
                from gpu_model_preview import prepare_geometry
                imported = AnimatedGLB(blob,'Original Evolve animation')
                details['_resolved_caf_reference'] = selected
                details['_extracted_on_play'] = extracted
                details['_animation_asset_origin'] = origin
                return (imported, prepare_geometry(imported.preview_mesh)), details

            self.model_panel.set_native_catalog(refs, idle, config,
                                                loader=build_native_clip)
            prefs=self.manager.settings.get('animation_playback_preferences',{})
            key=project_session_key(self.workspace)+'|'+self.current.casefold()
            if isinstance(prefs,dict) and isinstance(prefs.get(key),dict):
                self.model_panel.restore_playback_preferences(prefs[key])
            # Keep the viewport readable; detailed mesh/texture diagnostics
            # remain available on hover rather than taking up viewport rows.
            self.model_panel._info.setText('User Perspective  •  ' + mesh.source_name)
            self.model_panel._info.setToolTip(
                mesh.description + '\n' + self.texture_message[:1000])
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
        self._save_project_session()
        target = self.archive_map[name]
        new_records = self._read_records(target)
        self.dependency_button.setEnabled(False)
        self.workspace, self.records = target, new_records
        self.favorites_only.setChecked(False)
        self.preview_index = None
        self.character_preview = None
        self.character_skin_choice = None
        self.current, self.loaded_text, self.raw = None, '', b''
        self.manager.current_workspace = str(target)
        self.manager.archive_label = name
        self.manager.output_pak = ''
        self.manager.persist()
        self.populate()
        self.current_file_label.setText('Select a file from ' + name)
        self.tabs.setCurrentIndex(0)
        self.views['files'].clear()
        QTimer.singleShot(0,self._restore_project_session)
        self.status.setText('Browsing ' + name + ' | Build Mod applies only to this PAK.')

    def change_tab(self, index):
        if self._dirty():
            # QTabWidget emits after a change. Revert if user keeps edits.
            if not self._confirm_buffer():
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(0)
                self.tabs.blockSignals(False)
                return
        self._capture_playback_preferences()
        self._model_generation += 1
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
            from workflow_lab import clear_recovery
            clear_recovery(self.workspace,self.current)
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
            clear_model_cache(self.workspace)
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
        tree = self.file_tree
        item = tree.itemAt(position)
        if item is None or not item.data(0, Qt.UserRole):
            return
        tree.setCurrentItem(item)
        section = self._category(item.data(0, Qt.UserRole))
        menu = QMenu(tree)
        menu.addAction('Preview / Open', self.load_selection)
        menu.addAction('Toggle Favorite ★', self.toggle_favorite)
        menu.addAction('Project History & Undo…', self.open_project_history)
        menu.addAction('Import Over / Replace', self.import_raw)
        menu.addAction('Restore Original Extraction', self.restore_original)
        menu.addAction('Export Raw File', self.export_raw)
        if section=='files' and self.current and self.current.lower().endswith('.xml'):
            menu.addAction('Visual Gameplay Values…', self.open_gameplay_editor)
        if section=='images':
            menu.addAction('Advanced Texture Adjustments…', self.open_texture_editor)
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

    def export_animated_character(self):
        """Export native CAF clips with a verified character rig, read-only."""
        if self.mode != 'models' or not self.mesh or not self.current:
            QMessageBox.information(self, 'Animated Blender export',
                                    'Open a supported character model first.')
            return
        from character_animation_catalog import discover_animations
        from native_character_glb import create_character_glb
        from qt_gpu_viewer import _NativeConversion, _NativeResult
        from PySide6.QtCore import QThreadPool
        skeleton = (self.character_preview.skeleton if self.character_preview
                    else self.current)
        refs, _, source = discover_animations(
            skeleton, self.preview_index, preferred_workspace=self.workspace,
            loose_index=self.loose_animation_index)
        clips = tuple(r for r in refs if r.available and r.kind == 'caf'
                      and 'additive' not in r.name.casefold()
                      and 'aimpose' not in r.name.casefold())
        if not clips:
            QMessageBox.information(self, 'Animated Blender export',
                                    'No compatible extracted CAF clips found for this skeleton.')
            return
        target, _ = QFileDialog.getSaveFileName(
            self, 'Export complete character with native animations',
            str(Path(self.manager.projects_path)/
                (Path(self.current).stem + '-animated.glb')),
            'Blender animated glTF 2.0 (*.glb)')
        if not target:
            return
        target = Path(target).resolve().with_suffix('.glb')
        protected = (self.workspace/'files').resolve()
        if target.is_relative_to(protected):
            QMessageBox.warning(self,'Animated Blender export',
                                'Choose an export location outside extracted game assets.')
            return
        if len(clips) > 256:
            QMessageBox.warning(self,'Animated Blender export',
                                'More than 256 matching animations; refine the selection.')
            return
        if self._export_native_pool is None:
            self._export_native_pool = QThreadPool(self)
            self._export_native_pool.setMaxThreadCount(1)
            self._export_native_signals = _NativeResult(self)
            self._export_native_signals.completed.connect(self._export_animated_ready)
        workspace, relative = self.workspace, self.current
        index, character = self.preview_index, self.character_preview
        tex = self.texture_folder
        projects, stage = self.manager.projects_path, self.manager.stage_path
        self._export_native_target = target
        self._export_native_token = getattr(self,'_export_native_token',0)+1
        token = self._export_native_token

        def produce(_reference):
            payload, report = create_character_glb(
                workspace, relative, index, character=character,
                texture_folder=tex, projects_root=projects,
                stage_root=stage, clips=clips)
            import os
            import tempfile
            # Atomic export: never leave a half-written GLB on failure.
            fd, temporary = tempfile.mkstemp(
                prefix='.evolve-animation-',suffix='.glb',dir=target.parent)
            try:
                with os.fdopen(fd,'wb') as output:
                    output.write(payload)
                os.replace(temporary,target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return b'', report

        self.status.setText('Exporting %d original CryEngine animations…' % len(clips))
        self._export_native_pool.start(
            _NativeConversion(produce,None,token,self._export_native_signals))

    def _export_animated_ready(self, token, result, error):
        if token != getattr(self,'_export_native_token',None):
            return
        if error:
            self.status.setText('Animated Blender export failed: ' + error[:260])
            QMessageBox.warning(self,'Animated Blender export',error)
            return
        _, report = result
        self.status.setText(
            'Exported %d animated clips into %s' %
            (report.get('animations',0),self._export_native_target))

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
                    blob, details = create_character_glb(
                        source_workspace, source_relative, source_index,
                        character=source_character, texture_folder=source_textures,
                        projects_root=source_projects, stage_root=source_stage, clip=ref)
                    from model_animation import AnimatedGLB
                    from gpu_model_preview import prepare_geometry
                    imported = AnimatedGLB(blob,'Original Evolve animation')
                    return (imported, prepare_geometry(imported.preview_mesh)), details
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
        self._save_project_session()
        self._model_generation += 1
        if self.model_panel is not None and not self.model_panel.close():
            event.ignore()
            return
        super().closeEvent(event)
