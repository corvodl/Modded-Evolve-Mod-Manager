"""Read-only Qt browser for files already extracted into the Asset Library."""
from __future__ import annotations

from pathlib import Path
import os

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QLineEdit, QLabel, QPushButton, QPlainTextEdit,
    QStackedWidget, QFileDialog, QMessageBox, QComboBox, QMenuBar,
)

from asset_library_preview import (
    archive_folder, archive_entries, read_asset, texture_image,
    library_model_index, export_file, export_png, export_glb,
)


class _PreviewSignals(QObject):
    loaded = Signal(int, object, object)
    exported = Signal(int, object, object)


class _PreviewWork(QRunnable):
    def __init__(self, token, fn, signal):
        super().__init__()
        self.token, self.fn, self.signal = token, fn, signal

    def run(self):
        try:
            self.signal.emit(self.token, self.fn(), None)
        except Exception as exc:
            self.signal.emit(self.token, None, str(exc))


class QtAssetPreviewWindow(QMainWindow):
    """Browse originals; exported files are copies, not editable game projects."""

    def __init__(self, manager, archive):
        super().__init__(manager)
        self.manager = manager
        self.identifier = archive['id']
        self.name = archive['name']
        self.folder = archive_folder(self.identifier)
        self.entries = archive_entries(self.identifier)
        self.current = None
        self.model_panel = None
        self._serial = 0
        self._export_serial = 0
        self._signals = _PreviewSignals(self)
        self._signals.loaded.connect(self._loaded)
        self._signals.exported.connect(self._export_finished)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

        self.setWindowTitle('Asset Library — ' + self.name + ' (Read only)')
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1200, 770)
        self.setMinimumSize(700, 490)
        self.setStyleSheet(manager.styleSheet())

        outer = QWidget(self)
        self.setCentralWidget(outer)
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)
        menu = QMenuBar(self)
        menu.setNativeMenuBar(False)
        file_menu = menu.addMenu('File')
        file_menu.addAction('Export Original File…', self.export_original)
        file_menu.addAction('Export Complete PAK Layout…',
                            lambda: self.manager.export_library_archive_by_id(self.identifier))
        export_menu = menu.addMenu('Export')
        export_menu.addAction('Texture as PNG…', self.export_texture_png)
        export_menu.addAction('Model for Blender (.glb)…', self.export_model_glb)
        view_menu = menu.addMenu('View')
        view_menu.addAction('Refresh Extracted Files', self.refresh)
        layout.setMenuBar(menu)

        note = QLabel('Asset Library is for previewing and exporting only — no editing or mod building. '
                      'To modify a file, create an editable project from Modding.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(3, 3, 3, 3)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search extracted files…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.populate)
        left_lay.addWidget(self.search)
        self.type_filter = QComboBox()
        self.type_filter.addItems(('All files', 'Models', 'Textures', 'Animations', 'Other'))
        self.type_filter.currentIndexChanged.connect(self.populate)
        left_lay.addWidget(self.type_filter)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(('File / Folder', 'Size'))
        self.tree.setColumnWidth(0, 270)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._selected)
        left_lay.addWidget(self.tree, 1)
        self.count = QLabel()
        left_lay.addWidget(self.count)
        splitter.addWidget(left)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(3, 3, 3, 3)
        self.location = QLabel('Select an extracted file to preview')
        self.location.setWordWrap(True)
        right_lay.addWidget(self.location)
        self.stack = QStackedWidget()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.stack.addWidget(self.text)
        self.image = QLabel('Select an extracted DDS texture')
        self.image.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.image)
        self.model_holder = QWidget()
        self.model_lay = QVBoxLayout(self.model_holder)
        self.model_lay.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.model_holder)
        right_lay.addWidget(self.stack, 1)
        buttons = QHBoxLayout()
        for label, callback in (
            ('Export Original…', self.export_original),
            ('Export PNG…', self.export_texture_png),
            ('Export Blender GLB…', self.export_model_glb)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        right_lay.addLayout(buttons)
        self.status = QLabel('Read-only reference files; originals remain unchanged.')
        self.status.setWordWrap(True)
        right_lay.addWidget(self.status)
        splitter.addWidget(right)
        splitter.setSizes([410, 790])
        layout.addWidget(splitter, 1)
        self.populate()

    @staticmethod
    def _kind(relative):
        name = relative.casefold()
        if name.endswith(('.skin', '.skinm', '.chrm', '.chr', '.cgfm', '.cgf',
                          '.cga', '.cgam', '.cdf', '.chrparams')):
            return 1
        if name.endswith('.dds') or '.dds.' in name:
            return 2
        if name.endswith(('.caf', '.dba', '.bspace', '.comb', '.i_caf')):
            return 3
        return 4

    def refresh(self):
        try:
            self.entries = archive_entries(self.identifier)
            self.populate()
        except (OSError, ValueError) as exc:
            self.status.setText('Library refresh failed: ' + str(exc))

    def populate(self, *_args):
        search = self.search.text().strip().casefold()
        kind = self.type_filter.currentIndex()
        matches = [path for path in sorted(self.entries, key=str.casefold)
                   if (not search or search in path.casefold())
                   and (kind == 0 or self._kind(path) == kind)]
        # A full game import can contain hundreds of thousands of entries.
        # Bound Qt objects while keeping search over the complete inventory.
        visible = matches[:2500]
        self.tree.blockSignals(True)
        self.tree.clear()
        folders = {}
        for relative in visible:
            parent = None
            parts = relative.split('/')
            for i, piece in enumerate(parts[:-1]):
                key = '/'.join(parts[:i+1])
                item = folders.get(key)
                if item is None:
                    item = QTreeWidgetItem([piece, ''])
                    if parent is not None:
                        parent.addChild(item)
                    else:
                        self.tree.addTopLevelItem(item)
                    folders[key] = item
                parent = item
            size = self.entries[relative].stat().st_size
            label = ('%.1f MiB' % (size / 1048576) if size >= 1048576
                     else '%.0f KiB' % (size / 1024))
            node = QTreeWidgetItem([parts[-1], label])
            node.setData(0, Qt.UserRole, relative)
            node.setToolTip(0, relative)
            if parent is None:
                self.tree.addTopLevelItem(node)
            else:
                parent.addChild(node)
        self.tree.blockSignals(False)
        if search:
            self.tree.expandAll()
        self.count.setText(
            '%d of %d matching files shown%s' %
            (len(visible), len(matches),
             ' — search to narrow results' if len(visible) < len(matches) else ''))

    def _selected(self):
        node = self.tree.currentItem()
        relative = node.data(0, Qt.UserRole) if node else None
        if relative:
            self.preview(relative)

    def preview(self, relative):
        if relative not in self.entries:
            self.status.setText('That asset is not in this imported PAK.')
            return
        self.current = relative
        self._serial += 1
        token = self._serial
        self.location.setText(self.name + '  /  ' + relative)
        self.image.setText('Preparing texture preview…')
        self.text.setPlainText('Loading read-only asset preview…')
        self.stack.setCurrentWidget(self.text)
        self.status.setText('Loading ' + relative)
        kind = self._kind(relative)
        if kind == 2 and relative.casefold().endswith(('.dds', '.dds.0')):
            self._pool.start(_PreviewWork(
                token, lambda: ('image', texture_image(self.identifier, relative)),
                self._signals.loaded))
        elif relative.casefold().endswith(('.skinm', '.chrm', '.cgfm', '.cgam')):
            def load_model():
                from async_model_resolve import resolve_model
                index = library_model_index(self.identifier)
                data = resolve_model(self.folder, relative, index=index)
                return ('model', data)
            self._pool.start(_PreviewWork(token, load_model, self._signals.loaded))
        else:
            try:
                file = self.entries[relative]
                binary = (self._kind(relative) == 3 and
                          relative.casefold().endswith(('.caf', '.dba')))
                if binary or file.stat().st_size > 2 * 1024 * 1024:
                    explanation = ('Native animation tracks require a compatible skeleton/DBA decoder.'
                                   if binary else 'File is too large for text preview.')
                    self._loaded(token, ('text',
                        explanation + '\n\nVirtual path: ' + relative +
                        '\nBytes: ' + str(file.stat().st_size)), None)
                else:
                    raw = read_asset(self.identifier, relative, limit=2 * 1024 * 1024)
                    if b'\0' in raw[:4096]:
                        label = 'Binary asset; export the original to inspect it in compatible tools.'
                    else:
                        try:
                            label = raw.decode('utf-8-sig')
                        except UnicodeDecodeError:
                            label = 'Unsupported text encoding; export the original bytes.'
                    self._loaded(token, ('text', label), None)
            except (OSError, ValueError) as exc:
                self._loaded(token, None, str(exc))

    def _loaded(self, token, value, error):
        if token != self._serial:
            return
        if error:
            self.stack.setCurrentWidget(self.text)
            self.text.setPlainText('Preview unavailable: ' + str(error))
            self.status.setText('Original extracted file may still be exported.')
            return
        kind, result = value
        if kind == 'image':
            from io import BytesIO
            data = BytesIO()
            result.convert('RGBA').save(data, 'PNG')
            pixmap = QPixmap()
            pixmap.loadFromData(data.getvalue(), 'PNG')
            self.image.setPixmap(pixmap.scaled(
                950, 650, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.stack.setCurrentWidget(self.image)
            self.status.setText('Read-only DDS preview • %d × %d' % result.size)
        elif kind == 'model':
            mesh = result['mesh']
            materials = result['materials']
            if self.model_panel:
                self.model_lay.removeWidget(self.model_panel)
                self.model_panel.hide()
                self.model_panel.deleteLater()
                self.model_panel = None
            if os.environ.get('QT_QPA_PLATFORM', '').casefold() in ('offscreen', 'minimal'):
                fallback = QLabel(mesh.description + '\nGPU preview requires a desktop OpenGL context.')
                fallback.setAlignment(Qt.AlignCenter)
                self.model_lay.addWidget(fallback)
                self.model_panel = fallback
            else:
                from qt_gpu_viewer import QtGPUModelPanel
                panel = QtGPUModelPanel(self.model_holder, mesh, materials, compact=True,
                                       prepared_geometry=result['geometry'])
                panel.setObjectName('assetLibraryGpuPreview')
                refs, idle, config = result['animation']
                panel.set_native_catalog(refs, idle, config)
                self.model_lay.addWidget(panel)
                self.model_panel = panel
            self.stack.setCurrentWidget(self.model_holder)
            loaded = materials.count if materials is not None else 0
            self.status.setText('Read-only 3D preview • %s • %d decoded textures' %
                                (mesh.description, loaded))
        else:
            self.text.setPlainText(result)
            self.stack.setCurrentWidget(self.text)
            self.status.setText('Read-only file preview')

    def _export(self, category):
        relative = self.current
        if not relative or relative not in self.entries:
            QMessageBox.information(self, 'Export asset', 'Select an extracted file first.')
            return
        if category == 'png' and not relative.casefold().endswith(('.dds', '.dds.0')):
            QMessageBox.information(self, 'Export PNG', 'Choose a DDS or .dds.0 texture.')
            return
        if category == 'glb' and not relative.casefold().endswith(('.skinm', '.chrm', '.cgfm', '.cgam')):
            QMessageBox.information(self, 'Export GLB', 'Choose a supported CryEngine render mesh.')
            return
        extension = ('.png' if category == 'png' else '.glb' if category == 'glb'
                     else '')
        base = Path(relative).name
        if base.casefold().endswith('.dds.0'):
            base = base[:-2]
        filename = Path(base).stem + extension if extension else base
        name, _ = QFileDialog.getSaveFileName(
            self, 'Export read-only library copy', str(Path.home() / filename))
        if not name:
            return
        target = Path(name)
        if extension and target.suffix.casefold() != extension:
            target = target.with_suffix(extension)
        if target.exists():
            if QMessageBox.question(
                    self, 'Replace exported file?',
                    'Overwrite this external exported copy?\n' + str(target),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        identifier = self.identifier
        self._export_serial += 1
        token = self._export_serial
        def work():
            if category == 'png':
                return export_png(identifier, relative, target)
            if category == 'glb':
                return export_glb(identifier, relative, target)
            return export_file(identifier, relative, target)
        self.status.setText('Exporting a copy to ' + str(target))
        self._pool.start(_PreviewWork(token, work, self._signals.exported))

    def _export_finished(self, token, value, error):
        if token != self._export_serial:
            return
        if error:
            self.status.setText('Export failed: ' + str(error)[:250])
            QMessageBox.warning(self, 'Export failed', str(error))
        else:
            path = value[0] if isinstance(value, tuple) else value
            self.status.setText('Exported copy: ' + str(path))
            QMessageBox.information(self, 'Export complete', 'Saved to:\n' + str(path))

    def export_original(self):
        self._export('original')

    def export_texture_png(self):
        self._export('png')

    def export_model_glb(self):
        self._export('glb')

    def closeEvent(self, event):
        self._serial += 1
        self._export_serial += 1
        # Avoid freezing the main Qt thread waiting for a long model or
        # export operation. The user can close the viewer after it completes.
        if self._pool.activeThreadCount():
            self.status.setText('Finish the active preview/export before closing.')
            event.ignore()
            return
        super().closeEvent(event)
