"""Editable Scaleform GFX session UI for PAK mod projects.

Advanced authoring (buttons, sprite timelines, fonts, AS1/2/3) is delegated to
the external FFDec 26.3.0 GUI; direct resource replacements and tag-XML edits
are driven by the documented CLI. No input changes the PAK or the Asset Library
until an explicitly confirmed, backup-protected project commit.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QMessageBox, QGroupBox, QWidget,
)
from gfx_preview_backend import inspect_gfx, GfxError, resolve_imports, export_gfx_png
from gfx_edit_backend import (
    create_gfx_session, read_gfx_session, replace_gfx_resource,
    export_gfx_xml, rebuild_gfx_from_xml, export_gfx_scripts,
    rebuild_gfx_scripts, use_edited_gfx_file,
    undo_gfx_session, apply_gfx_session, launch_gfx_gui,
)
from qt_gfx_preview import GfxPreviewWidget


class GfxEditorDialog(QDialog):
    def __init__(self, owner, relative):
        if owner.read_only_library:
            raise PermissionError(
                "Asset Library originals are read-only. Open an editable Modding project.")
        super().__init__(owner)
        self.owner = owner
        self.manager = owner.manager
        self.relative = relative
        from gfx_preview_backend import preferred_ffdec_override
        self._backend = preferred_ffdec_override(
            owner.manager.settings.get("gfx_ffdec_path"))
        from workspace_file_actions import checked_path
        from gfx_preview_backend import resolve_imports
        source = checked_path(owner.workspace, relative)
        original = source.read_bytes()
        info = inspect_gfx(original)
        deps, issues = resolve_imports(info, relative, owner._gfx_dependency)
        self._issues = issues
        self.snapshot = create_gfx_session(
            owner.workspace, relative, dependencies=deps)
        self.setWindowTitle("Edit Scaleform GFX — " + relative)
        self.resize(1120, 820)
        self.setMinimumSize(670, 550)
        outer = QVBoxLayout(self)
        self.warning = QLabel(
            "Edits are staged in a separate GFX session. "
            "The original project is unchanged until Apply to Mod Project. "
            "FFDec supports advanced ActionScript, timeline, buttons, fonts and tags. "
            "Scaleform runtime callbacks still require in-game testing.")
        self.warning.setWordWrap(True)
        outer.addWidget(self.warning)
        self.viewer = GfxPreviewWidget(self)
        self.viewer.setup.setEnabled(True)
        outer.addWidget(self.viewer, 1)

        tools = QGroupBox("Editing and rebuild")
        layout = QVBoxLayout(tools)
        row = QHBoxLayout()
        self.category = QComboBox()
        self.category.setObjectName("gfxEditCategory")
        self.category.currentIndexChanged.connect(self._category_changed)
        row.addWidget(QLabel("Native resource:"))
        row.addWidget(self.category)
        self.resource = QComboBox()
        self.resource.setObjectName("gfxEditResource")
        row.addWidget(self.resource, 1)
        self.replace = QPushButton("Replace selected resource…")
        self.replace.setObjectName("gfxReplaceResource")
        self.replace.clicked.connect(self.replace_resource)
        row.addWidget(self.replace)
        layout.addLayout(row)

        advanced = QHBoxLayout()
        self.open_ffdec = QPushButton("Open full FFDec editor…")
        self.open_ffdec.setObjectName("gfxFullEditor")
        self.open_ffdec.clicked.connect(self.open_advanced)
        advanced.addWidget(self.open_ffdec)
        self.xml_export = QPushButton("Export editable tag XML…")
        self.xml_export.clicked.connect(self.export_xml)
        advanced.addWidget(self.xml_export)
        self.xml_import = QPushButton("Rebuild from edited XML…")
        self.xml_import.clicked.connect(self.import_xml)
        advanced.addWidget(self.xml_import)
        self.import_movie = QPushButton("Import edited GFX…")
        self.import_movie.setObjectName("gfxImportEdited")
        self.import_movie.clicked.connect(self.import_edited)
        advanced.addWidget(self.import_movie)
        self.reload_movie = QPushButton("Reload FFDec changes")
        self.reload_movie.setObjectName("gfxReloadEdited")
        self.reload_movie.clicked.connect(self.reload_edited)
        advanced.addWidget(self.reload_movie)
        self.undo = QPushButton("Undo staged edit")
        self.undo.clicked.connect(self.undo_edit)
        advanced.addWidget(self.undo)
        layout.addLayout(advanced)
        script_controls = QHBoxLayout()
        self.script_export = QPushButton("Export ActionScript sources…")
        self.script_export.setObjectName("gfxExportActionScript")
        self.script_export.clicked.connect(self.export_scripts)
        script_controls.addWidget(self.script_export)
        self.script_import = QPushButton("Rebuild from edited ActionScript…")
        self.script_import.setObjectName("gfxImportActionScript")
        self.script_import.clicked.connect(self.import_scripts)
        script_controls.addWidget(self.script_import)
        script_controls.addStretch()
        layout.addLayout(script_controls)
        outer.addWidget(tools)

        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.apply = QPushButton("Apply to Mod Project")
        self.apply.setObjectName("gfxApplyToProject")
        self.apply.clicked.connect(self.apply_to_workspace)
        footer.addWidget(self.apply)
        cancel = QPushButton("Close without applying")
        cancel.clicked.connect(self.close)
        footer.addWidget(cancel)
        outer.addLayout(footer)
        self._reload_preview()

    def _reload_preview(self):
        self.snapshot = read_gfx_session(self.owner.workspace,
                                         self.snapshot.session)
        data = self.snapshot.working_file.read_bytes()
        info = inspect_gfx(data)
        _deps, self._issues = resolve_imports(
            info, self.relative, self.owner._gfx_dependency)
        display = {(name, charid) for name, charid in info.resources}
        current = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        for kind in ("image", "shape", "text", "sound", "binary"):
            if any(k == kind for k, _ in display):
                self.category.addItem(kind.title() + "s", kind)
        if current is not None:
            for i in range(self.category.count()):
                if self.category.itemData(i) == current:
                    self.category.setCurrentIndex(i)
                    break
        self.category.blockSignals(False)
        self._category_changed()
        # A successful imported XML or FFDec GUI save invalidates the old
        # raster cache because the source bytes now differ.
        self.viewer.open_movie(data, self.relative,
                               resolve=self.owner._gfx_dependency,
                               context="gfx-edit:" + str(self.snapshot.session),
                               backend=self._backend)
        self.status.setText(
            ("Staged GFX: %d native edits; %s | %s" %
             (self.snapshot.edits,
              "unsaved changes ready for review" if self.snapshot.changed
              else "original content unchanged",
              ("Missing imports: " + ", ".join(self._issues))
              if self._issues else "matching-context imports available")))
        self.apply.setEnabled(self.snapshot.changed)
        self.undo.setEnabled(self.snapshot.edits > 0)

    def _category_changed(self, *_):
        kind = self.category.currentData()
        info = inspect_gfx(self.snapshot.working_file.read_bytes())
        labels = {cid: name for cid, name in info.exports if name}
        self.resource.clear()
        for resource_kind, character_id in info.resources:
            if resource_kind == kind:
                self.resource.addItem(
                    f"#{character_id} " + labels.get(character_id, ""), character_id)
        self.replace.setEnabled(self.resource.count() > 0)

    def _run(self, label, action):
        try:
            from qt_operation_progress import run_busy
            result = run_busy(
                self, "Scaleform " + label, "Validating FFDec editing result…",
                action)
            self._reload_preview()
            return result
        except Exception as exc:
            QMessageBox.warning(self, "GFX " + label + " failed", str(exc))
            return None

    def replace_resource(self):
        kind = self.category.currentData()
        character_id = self.resource.currentData()
        if not kind or character_id is None:
            return
        formats = {
            "image": "Image (*.png *.jpg *.jpeg)",
            "shape": "SVG (*.svg)",
            "text": "Text (*.txt)",
            "sound": "Sound (*.mp3 *.wav)",
            "binary": "Binary (*.bin)",
        }
        file, _ = QFileDialog.getOpenFileName(
            self, "Select replacement for " + str(character_id),
            "", formats[kind])
        if not file:
            return
        self._run(kind + " replacement",
                  lambda: replace_gfx_resource(
                      self.owner.workspace, self.snapshot.session,
                      kind, character_id, file, configured=self._backend))

    def edit_gfx(self):
        # The shared preview widget offers this action on editing dialogs.
        self.open_advanced()

    def open_advanced(self):
        try:
            pid = launch_gfx_gui(
                self.owner.workspace, self.snapshot.session, configured=self._backend)
            QMessageBox.information(
                self, "FFDec launched",
                "Edit and save the private movie.gfx copy in FFDec. "
                "You can change scripts, symbols, timeline, frames and tags "
                "through FFDec's editor. Once saved, return here and click "
                "'Reload edited GFX' to validate it. "
                f"FFDec process: {pid}.\n\n"
                "Saving in FFDec does not modify the PAK or installed game.")
        except Exception as exc:
            QMessageBox.warning(self, "Cannot open FFDec editor", str(exc))

    def export_xml(self):
        output, _ = QFileDialog.getSaveFileName(
            self, "Export editable FFDec tag XML",
            Path(self.relative).stem + ".xml", "XML (*.xml)")
        if output:
            self._run("XML export", lambda: export_gfx_xml(
                self.owner.workspace, self.snapshot.session,
                output, configured=self._backend))

    def import_xml(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Rebuild GFX from edited FFDec XML", "", "XML (*.xml)")
        if path:
            self._run("XML rebuild", lambda: rebuild_gfx_from_xml(
                self.owner.workspace, self.snapshot.session,
                path, configured=self._backend))

    def export_scripts(self):
        parent = QFileDialog.getExistingDirectory(
            self, "Choose ActionScript export parent", str(Path.home()))
        if not parent:
            return
        folder = Path(parent) / (Path(self.relative).stem + "-actionscripts")
        self._run("ActionScript export", lambda: export_gfx_scripts(
            self.owner.workspace, self.snapshot.session, folder,
            configured=self._backend))

    def import_scripts(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose edited FFDec ActionScript source directory",
            str(Path.home()))
        if folder:
            self._run("ActionScript rebuild", lambda: rebuild_gfx_scripts(
                self.owner.workspace, self.snapshot.session,
                folder, configured=self._backend))

    def import_edited(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a separately saved edited GFX",
            str(self.snapshot.session), "Scaleform GFX (*.gfx)")
        if path:
            self._run("movie import", lambda: use_edited_gfx_file(
                self.owner.workspace, self.snapshot.session, path))

    def reload_edited(self):
        try:
            self._reload_preview()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid FFDec movie", str(exc))

    def undo_edit(self):
        self._run("undo", lambda: undo_gfx_session(
            self.owner.workspace, self.snapshot.session))

    def apply_to_workspace(self):
        if not self.snapshot.changed:
            return
        if QMessageBox.question(
            self, "Apply edited GFX to Mod Project?",
            "Validate and replace ONLY the selected extracted GFX file? "
            "Its current bytes are backed up in EditorBackups. "
            "The original game PAK and Asset Library will not be modified. "
            "Runtime compatibility is not established; test offline after "
            "using the ordinary mod build/sign workflow.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            result = apply_gfx_session(
                self.owner.workspace, self.snapshot.session,
                expected_sha256=self.snapshot.original_sha256)
            if result["changed"]:
                self.owner.load(self.relative)
                self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Edited GFX was not applied", str(exc))

    def configure_backend(self):
        self.viewer.configure_backend()
        from gfx_preview_backend import preferred_ffdec_override
        self._backend = preferred_ffdec_override(
            self.owner.manager.settings.get("gfx_ffdec_path"))
        self._reload_preview()

    def export_png(self):
        try:
            content = self.viewer.selected_png()
            output, _ = QFileDialog.getSaveFileName(
                self, "Export edited movie frame PNG",
                Path(self.relative).stem + "-edited.png", "PNG (*.png)")
            if output:
                export_gfx_png(content, output)
        except Exception as exc:
            QMessageBox.warning(self, "Could not export rendered GFX", str(exc))

    def closeEvent(self, event):
        self.viewer.cancel_pending()
        super().closeEvent(event)
