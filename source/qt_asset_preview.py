"""Asset Library viewer: the exact Modding editor, with read-only controls.

There is deliberately one model/animation viewport implementation. Asset
Library references are fully extracted game files, never mod project copies.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMessageBox, QTreeWidgetItemIterator,
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
        self.build_id = archive.get('build_id')
        folder = archive_folder(self.identifier)
        super().__init__(manager, folder, library_archive=archive)
        self.folder = self.workspace
        self.entries = archive_entries(self.identifier)
        # Aliases maintain compatibility with the existing viewer smoke test.
        self.tree = self.file_tree
        self.text = self.views['files']
        self.setWindowTitle('Asset Library — ' + self.name + ' — Read Only')
        # Retain one compact breadcrumb row; the old two notice banners
        # reduced the model viewport unnecessarily on narrower windows.
        self.selection_label = QLabel('Read-only · 0 selected')
        self.selection_label.setObjectName('libraryReadOnlyNotice')
        self.selection_label.setMaximumHeight(22)
        self._selection_protection_tooltip = (
            'Asset Library originals are protected: preview and export only. '
            'Editing and importing are disabled. File > Edit / Add to Mod '
            'Project makes a separate editable copy; originals never change.')
        self.selection_label.setToolTip(self._selection_protection_tooltip)
        self.selection_label.setAccessibleName(
            'Asset Library read-only selection status')
        self.workspace_header.insertWidget(1, self.selection_label)
        self.status.setText('Asset Library: preview and export only. No editing or importing.')
        self.editor_menus['File'].addAction(
            'Open Asset Library Folder', self.explore)
        self.editor_menus['File'].addAction('Edit / Add to Mod Project…', self.edit_in_project)
        self.editor_menus['File'].addAction('Export Selected Files…', self.export_selection)
        self.editor_menus['File'].addAction('Open Entire Map…', self.edit_entire_map)
        # Selection updates only the existing header, never a separate row.
        self.file_tree.itemSelectionChanged.connect(self._report_selection)
        self.virtual_file_tree.selectionModel().selectionChanged.connect(
            lambda *_: self._report_selection())

    def selected_virtual_files(self, *, limit=500):
        """Return deduplicated file identities under selected file/folder nodes."""
        from evolve_pak_workspace import normalized_name
        chosen = set()
        for node in self.file_tree.selectedItems():
            rel = node.data(0, Qt.UserRole)
            folder = node.data(0, Qt.UserRole + 1)
            if isinstance(rel, str) and rel in self.entries:
                chosen.add(rel)
            elif isinstance(folder, str):
                prefix = normalized_name(folder).rstrip('/') + '/'
                chosen.update(path for path in self.entries if path.startswith(prefix))
            if len(chosen) > limit:
                raise ValueError("At most %d files may be selected at once." % limit)
        if not chosen and self.current in self.entries:
            chosen.add(self.current)
        return tuple(sorted(chosen, key=str.casefold))

    def _report_selection(self):
        try:
            count = len(self.selected_virtual_files())
            self.selection_label.setText('Read-only · %d selected' % count)
            self.selection_label.setToolTip(self._selection_protection_tooltip)
        except ValueError as exc:
            self.selection_label.setText('Selection limit')
            self.selection_label.setToolTip(str(exc))

    def edit_in_project(self):
        try:
            selected = self.selected_virtual_files()
            if not selected:
                raise ValueError("Select a file to edit.")
            self.manager._pending_library_virtual = selected[0]
            self.manager.create_mod_project_from_asset_library(
                self.identifier, source_build_id=self.build_id)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Edit project", str(exc))

    def edit_entire_map(self):
        from pak_dependencies import is_level_pak
        if not is_level_pak(self.name):
            QMessageBox.information(self, "Open map",
                "Choose a supported level PAK for editable map projects.")
            return
        self.manager.open_library_map_from_archive(
            self.identifier, build_id=self.build_id)

    def export_selection(self):
        from asset_library_preview import _outside_library, export_file
        from qt_background_exports import begin_file_operation, exports_busy
        if exports_busy(self):
            QMessageBox.information(self, "Export busy", "Finish the existing export first.")
            return
        try:
            selected = self.selected_virtual_files()
            if not selected:
                raise ValueError("Select one or more files.")
        except ValueError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Export selected original assets", str(Path.home()))
        if not folder:
            return
        try:
            parent = _outside_library(folder)
            if not parent.is_dir():
                raise ValueError("Choose an existing destination folder.")
            output = parent / ("SelectedAssets-" + self.identifier[:8])
            if output.exists():
                raise FileExistsError("Selected assets export folder already exists.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        archive_id = self.identifier

        def work(progress, cancelled):
            staging = parent / ("." + output.name + "-" + uuid4().hex + ".tmp")
            staging.mkdir()
            try:
                for number, virtual in enumerate(selected, 1):
                    if cancelled():
                        raise InterruptedError("Export cancelled.")
                    target = staging.joinpath(*virtual.split('/'))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    export_file(archive_id, virtual, target)
                    progress("Exporting selected originals", number, len(selected))
                if cancelled():
                    raise InterruptedError("Export cancelled.")
                os.replace(staging, output)
                return output, len(selected)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        begin_file_operation(
            self, "Export selected files", "Copying read-only originals", work,
            on_success=lambda result: self.status.setText(
                "Exported %d files to %s" % (result[1], result[0])),
            on_error=lambda error: QMessageBox.warning(self, "Export", str(error)))

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
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Asset Library', str(error))
            return
        self._model_generation += 1
        self.media_view.clear()
        self.gfx_view.cancel_pending()
        self.identifier = archive_id
        self.workspace = Path(candidate).resolve()
        self.folder = self.workspace
        self.name = name
        self.library_archive = {
            'id': archive_id, 'name': name, 'build_id': self.build_id}
        self.entries = files
        self.records = {rel: {'path': rel, 'format': 'raw'} for rel in files}
        self.preview_index = None
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
        self.selection_label.setText('Read-only · 0 selected')
        self.selection_label.setToolTip(self._selection_protection_tooltip)

    def preview(self, relative):
        self.load(relative)

    def refresh(self):
        """Refresh only approved library entries; never extract or import."""
        self.media_view.clear()
        self.entries = archive_entries(self.identifier)
        self.records = {rel: {'path': rel, 'format': 'raw'}
                        for rel in self.entries}
        self.preview_index = None
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
            from asset_library_preview import _outside_library
            from qt_operation_progress import copy_file_progress
            source = archive_entries(self.identifier).get(rel)
            if source is None:
                raise FileNotFoundError('Original reference file not found.')
            target = copy_file_progress(
                source, _outside_library(output), parent=self,
                title='Export original Asset Library file')
            self.status.setText('Exported original file: ' + str(target))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Asset export', str(error))

    def export_png(self):
        if self.mode == 'gfx':
            return super().export_png()
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
            from qt_operation_progress import run_busy
            run_busy(self, 'Export DDS as PNG', 'Decoding ' + self.current,
                     lambda: export_library_png(self.identifier, self.current, target))
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
            Path(self.current).stem + '.fbx', 'Model FBX (.fbx)')
        if not output:
            return
        from asset_library_preview import _outside_library
        from qt_background_exports import begin_file_operation, exports_busy
        if exports_busy(self):
            QMessageBox.information(
                self, 'Export already running', 'Finish the current export first.')
            return
        try:
            target = Path(output)
            if target.suffix.casefold() != '.fbx':
                target = target.with_suffix('.fbx')
            target = _outside_library(target)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Blender export', str(exc))
            return
        archive_id, virtual = self.identifier, self.current

        def work(progress, cancelled):
            progress('Converting model geometry and materials', 0, 1)
            if cancelled():
                raise InterruptedError('Export cancelled.')
            result = export_glb(archive_id, virtual, target)
            progress('Blender model exported', 1, 1)
            return result

        def finish(result):
            saved, details = result
            self.status.setText(
                'FBX exported: %d textures; rig: %s | %s' %
                (details['textures'], details['rig'], saved))

        begin_file_operation(
            self, 'Export Blender model', 'Converting ' + virtual, work,
            on_success=finish,
            on_error=lambda err: QMessageBox.warning(
                self, 'Blender export stopped', err))

    def export_blender_folder(self):
        """Use the same canonical character export as Modding/File Editor.

        The former Asset Library override wrote individual GLBs alongside
        Assemblies/ and CDF_assembly_report.json. That legacy layout is
        incompatible with the character-folder export contract. Keep one
        implementation so both menus export <Character>/<Character>.fbx,
        animations/, textures/, and models/ consistently.
        """
        return super().export_blender_folder()

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
