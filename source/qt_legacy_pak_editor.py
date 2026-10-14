"""Separate Qt viewer/editor for ordinary pre-release (2012) Evolve ZIP PAKs.

Historical archives cannot be signed or installed as Stage 2 CryPaks. This
window permits browsing, native text edits, binary replacements, PNG previews
and checked ZIP PAK rebuilds in an isolated project directory.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re

from PySide6.QtCore import QSize, Qt, Signal, QThread
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTreeWidget, QTreeWidgetItem, QSplitter,
    QPlainTextEdit, QFileDialog, QMessageBox, QStackedWidget, QProgressDialog,
    QMenuBar,
)
from qt_navigation_icons import navigation_icon, action_icon
from legacy_pak_workspace import (
    PROJECT_FILE, create_project, load_project, read_member, read_editable_text,
    stage_text, stage_file, revert_file, changed_members, export_member, rebuild_pak,
    entries_by_name,
)
from legacy_texture_preview import decode_legacy_texture

TEXT_SUFFIXES = frozenset((
    ".lua", ".xml", ".ent", ".xsd", ".txt", ".cfg", ".ini",
    ".csv", ".json", ".mtl", ".chrparams", ".bspace", ".comb",
))
TEXTURE = re.compile(r"(?i)\.dds(?:\.\d+a?)?$")


class _LegacyBuildThread(QThread):
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, project, target, parent=None):
        super().__init__(parent)
        self.folder = project["folder"]
        self.target = target

    def run(self):
        try:
            data = rebuild_pak(load_project(self.folder), self.target,
                               progress=lambda done, total: self.progress.emit(done, total))
            self.completed.emit(data)
        except Exception as exc:
            self.failed.emit(str(exc))


class LegacyPAKEditor(QMainWindow):
    """No signed Stage 2 actions are ever exposed in this window."""

    def __init__(self, manager, folder):
        super().__init__(manager)
        self.manager = manager
        self.project = load_project(folder)
        self.records = entries_by_name(self.project)
        self.active = None
        self.text_encoding = None
        self.loaded_text = None
        self._worker = None
        self.setObjectName("legacyPakEditor")
        self.setWindowTitle("Legacy Evolve PAK Editor • " + self.project["source_name"])
        self.resize(1120, 760)
        self.setMinimumSize(740, 500)
        shell = QWidget(self)
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        head = QLabel("LEGACY EVOLVE (2012) • ZIP PAK PROJECT")
        head.setObjectName("sectionTitle")
        outer.addWidget(head)
        notice = QLabel(
            "Historical PAK format (methods 0/8). Edits are saved as project "
            "overrides. Rebuilt PAKs are UNSIGNED and are NOT Stage 2 mods.")
        notice.setWordWrap(True)
        notice.setObjectName("legacyPakWarning")
        outer.addWidget(notice)
        menu = self.menuBar()
        menu.setNativeMenuBar(False)
        self.editor_menus = {section: menu.addMenu(section)
                             for section in ("File", "Edit", "View", "Tools")}
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.tool_buttons = {}
        for label, callback, section in (
            ("Save Text", self.save_text, "Edit"),
            ("Replace File…", self.replace_file, "Edit"),
            ("Restore Original", self.restore_file, "Edit"),
            ("Export File…", self.export_file, "File"),
            ("Export Texture PNG…", self.export_png, "File"),
            ("Build Legacy PAK…", self.build, "File"),
        ):
            button = QPushButton(label, shell)
            button.setObjectName("legacy" + re.sub(r"\W", "", label))
            button.setToolTip(label)
            button.setIconSize(QSize(15, 15))
            button.clicked.connect(callback)
            self.editor_menus[section].addAction(label, callback)
            self.tool_buttons[label] = button
            # Context-only shortcuts: large import/rebuild tools stay in menus.
            if label in ("Save Text", "Export Texture PNG…"):
                toolbar.addWidget(button)
            else:
                button.hide()
            if label == "Save Text":
                self.save_button = button
            if label == "Build Legacy PAK…":
                self.build_button = button
            if label == "Export Texture PNG…":
                self.png_button = button
        self.editor_menus["View"].addAction("Refresh File Explorer", self.refresh_tree)
        self.editor_menus["Tools"].addAction("Verify Source CRC", self.verify_source)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)
        split = QSplitter(Qt.Horizontal)
        files = QWidget()
        left = QVBoxLayout(files)
        left.addWidget(QLabel("PAK FILE EXPLORER"))
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Search names and paths…")
        self.search.textChanged.connect(self.refresh_tree)
        left.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(("Asset", "Size"))
        self.tree.setColumnWidth(0, 330)
        self.tree.currentItemChanged.connect(self._chosen)
        left.addWidget(self.tree)
        split.addWidget(files)
        right = QWidget()
        layout = QVBoxLayout(right)
        self.path_label = QLabel("Select a file to preview or edit.")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        self.stack = QStackedWidget()
        self.text = QPlainTextEdit()
        self.text.setObjectName("legacyTextEditor")
        self.text.textChanged.connect(self._dirty)
        self.stack.addWidget(self.text)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(100, 90)
        self.stack.addWidget(self.image)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.info)
        layout.addWidget(self.stack, 1)
        split.addWidget(right)
        split.setSizes((410, 680))
        outer.addWidget(split, 1)
        self.status = QLabel("")
        outer.addWidget(self.status)
        self.refresh_tree()
        self._dirty()

    def _confirm_discard(self):
        if (self.stack.currentWidget() is self.text and self.loaded_text is not None
                and self.text.toPlainText() != self.loaded_text):
            return QMessageBox.question(
                self, "Unsaved legacy edit", "Discard unsaved text changes?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        return True

    def refresh_tree(self):
        query = self.search.text().casefold()
        previous = self.active
        self.tree.blockSignals(True)
        self.tree.clear()
        folders = {}
        modified = set(changed_members(self.project))
        for name, record in self.records.items():
            if query not in name.casefold():
                continue
            parent = None
            parts = name.split("/")
            for position in range(len(parts) - 1):
                key = "/".join(parts[:position + 1])
                if key not in folders:
                    item = QTreeWidgetItem([parts[position], ""])
                    item.setIcon(0, navigation_icon("folder", size=18))
                    if parent:
                        parent.addChild(item)
                    else:
                        self.tree.addTopLevelItem(item)
                    folders[key] = item
                parent = folders[key]
            category = ("texture" if TEXTURE.search(name) else
                        "text" if Path(name).suffix.casefold() in TEXT_SUFFIXES else
                        "file")
            name_label = parts[-1] + ("  • edited" if name in modified else "")
            item = QTreeWidgetItem([name_label, f"{record['size']:,} B"])
            item.setData(0, Qt.UserRole, name)
            item.setIcon(0, navigation_icon(category, size=18))
            item.setToolTip(0, name)
            if parent:
                parent.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            if name == previous:
                self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)
        if query:
            self.tree.expandAll()
        self.status.setText(
            f"{len(self.records)} archive files • {len(modified)} modified • "
            "editing a separate project, never installed game files")

    def _chosen(self, item, before):
        if item is None:
            return
        name = item.data(0, Qt.UserRole)
        if not name or name == self.active:
            return
        if not self._confirm_discard():
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(before)
            self.tree.blockSignals(False)
            return
        self.active = name
        self.show_file()

    def show_file(self):
        if not self.active:
            return
        name = self.active
        record = self.records[name]
        self.path_label.setText(name)
        self.loaded_text = None
        self.text_encoding = None
        self.image.clear()
        self.png_button.setEnabled(bool(TEXTURE.search(name)))
        if Path(name).suffix.casefold() in TEXT_SUFFIXES:
            try:
                text, encoding = read_editable_text(self.project, name)
                self.loaded_text = text
                self.text_encoding = encoding
                self.text.setPlainText(text)
                self.stack.setCurrentWidget(self.text)
            except (ValueError, UnicodeError, OSError) as exc:
                self.info.setText("Text preview unavailable: " + str(exc) +
                                  "\nUse Replace File to import a supported edited asset.")
                self.stack.setCurrentWidget(self.info)
        elif TEXTURE.search(name):
            try:
                from PIL import Image
                picture, description = decode_legacy_texture(self.project, name)
                pix = QPixmap()
                with BytesIO() as stream:
                    picture.convert("RGBA").save(stream, format="PNG")
                    pix.loadFromData(stream.getvalue(), "PNG")
                self.image.setPixmap(pix.scaled(
                    900, 650, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.stack.setCurrentWidget(self.image)
                self.status.setText(description)
            except (ValueError, OSError) as exc:
                self.info.setText(
                    "The texture is retained in its original bytes. " +
                    str(exc) + "\nYou can export or replace the raw texture fragments.")
                self.stack.setCurrentWidget(self.info)
        else:
            self.info.setText(
                f"Binary file • {record['size']:,} bytes\n"
                "Audio (FSB/FEV), CRID/USM videos and other binary assets can "
                "be exported or replaced with files authored externally.\n"
                "The manager does not claim to decode or play these codecs.")
            self.stack.setCurrentWidget(self.info)
        self._dirty()

    def _dirty(self):
        changed = (self.active is not None and self.text_encoding is not None
                   and self.stack.currentWidget() is self.text
                   and self.loaded_text != self.text.toPlainText())
        self.save_button.setEnabled(changed)
        self.save_button.setVisible(changed)
        self.png_button.setVisible(bool(self.active and
                                        TEXTURE.search(self.active)))

    def verify_source(self):
        try:
            from legacy_pak_workspace import inspect_archive
            result = inspect_archive(self.project["source_path"], verify_crc=True)
            QMessageBox.information(self, "Legacy PAK integrity",
                                    f"Verified {result['count']} archive members.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Legacy PAK integrity", str(exc))

    def save_text(self):
        if not self.active or not self.text_encoding:
            return
        try:
            stage_text(self.project, self.active, self.text.toPlainText(),
                       self.text_encoding)
            self.loaded_text = self.text.toPlainText()
            self._dirty()
            self.refresh_tree()
            self.status.setText("Saved text to an independent legacy project override.")
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "Legacy text edit", str(exc))

    def replace_file(self):
        if not self.active:
            return
        target, _ = QFileDialog.getOpenFileName(
            self, "Import replacement file into private legacy project")
        if not target:
            return
        try:
            stage_file(self.project, self.active, target)
            self.loaded_text = None
            self.refresh_tree()
            self.show_file()
            self.status.setText("Staged binary replacement. Original archive untouched.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Legacy replace file", str(exc))

    def restore_file(self):
        if self.active and QMessageBox.question(
                self, "Restore original file",
                "Remove this project's override? The original PAK stays unchanged.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            try:
                revert_file(self.project, self.active)
                self.loaded_text = None
                self.refresh_tree()
                self.show_file()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Restore failed", str(exc))

    def export_file(self):
        if not self.active:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export native asset", Path(self.active).name)
        if filename:
            try:
                export_member(self.project, self.active, filename)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def export_png(self):
        if not self.active or not TEXTURE.search(self.active):
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export highest available texture mip as PNG",
            Path(self.active).name.rsplit(".dds", 1)[0] + ".png",
            "PNG (*.png)")
        if not filename:
            return
        try:
            from PIL import Image
            image, _ = decode_legacy_texture(self.project, self.active)
            image.save(filename, "PNG")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Legacy texture export", str(exc))

    def build(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._confirm_discard():
            return
        if not changed_members(self.project):
            QMessageBox.information(self, "Build legacy PAK", "No staged edits to build.")
            return
        suggested = self.project["source_path"].stem + "-legacy-edited.pak"
        target, _ = QFileDialog.getSaveFileName(
            self, "Build separate UNSIGNED historical ZIP PAK", suggested,
            "Legacy PAK (*.pak)")
        if not target:
            return
        if not QMessageBox.question(
            self, "Build unsigned legacy PAK?",
            "This creates a new ordinary ZIP PAK for the 2012 engine. "
            "It is NOT compatible with Stage 2's signing or mod injector. "
            "Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            return
        self.build_button.setEnabled(False)
        self._build_progress = QProgressDialog(
            "Repacking and verifying historical PAK…", "", 0,
            len(self.project["entries"]), self)
        self._build_progress.setCancelButton(None)
        self._build_progress.setWindowModality(Qt.WindowModal)
        self._build_progress.setMinimumDuration(0)
        self._worker = _LegacyBuildThread(self.project, target, self)
        self._worker.progress.connect(
            lambda done, total: self._build_progress.setValue(done))
        self._worker.completed.connect(self._build_done)
        self._worker.failed.connect(self._build_failed)
        self._worker.finished.connect(self._build_progress.close)
        self._worker.finished.connect(lambda: self.build_button.setEnabled(True))
        self._worker.start()

    def _build_done(self, report):
        self.status.setText("Built unsigned PAK: " + report["output"])
        QMessageBox.information(
            self, "Legacy PAK built",
            f"Verified {report['members']} assets; {report['changes']} edited. "
            "The original PAK was not modified.\n\n" + report["output"])

    def _build_failed(self, error):
        QMessageBox.warning(self, "Legacy PAK build failed", error)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Legacy PAK build",
                                    "Wait for the verification/rebuild to finish.")
            event.ignore()
        elif self._confirm_discard():
            event.accept()
        else:
            event.ignore()
