"""Asset Library viewer: the exact Modding editor, with read-only controls.

There is deliberately one model/animation viewport implementation. Asset
Library references are fully extracted game files, never mod project copies.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMessageBox, QTreeWidgetItemIterator,
)

from qt_workspace_editor import QtWorkspaceEditor
from asset_library_preview import (
    archive_folder, archive_entries, library_model_index, export_file,
    export_png as export_library_png, export_glb,
)


class QtAssetPreviewWindow(QtWorkspaceEditor):
    """Identical GPU/file explorer to Modding, minus edits and imports."""

    def __init__(self, manager, archive):
        self.identifier = archive['id']
        self.name = archive['name']
        folder = archive_folder(self.identifier)
        super().__init__(manager, folder, library_archive=archive)
        self.folder = self.workspace
        self.entries = archive_entries(self.identifier)
        # Aliases maintain compatibility with the existing viewer smoke test.
        self.tree = self.file_tree
        self.text = self.views['files']
        self.setWindowTitle('Asset Library — ' + self.name + ' — Read Only')
        note = QLabel(
            'Asset Library is for previewing and exporting only — editing '
            'and importing are disabled. All original assets are protected.')
        note.setWordWrap(True)
        note.setObjectName('libraryReadOnlyNotice')
        self.centralWidget().layout().insertWidget(0, note)
        self.status.setText('Asset Library: preview and export only. No editing or importing.')
        self.editor_menus['File'].addAction(
            'Open Asset Library Folder', self.explore)

    def switch_archive(self, name):
        """Allow the exact same top PAK picker to browse imported archives."""
        if not self.read_only_library:
            return super().switch_archive(name)
        candidate = self.archive_map.get(name)
        if candidate is None:
            return
        archive_id = Path(candidate).name
        if archive_id == self.identifier:
            return
        try:
            files = archive_entries(archive_id)
            index = library_model_index(archive_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Asset Library', str(error))
            return
        self._model_generation += 1
        self.identifier = archive_id
        self.workspace = Path(candidate).resolve()
        self.folder = self.workspace
        self.name = name
        self.entries = files
        self.records = {rel: {'path': rel, 'format': 'raw'} for rel in files}
        self.preview_index = index
        self.current = None
        self.mode = None
        self.raw = b''
        self.character_preview = None
        self.character_skin_choice = None
        self.materials = None
        self.mesh = None
        self._prepared_animation = None
        if self.model_panel is not None:
            self.views['models'].removeWidget(self.model_panel)
            self.model_panel.hide()
            self.model_panel.deleteLater()
            self.model_panel = None
            self.model_gpu = None
        self.views['files'].clear()
        self.tabs.setCurrentIndex(0)
        self.search.clear()
        self.populate()
        self.current_file_label.setText('Choose an extracted file from ' + name)
        self.setWindowTitle('Asset Library — ' + name + ' — Read Only')
        self.status.setText('Asset Library: read-only PAK switched; no files changed.')

    def preview(self, relative):
        self.load(relative)

    def refresh(self):
        """Refresh only approved library entries; never extract or import."""
        self.entries = archive_entries(self.identifier)
        self.records = {rel: {'path': rel, 'format': 'raw'}
                        for rel in self.entries}
        self.preview_index = library_model_index(self.identifier)
        self.populate()
        if self.current and self.current in self.records:
            self.load(self.current)

    def export_raw(self):
        try:
            rel = self.selected()
            output, _ = QFileDialog.getSaveFileName(
                self, 'Export original read-only asset', Path(rel).name)
            if not output:
                return
            target = export_file(self.identifier, rel, output)
            self.status.setText('Exported original file: ' + str(target))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Asset export', str(error))

    def export_png(self):
        if self.mode != 'images' or not self.current:
            return
        stem = Path(self.current).name.split('.dds')[0]
        output, _ = QFileDialog.getSaveFileName(
            self, 'Export decoded DDS texture', stem + '.png', 'PNG (*.png)')
        if not output:
            return
        try:
            target = Path(output)
            if target.suffix.casefold() != '.png':
                target = target.with_suffix('.png')
            export_library_png(self.identifier, self.current, target)
            self.status.setText('Exported texture copy: ' + str(target))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Texture export', str(error))

    def export_blender_model(self):
        if self.mode != 'models' or not self.current:
            QMessageBox.information(
                self, 'Blender export', 'Select a supported model first.')
            return
        output, _ = QFileDialog.getSaveFileName(
            self, 'Export read-only model for Blender',
            Path(self.current).stem + '.glb', 'Blender glTF (.glb)')
        if not output:
            return
        try:
            target = Path(output)
            if target.suffix.casefold() != '.glb':
                target = target.with_suffix('.glb')
            target, details = export_glb(self.identifier, self.current, target)
            self.status.setText(
                'Blender GLB exported: %d textures; rig: %s | %s' %
                (details['textures'], details['rig'], target))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Blender export', str(error))

    def export_blender_folder(self):
        if self.mode != 'models' or not self.current:
            QMessageBox.information(
                self, 'Blender export', 'Select a model from File Explorer first.')
            return
        output = QFileDialog.getExistingDirectory(
            self, 'Export up to 100 compatible models, preserving virtual paths')
        if not output:
            return
        prefix = str(Path(self.current).parent.as_posix()).casefold() + '/'
        models = [rel for rel in self.entries if rel.casefold().startswith(prefix)
                  and rel.casefold().endswith(('.skinm', '.chrm', '.cgfm', '.cgam'))]
        if len(models) > 100:
            QMessageBox.information(
                self, 'Blender export', 'Narrow the file selection to 100 models or fewer.')
            return
        from asset_library_preview import _outside_library
        try:
            root = _outside_library(output)
            converted = 0
            failed = []
            for rel in models:
                destination = root / Path(rel).with_suffix('.glb')
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    export_glb(self.identifier, rel, destination)
                    converted += 1
                except (OSError, ValueError) as error:
                    failed.append(rel + ': ' + str(error)[:70])
            self.status.setText(
                'Exported %d Blender model(s); %d unsupported | %s' %
                (converted, len(failed), output))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Blender folder export', str(error))

    def export_model(self):
        self.export_raw()

    def closeEvent(self, event):
        # The Qt editor prepares models in worker threads. Avoid discarding an
        # active worker's signals while its source window is being destroyed.
        pool = self._model_pool
        if pool is not None and pool.activeThreadCount():
            self.status.setText('Please wait for model preparation to finish.')
            event.ignore()
            return
        super().closeEvent(event)
