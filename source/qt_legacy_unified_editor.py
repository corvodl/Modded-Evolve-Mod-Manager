"""Unified Stage 2 editor window backed by an original/legacy Evolve ZIP PAK.

All normal explorer, GPU model, GFX and text controls come from QtWorkspaceEditor.
Only archive reading, staged edits, legacy DDS and unsigned rebuild differ.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox

from qt_workspace_editor import QtWorkspaceEditor, TEXT_SUFFIXES
from legacy_pak_workspace import (
    load_project, read_editable_text, stage_text, stage_file, revert_file,
    changed_members, export_member, rebuild_pak,
)
from legacy_texture_preview import decode_legacy_texture
from legacy_unified_workspace import (
    unified_view_for_project, materialize_legacy_member, materialize_model_companions,
)
from qt_operation_progress import run_busy

LEGACY_DDS = re.compile(r"(?i)\.dds(?:\.\d+a?)?$")


class LegacyUnifiedWorkspaceEditor(QtWorkspaceEditor):
    """Same Stage 2 editor and file explorer, with a legacy PAK backend."""

    def __init__(self, manager, folder):
        self.legacy_project = load_project(folder)
        cache = unified_view_for_project(self.legacy_project)
        self._legacy_text_encoding = "utf-8"
        self._legacy_warnings = []
        super().__init__(manager, cache)
        self.setObjectName("unifiedLegacyStage2Editor")
        self.setWindowTitle("Edit Files — " + self.legacy_project["source_name"] +
                            " [Legacy ZIP source]")
        self.status.setText(
            "Legacy Evolve source — same Stage 2 explorer/viewers. Edits go to "
            "private overrides; only unsigned legacy PAK rebuilds are supported.")
        self._configure_legacy_menus()

    def _configure_legacy_menus(self):
        file_menu = self.editor_menus["File"]
        file_menu.addSeparator()
        file_menu.addAction("Build unsigned legacy PAK…", self.build_legacy)
        file_menu.addAction("Export selected original/edited asset…", self.export_raw)
        # Commands that silently operate on Stage 2 staging, signing or
        # source conventions are inappropriate for a legacy ZIP project.
        disabled_titles = (
            "Validate Project Mod", "PAK Difference Inspector",
            "Open Level Editor", "Import Same-Layout Model",
            "Import Original CAF/DBA", "Project History", "Gameplay Presets",
            "Recovery Snapshot", "Batch Asset Export",
            "Import Replacement DDS", "Import Replacement PNG",
        )
        for menu in self.editor_menus.values():
            for action in menu.actions():
                if any(word in action.text() for word in disabled_titles):
                    action.setEnabled(False)
                    action.setToolTip(
                        "This operation needs an installed signed Stage 2 workspace.")
        self.current_file_label.setToolTip(
            "Legacy archive source; conversion into Stage 2 is not assumed.")

    def _category(self, rel):
        if LEGACY_DDS.search(rel):
            return "images"
        return super()._category(rel)

    def _legacy_image_load(self, rel):
        # Use the existing Stage 2 image view, not a second legacy viewer.
        image, desc = decode_legacy_texture(self.legacy_project, rel)
        memory = BytesIO()
        image.convert("RGBA").save(memory, format="PNG")
        pix = QPixmap()
        if not pix.loadFromData(memory.getvalue(), "PNG"):
            raise ValueError("Legacy texture PNG preview conversion failed.")
        self.tabs.setCurrentIndex(self.TABS.index("images"))
        self.current = rel
        self.mode = "images"
        self.raw = b""
        self.stream = None
        self.full_character_active = False
        self.current_file_label.setText(rel + "  [Legacy source]")
        self.views["images"].setPixmap(
            pix.scaled(720, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.status.setText(
            rel + " | " + desc + " | historical PAK | "
            "export PNG for Stage 2 conversion; not automatically compatible.")
        self._sync_editor_context_actions()

    def load(self, rel, *, full_character=False):
        if rel not in self.records:
            raise ValueError("Legacy asset is no longer indexed.")
        if LEGACY_DDS.search(rel):
            self._legacy_image_load(rel)
            return
        if self._category(rel) == "models":
            try:
                detail = materialize_model_companions(
                    self.legacy_project, rel, root=self.workspace)
                self._legacy_warnings = detail["warnings"]
            except (OSError, ValueError) as exc:
                self._legacy_warnings = [str(exc)]
        materialize_legacy_member(self.legacy_project, rel, root=self.workspace)
        super().load(rel, full_character=full_character)
        if self.mode == "files" and self.views["files"].isReadOnly():
            # Older Lua/XML text can use CP1252; don't force it through the
            # standard Stage 2 UTF-8-only decoder.
            try:
                text, encoding = read_editable_text(self.legacy_project, rel)
                if Path(rel).suffix.casefold() in TEXT_SUFFIXES:
                    self.views["files"].setPlainText(text)
                    self.views["files"].setReadOnly(False)
                    self.loaded_text = text
                    self._legacy_text_encoding = encoding
            except (OSError, ValueError):
                pass
        self.status.setText(
            rel + " | legacy Evolve PAK | independent overrides; "
            "original and Stage 2 game unchanged" +
            (" | " + "; ".join(self._legacy_warnings[:2])
             if self._legacy_warnings else ""))
        self._sync_editor_context_actions()

    def save(self):
        if not self.current or self.mode != "files":
            return
        view = self.views["files"]
        if view.isReadOnly():
            return
        try:
            text = view.toPlainText()
            stage_text(self.legacy_project, self.current, text,
                       self._legacy_text_encoding)
            materialize_legacy_member(
                self.legacy_project, self.current, root=self.workspace)
            self.raw = (self.workspace / "files" / self.current).read_bytes()
            self.loaded_text = text
            self.status.setText("Saved legacy override for " + self.current +
                                "; original PAK untouched.")
            self._sync_editor_context_actions()
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "Legacy asset edit", str(exc))

    def import_raw(self):
        if not self.current:
            return
        file, _ = QFileDialog.getOpenFileName(
            self, "Replace selected legacy file with external file")
        if not file:
            return
        try:
            stage_file(self.legacy_project, self.current, file)
            # Force retrieval from override instead of stale cache.
            cached = self.workspace / "files" / self.current
            cached.unlink(missing_ok=True)
            self.load(self.current)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Legacy binary replacement", str(exc))

    def restore_original(self):
        if not self.current:
            return
        if QMessageBox.question(
            self, "Restore original asset?",
            "Remove this project's staged override? The original PAK is never changed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            revert_file(self.legacy_project, self.current)
            (self.workspace / "files" / self.current).unlink(missing_ok=True)
            self.load(self.current)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Legacy restore", str(exc))

    def export_raw(self):
        if not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export legacy asset", Path(self.current).name)
        if not path:
            return
        try:
            run_busy(self, "Export legacy asset", self.current,
                     lambda: export_member(
                         self.legacy_project, self.current, path))
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Legacy asset export", str(exc))

    def export_png(self):
        if not self.current or not LEGACY_DDS.search(self.current):
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export legacy texture for Stage 2 conversion",
            Path(self.current).name.split(".dds", 1)[0] + ".png",
            "PNG (*.png)")
        if filename:
            try:
                image, _ = decode_legacy_texture(
                    self.legacy_project, self.current)
                image.save(filename, "PNG")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Legacy texture export", str(exc))

    def import_png(self):
        QMessageBox.information(
            self, "Legacy texture conversion",
            "Export the highest-resolution PNG, then import it into an "
            "independent signed Stage 2 mod workspace after verifying format "
            "and material compatibility. Replacing legacy DDS stream fragments "
            "requires preserving the old streaming format.")

    def import_dds(self):
        self.import_png()

    def build_legacy(self):
        if not changed_members(self.legacy_project):
            QMessageBox.information(
                self, "Build legacy PAK", "No edits are staged.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Build a new unsigned legacy archive",
            self.legacy_project["source_path"].stem + "-modified.pak",
            "PAK files (*.pak)")
        if not path:
            return
        if QMessageBox.question(
            self, "Unsigned legacy PAK",
            "Rebuild this archive using legacy ZIP methods 0/8? It will NOT "
            "be signed or installed into Evolve Stage 2.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            result = run_busy(
                self, "Build legacy PAK", "Validating ZIP entries and CRCs…",
                lambda: rebuild_pak(self.legacy_project, path))
            QMessageBox.information(
                self, "Legacy PAK built",
                str(result["changes"]) + " modified assets; wrote " +
                result["output"])
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Legacy rebuild failed", str(exc))
