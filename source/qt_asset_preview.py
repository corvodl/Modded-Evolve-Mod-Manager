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
        note = QLabel(
            'Asset Library is for previewing and exporting only — editing '
            'and importing are disabled. All original assets are protected.')
        note.setWordWrap(True)
        note.setObjectName('libraryReadOnlyNotice')
        self.centralWidget().layout().insertWidget(0, note)
        self.status.setText('Asset Library: preview and export only. No editing or importing.')
        self.editor_menus['File'].addAction(
            'Open Asset Library Folder', self.explore)
        self.editor_menus['File'].addAction('Edit / Add to Mod Project…', self.edit_in_project)
        self.editor_menus['File'].addAction('Export Selected Files…', self.export_selection)
        self.editor_menus['File'].addAction('Open Entire Map…', self.edit_entire_map)
        self.selection_label = QLabel('Use Ctrl/Shift for multi-file selection.')
        self.centralWidget().layout().insertWidget(1, self.selection_label)
        self.file_tree.itemSelectionChanged.connect(self._report_selection)

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
            self.selection_label.setText(
                "%d selected • Originals read only • Edit / Add to Mod Project makes copies" % count)
        except ValueError as exc:
            self.selection_label.setText(str(exc))

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
            Path(self.current).stem + '.glb', 'Blender glTF (.glb)')
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
            if target.suffix.casefold() != '.glb':
                target = target.with_suffix('.glb')
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
                'Blender GLB exported: %d textures; rig: %s | %s' %
                (details['textures'], details['rig'], saved))

        begin_file_operation(
            self, 'Export Blender model', 'Converting ' + virtual, work,
            on_success=finish,
            on_error=lambda err: QMessageBox.warning(
                self, 'Blender export stopped', err))

    def export_blender_folder(self):
        """Export only the current virtual folder, without blocking the GUI."""
        try:
            folder = self._current_model_folder_relative()
            self._current_model_source_folder()
        except (OSError, ValueError) as error:
            QMessageBox.information(self, 'Blender export', str(error))
            return
        output = QFileDialog.getExistingDirectory(
            self, 'Export selected model folder (destination only)',
            str(Path.home()))
        if not output:
            return
        prefix = folder.casefold().rstrip('/') + '/'
        models = [rel for rel in self.entries if rel.casefold().startswith(prefix)
                  and rel.casefold().endswith(('.skinm', '.chrm', '.cgfm', '.cgam'))]
        if len(models) > 100:
            QMessageBox.information(
                self, 'Blender export', 'Narrow selection to 100 models or fewer.')
            return
        from asset_library_preview import _outside_library
        from qt_background_exports import begin_file_operation, exports_busy
        if exports_busy(self):
            QMessageBox.information(
                self, 'Export already running',
                'Finish or cancel the current export first.')
            return
        try:
            root = _outside_library(output)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Blender folder export', str(error))
            return
        archive_id = self.identifier
        models = tuple(models)

        workspace = self.workspace

        def work(progress, cancelled):
            from character_asset_export import build_cdf_folder_glbs
            from asset_library_preview import library_model_index
            import json
            converted, failed = 0, []
            export_root = root / Path(folder).name
            first_model = None
            progress('Preparing Blender export', 0, len(models))
            for number, rel in enumerate(models, 1):
                if cancelled():
                    raise InterruptedError(
                        'Cancelled after %d of %d models.' %
                        (number - 1, len(models)))
                progress('Converting ' + rel, number - 1, len(models))
                local = rel[len(folder.rstrip('/') + '/'):]
                destination = export_root / Path(local).with_suffix('.glb')
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    export_glb(archive_id, rel, destination)
                    # GLB retains embedded images for Blender's single-file
                    # import. Independent PNG copies all live at export-root/
                    # textures/, including deeply nested CDF assemblies.
                    from blender_texture_folder import write_texture_folder
                    namespace = destination.relative_to(export_root).with_suffix('').as_posix().replace('/', '__')
                    write_texture_folder(
                        destination.read_bytes(), destination,
                        texture_root=export_root / 'textures', model_namespace=namespace)
                    if first_model is None:
                        first_model = (destination, rel)
                    converted += 1
                except (OSError, ValueError) as error:
                    failed.append(rel + ': ' + str(error)[:110])
                progress('Exporting Blender models', number, len(models))
            index = library_model_index(archive_id)
            assemblies, unresolved = build_cdf_folder_glbs(
                workspace, folder, index,
                progress=progress, cancelled=cancelled)
            assembly_dest = export_root
            if assemblies or unresolved:
                for rel, (blob, details) in assemblies.items():
                    if cancelled():
                        raise InterruptedError('Cancelled during CDF assembly export.')
                    output = assembly_dest.joinpath(*Path(rel).parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with output.open('xb') as output_file:
                        output_file.write(blob)
                    from blender_texture_folder import write_texture_folder
                    namespace = output.relative_to(export_root).with_suffix('').as_posix().replace('/', '__')
                    write_texture_folder(
                        blob, output, texture_root=export_root / 'textures',
                        model_namespace=namespace)
                assembly_dest.mkdir(parents=True, exist_ok=True)
                (assembly_dest / 'CDF_assembly_report.json').write_text(
                    json.dumps({'generated': [dict(glb=rel, **info)
                                              for rel, (_, info) in assemblies.items()],
                                'unsupported': list(unresolved)}, indent=2),
                    encoding='utf-8')
            failed.extend(name + ': ' + reason for name, reason in unresolved)
            # Export one combined animation GLB for verified CAF/DBA tracks.
            # A source-only manifest is still useful if clips are unavailable.
            from character_animation_export import export_character_animation_folder
            from character_assembly import assemble_character
            animation_report = None
            preferred = next(iter(sorted(assemblies.items())), None)
            source_blob, source_ref, source_character = None, None, None
            if preferred is not None:
                _name, (source_blob, info) = preferred
                source_ref = info.get('cdf')
                if source_ref:
                    try:
                        source_character = assemble_character(
                            workspace, source_ref, index)
                    except (ValueError, OSError) as error:
                        failed.append('Animation rig discovery: ' + str(error)[:180])
            elif first_model is not None:
                source_path, source_ref = first_model
                source_blob = source_path.read_bytes()
            if source_blob is not None and source_ref is not None:
                animation_report = export_character_animation_folder(
                    export_root, source_blob, source_ref, index, workspace,
                    character=source_character, cancelled=cancelled,
                    progress=progress, model_name=Path(source_ref).stem)
            return converted, len(assemblies), failed, animation_report

        def finish(result):
            converted, assembled, failed, animations = result
            anim_count = animations['animations'] if animations else 0
            source_count = animations['sources'] if animations else 0
            self.status.setText(
                'Exported %d meshes, %d CDF assemblies, %d playable animation clips '
                'and %d source motions; %d unsupported | %s' %
                (converted, assembled, anim_count, source_count, len(failed), output))
            if failed:
                QMessageBox.information(
                    self, 'Blender export',
                    '%d individual GLBs, %d CDF assemblies, %d playable clips; '
                    '%d skipped. First issue: %s' %
                    (converted, assembled, anim_count, len(failed), failed[0]))

        def fail(error):
            self.status.setText('Blender export stopped: ' + error[:180])
            QMessageBox.warning(self, 'Blender export stopped', error)

        begin_file_operation(
            self, 'Export Asset Library models',
            'Exporting ' + str(len(models)) + ' models from ' + folder,
            work, on_success=finish, on_error=fail)

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
