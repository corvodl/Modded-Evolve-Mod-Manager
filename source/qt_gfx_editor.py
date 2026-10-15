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
    QComboBox, QFileDialog, QMessageBox, QGroupBox, QWidget, QMenuBar,
    QLineEdit, QSplitter, QScrollArea,
)
from gfx_preview_backend import inspect_gfx, GfxError, resolve_imports, export_gfx_png
from gfx_edit_backend import (
    create_gfx_session, read_gfx_session, replace_gfx_resource,
    export_gfx_xml, rebuild_gfx_from_xml, export_gfx_scripts,
    rebuild_gfx_scripts, use_edited_gfx_file,
    undo_gfx_session, apply_gfx_session, launch_gfx_gui,
)
from qt_gfx_preview import GfxPreviewWidget
from qt_navigation_icons import navigation_icon, action_icon
from gfx_featured_promo import stage_featured_promo
from qt_gfx_layout_editor import GfxLayoutDialog


class GfxEditorDialog(QDialog):
    def __init__(self, owner, relative, *, embedded=False):
        if owner.read_only_library:
            raise PermissionError(
                "Asset Library originals are read-only. Open an editable Modding project.")
        super().__init__(owner)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.owner = owner
        self.manager = owner.manager
        self.relative = relative
        self._backend = owner.manager.settings.get("gfx_ffdec_path")
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
        # The old six-button editing row clipped at normal Windows sizes.
        # All advanced actions remain discoverable in native menus.
        self.commands = QMenuBar(self)
        self.commands.setObjectName("gfxEditorMenuBar")
        outer.setMenuBar(self.commands)
        self.file_menu = self.commands.addMenu("File")
        self.edit_menu = self.commands.addMenu("Edit")
        self.tools_menu = self.commands.addMenu("Tools")
        self.warning = QLabel(
            "Edits are staged in a separate GFX session. "
            "The original project is unchanged until Apply to Mod Project. "
            "FFDec supports advanced ActionScript, timeline, buttons, fonts and tags. "
            "Scaleform runtime callbacks still require in-game testing.")
        self.warning.setWordWrap(True)
        outer.addWidget(self.warning)
        self.viewer = GfxPreviewWidget(self)
        self.viewer.setup.setEnabled(True)
        # Reuse the authoring panel within the ordinary file editor. The canvas
        # gets most of the width; edit controls scroll in a compact inspector.
        self.editor_split = QSplitter(Qt.Horizontal, self)
        self.editor_split.setObjectName("gfxAuthoringSplitter")
        self.editor_split.addWidget(self.viewer)
        self.inspector_scroll = QScrollArea(self)
        self.inspector_scroll.setObjectName("gfxAuthoringInspector")
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_content = QWidget()
        self.inspector_layout = QVBoxLayout(self.inspector_content)
        self.inspector_layout.setContentsMargins(7, 7, 7, 7)
        self.inspector_layout.setSpacing(8)
        self.inspector_scroll.setWidget(self.inspector_content)
        self.editor_split.addWidget(self.inspector_scroll)
        self.editor_split.setStretchFactor(0, 5)
        self.editor_split.setStretchFactor(1, 2)
        self.editor_split.setSizes([900, 310])
        outer.addWidget(self.editor_split, 1)

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
        self.replace.setIcon(action_icon(self.replace.text()))
        self.replace.setObjectName("gfxReplaceResource")
        self.replace.clicked.connect(self.replace_resource)
        row.addWidget(self.replace)
        layout.addLayout(row)

        advanced = QHBoxLayout()
        self.open_ffdec = QPushButton("Open full FFDec editor…")
        self.open_ffdec.setIcon(action_icon(self.open_ffdec.text()))
        self.open_ffdec.setObjectName("gfxFullEditor")
        self.open_ffdec.clicked.connect(self.open_advanced)
        advanced.addWidget(self.open_ffdec)
        self.xml_export = QPushButton("Export editable tag XML…")
        self.xml_export.setIcon(action_icon(self.xml_export.text()))
        self.xml_export.clicked.connect(self.export_xml)
        advanced.addWidget(self.xml_export)
        self.xml_import = QPushButton("Rebuild from edited XML…")
        self.xml_import.setIcon(action_icon(self.xml_import.text()))
        self.xml_import.clicked.connect(self.import_xml)
        advanced.addWidget(self.xml_import)
        self.import_movie = QPushButton("Import edited GFX…")
        self.import_movie.setIcon(action_icon(self.import_movie.text()))
        self.import_movie.setObjectName("gfxImportEdited")
        self.import_movie.clicked.connect(self.import_edited)
        advanced.addWidget(self.import_movie)
        self.reload_movie = QPushButton("Reload FFDec changes")
        self.reload_movie.setIcon(action_icon(self.reload_movie.text()))
        self.reload_movie.setObjectName("gfxReloadEdited")
        self.reload_movie.clicked.connect(self.reload_edited)
        advanced.addWidget(self.reload_movie)
        self.undo = QPushButton("Undo staged edit")
        self.undo.setIcon(action_icon(self.undo.text()))
        self.undo.clicked.connect(self.undo_edit)
        advanced.addWidget(self.undo)
        # Do not place these six long actions in a single visible row.
        # They remain real buttons for programmatic operation and tests;
        # menus are the user-facing discoverable entry points.
        for button in (self.open_ffdec, self.xml_export, self.xml_import,
                       self.import_movie, self.reload_movie, self.undo):
            button.setParent(tools)
            button.hide()
        script_controls = QHBoxLayout()
        self.script_export = QPushButton("Export ActionScript sources…")
        self.script_export.setIcon(action_icon(self.script_export.text()))
        self.script_export.setObjectName("gfxExportActionScript")
        self.script_export.clicked.connect(self.export_scripts)
        script_controls.addWidget(self.script_export)
        self.script_import = QPushButton("Rebuild from edited ActionScript…")
        self.script_import.setIcon(action_icon(self.script_import.text()))
        self.script_import.setObjectName("gfxImportActionScript")
        self.script_import.clicked.connect(self.import_scripts)
        script_controls.addWidget(self.script_import)
        script_controls.addStretch()
        for button in (self.script_export, self.script_import):
            button.setParent(tools)
            button.hide()
        self.file_menu.addAction("Import edited GFX…", self.import_edited)
        self.file_menu.addAction("Export editable tag XML…", self.export_xml)
        self.file_menu.addAction("Export ActionScript sources…", self.export_scripts)
        self.edit_menu.addAction("Replace selected resource…", self.replace_resource)
        self.edit_menu.addAction("Undo staged edit", self.undo_edit)
        self.tools_menu.addAction("Open full FFDec editor…", self.open_advanced)
        self.tools_menu.addAction("Rebuild from edited XML…", self.import_xml)
        self.tools_menu.addAction("Rebuild from edited ActionScript…", self.import_scripts)
        self.tools_menu.addAction("Reload FFDec changes", self.reload_edited)
        self.edit_menu.aboutToShow.connect(self._sync_command_menus)
        self.tools_menu.aboutToShow.connect(self._sync_command_menus)
        self.layout_button = QPushButton("Visual layout editor…")
        self.layout_button.setObjectName("gfxOpenVisualLayout")
        self.layout_button.setIcon(action_icon(self.layout_button.text()))
        self.layout_button.setToolTip(
            "Edit verified static GFX placements and guarded featured "
            "title/TRY/BUY runtime overrides in a private edit session.")
        self.layout_button.clicked.connect(self.open_layout)
        layout.addWidget(self.layout_button)
        self.tools_menu.addAction("Visual layout editor…", self.open_layout)
        self._layout_dialog = None
        self.inspector_layout.addWidget(tools)

        # Retail main_menu.gfx receives its featured offer from a game
        # callback. Only expose guarded script edits for this exact movie;
        # changing a static title resource would not change dynamic text.
        self.promo_group = None
        if relative.casefold() == "libs/ui/main_menu.gfx":
            self.promo_group = QGroupBox("Featured promotional UI (experimental)")
            self.promo_group.setObjectName("gfxFeaturedPromoControls")
            promo_layout = QVBoxLayout(self.promo_group)
            promo_row = QHBoxLayout()
            self.promo_mode = QComboBox()
            self.promo_mode.setObjectName("gfxFeaturedPromoMode")
            self.promo_mode.addItem("Hide", "hide")
            self.promo_mode.addItem("Remove / disable", "remove")
            self.promo_mode.addItem("Custom title text", "custom")
            promo_row.addWidget(self.promo_mode)
            self.promo_target = QComboBox()
            self.promo_target.setObjectName("gfxFeaturedPromoTarget")
            self.promo_target.addItem("Entire promotion", "all")
            self.promo_target.addItem("Title only", "title")
            promo_row.addWidget(self.promo_target)
            self.promo_text = QLineEdit()
            self.promo_text.setObjectName("gfxFeaturedPromoText")
            self.promo_text.setMaxLength(72)
            self.promo_text.setPlaceholderText("Replacement featured title")
            promo_row.addWidget(self.promo_text, 2)
            self.promo_stage = QPushButton("Stage promo edit")
            self.promo_stage.setObjectName("gfxFeaturedPromoStage")
            self.promo_stage.clicked.connect(self._stage_featured_promo)
            promo_row.addWidget(self.promo_stage)
            promo_layout.addLayout(promo_row)
            self.promo_note = QLabel(
                "Rebuilds the main menu's setFeatured ActionScript in a private "
                "session. Hide changes visibility; Remove additionally attempts "
                "to disable/remove runtime clips (static clips are hidden). "
                "Game callbacks or localization may override these changes. "
                "Test offline before publishing.")
            self.promo_note.setWordWrap(True)
            promo_layout.addWidget(self.promo_note)
            self.promo_mode.currentIndexChanged.connect(self._promo_options_changed)
            self.promo_target.currentIndexChanged.connect(self._promo_options_changed)
            self.promo_text.textChanged.connect(self._promo_options_changed)
            self._promo_options_changed()
            self.inspector_layout.addWidget(self.promo_group)
            self.tools_menu.addAction(
                "Featured promotional UI…", self._focus_featured_promo)

        self.inspector_layout.addStretch(1)
        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.apply = QPushButton("Apply to Mod Project")
        self.apply.setIcon(action_icon(self.apply.text()))
        self.apply.setObjectName("gfxApplyToProject")
        self.apply.clicked.connect(self.apply_to_workspace)
        footer.addWidget(self.apply)
        cancel = QPushButton("Back to preview" if embedded else "Close without applying")
        cancel.clicked.connect(owner._close_gfx_editor if embedded else self.close)
        footer.addWidget(cancel)
        outer.addLayout(footer)
        self._reload_preview()

    def open_layout(self):
        if self._layout_dialog is not None and self._layout_dialog.isVisible():
            self._layout_dialog.raise_()
            self._layout_dialog.activateWindow()
            return
        try:
            self._layout_dialog = GfxLayoutDialog(self)
            self._layout_dialog.show()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot open visual GFX editor", str(exc))

    def _focus_featured_promo(self):
        if self.promo_group is not None:
            self.promo_mode.setFocus()

    def _promo_options_changed(self, *_):
        if self.promo_group is None:
            return
        custom = self.promo_mode.currentData() == "custom"
        self.promo_target.setEnabled(not custom)
        self.promo_text.setEnabled(custom)
        self.promo_stage.setEnabled(
            not custom or bool(self.promo_text.text().strip()))

    def _stage_featured_promo(self):
        if self.promo_group is None:
            return
        mode = self.promo_mode.currentData()
        target = "title" if mode == "custom" else self.promo_target.currentData()
        text = self.promo_text.text() if mode == "custom" else ""
        self._run("featured promotion", lambda: stage_featured_promo(
            self.owner.workspace, self.snapshot.session, mode,
            target=target, custom=text, configured=self._backend))

    def _sync_command_menus(self):
        for action in self.edit_menu.actions():
            if action.text() == "Undo staged edit":
                action.setEnabled(self.undo.isEnabled())
            elif action.text() == "Replace selected resource…":
                action.setEnabled(self.replace.isEnabled())
        for action in self.tools_menu.actions():
            if action.text() == "Reload FFDec changes":
                action.setEnabled(self.reload_movie.isEnabled())

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
                               backend=self._backend,
                               available_paths=self.owner._gfx_available_paths(),
                               texture_preview=self.owner._gfx_texture_image)
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
                if not self.embedded:
                    self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Edited GFX was not applied", str(exc))

    def configure_backend(self):
        self.viewer.configure_backend()
        self._backend = self.owner.manager.settings.get("gfx_ffdec_path")
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
        if self._layout_dialog is not None:
            self._layout_dialog.close()
        self.viewer.cancel_pending()
        super().closeEvent(event)
