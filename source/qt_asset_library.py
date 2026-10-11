"""Qt Settings UI and background importer for the permanent extracted Asset Library."""
from __future__ import annotations
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QFileDialog, QMessageBox,
    QAbstractItemView, QProgressBar)

from asset_library import (LIBRARY_HOME, import_pak, installed_defaults,
                           imported_archives, verify_library, remove_archive,
                           extracted_index)


class AssetLibraryController(QObject):
    """One background operation at a time, surviving Settings dialog closure."""
    progress = Signal(str, int, int)
    finished = Signal(str, object, object)
    started = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.status = "Asset Library ready"
        self._cancel = threading.Event()
        self._mutex = threading.RLock()
        self.finished.connect(self._on_finished)
        self.progress.connect(self._on_progress)

    def _on_progress(self, name, current, total):
        self.status = "%s • %d / %d" % (name, current, total)

    def _on_finished(self, action, result, error):
        with self._mutex:
            self.busy = False
        self.status = (str(error) if error is not None else
                       "Asset Library " + action + " completed")

    def _start(self, action, callback):
        with self._mutex:
            if self.busy:
                return False
            self.busy = True
            self._cancel.clear()
        self.status = "Starting " + action + "…"
        self.started.emit(action)

        def work():
            try:
                data = callback()
                self.finished.emit(action, data, None)
            except Exception as exc:
                self.finished.emit(action, None, str(exc))
        threading.Thread(target=work, daemon=True,
                         name="AssetLibrary-" + action).start()
        return True

    def import_files(self, paths, stage, *, automatic=False, extra_keys=()):
        files = tuple(Path(p) for p in paths)
        if not files:
            return False

        def work():
            results = []
            for i, path in enumerate(files, 1):
                if self._cancel.is_set():
                    break
                self.progress.emit("Importing %d/%d: %s" %
                                   (i, len(files), path.name), 0, 1)
                try:
                    result = import_pak(
                        path, stage=stage, extra_keys=extra_keys,
                        cancelled=self._cancel.is_set,
                        progress=lambda name, done, total:
                            self.progress.emit(name, done, total))
                    results.append(result)
                except Exception as exc:
                    results.append({"name": path.name, "status": "error",
                                    "error": str(exc)[:400]})
            return results

        return self._start("automatic-import" if automatic else "import", work)

    def verify(self):
        return self._start(
            "verification",
            lambda: verify_library(cancelled=self._cancel.is_set,
                                   progress=lambda name, done, total:
                                       self.progress.emit(name, done, total)))

    def remove(self, identifier):
        return self._start("removal", lambda: remove_archive(identifier))

    def cancel(self):
        self._cancel.set()


class AssetLibraryDialog(QDialog):
    def __init__(self, parent, controller, stage, game=None):
        super().__init__(parent)
        self.controller = controller
        self.stage = Path(stage)
        self.game = Path(game) if game else None
        self.setWindowTitle("Extracted PAK Asset Library")
        self.resize(990, 560)
        lay = QVBoxLayout(self)
        info = QLabel(
            "PAKs are verified, fully extracted ONCE, and kept as ordinary "
            "read-only reference files. You may add any supported Evolve .pak. "
            "Character textures, skeletons, and CAF animations can then load "
            "without re-extracting the original archive.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.location = QLabel("Library: " + str(LIBRARY_HOME))
        self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.location)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Archive", "Files", "Extracted Size", "Warnings", "Source"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        controls = QHBoxLayout()
        self.add_button = QPushButton("Add PAKs…")
        self.add_button.clicked.connect(self.add_paks)
        controls.addWidget(self.add_button)
        self.key_button = QPushButton("Add with Public Key…")
        self.key_button.setToolTip(
            "Import signed Evolve PAKs that use a different public verification key.")
        self.key_button.clicked.connect(self.add_with_key)
        controls.addWidget(self.key_button)
        self.default_button = QPushButton("Import shared + animation PAKs")
        self.default_button.clicked.connect(self.import_defaults)
        controls.addWidget(self.default_button)
        self.verify_button = QPushButton("Verify Library")
        self.verify_button.clicked.connect(self.verify)
        controls.addWidget(self.verify_button)
        self.remove_button = QPushButton("Remove Selected…")
        self.remove_button.clicked.connect(self.remove_selected)
        controls.addWidget(self.remove_button)
        self.cancel_button = QPushButton("Cancel Import")
        self.cancel_button.clicked.connect(self.controller.cancel)
        controls.addWidget(self.cancel_button)
        controls.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        controls.addWidget(close)
        lay.addLayout(controls)

        controller.progress.connect(self._progress)
        controller.finished.connect(self._finished)
        controller.started.connect(self._running)
        self.table.itemSelectionChanged.connect(self._sync)
        self.refresh()
        self._sync()

    def refresh(self):
        archives = imported_archives()
        self.table.setRowCount(len(archives))
        self.rows = archives
        for row, data in enumerate(archives):
            values = (data["name"], str(data["entries"]),
                      "%.1f MiB" % (data["bytes"] / 1048576),
                      str(data["warnings"]), data["source"])
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(data["source"])
                self.table.setItem(row, col, cell)
        size = sum(item["bytes"] for item in archives)
        self.status.setText(
            "%d extracted PAKs • %.1f MiB in Asset Library • %s" %
            (len(archives), size / 1048576, self.controller.status))
        self._sync()

    def _sync(self):
        running = self.controller.busy
        for button in (self.add_button, self.key_button,
                       self.default_button, self.verify_button, self.remove_button):
            button.setEnabled(not running)
        self.remove_button.setEnabled(not running and bool(self.table.selectedItems()))
        self.cancel_button.setEnabled(running)

    def add_paks(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Select Evolve PAKs to add",
            str(self.game or Path.home()), "Evolve PAKs (*.pak);;All files (*)")
        if names:
            self.controller.import_files(names, self.stage)

    def add_with_key(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Select Evolve PAKs to add",
            str(self.game or Path.home()), "Evolve PAKs (*.pak)")
        if not names:
            return
        key, _ = QFileDialog.getOpenFileName(
            self, "Choose RSA public verification key (never a private key)",
            str(self.game or Path.home()), "RSA Public Key (*.bin *.der)")
        if key:
            self.controller.import_files(names, self.stage, extra_keys=(key,))

    def import_defaults(self):
        files = installed_defaults(self.game)
        if not files:
            QMessageBox.warning(self, "Game not found",
                "Select an installed EvolveGame with the shared / animation PAKs.")
            return
        self.controller.import_files(files, self.stage)

    def verify(self):
        self.controller.verify()

    def remove_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            return
        record = self.rows[row]
        if QMessageBox.question(
            self, "Remove extracted archive?",
            "Remove extracted reference files for " + record["name"] +
            "?\nThis does NOT remove the original PAK or game assets. "
            "Other previews will no longer see these references.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.controller.remove(record["id"])

    def _running(self, action):
        self.status.setText("Asset Library " + action + " in progress…")
        self.progress.setValue(0)
        self._sync()

    def _progress(self, name, current, total):
        self.status.setText(name + " • %d / %d files" % (current, total))
        self.progress.setValue(int(current * 100 / max(1, total)))

    def _finished(self, action, result, error):
        self.refresh()
        if error:
            QMessageBox.warning(self, "Asset Library", str(error)[:1200])
        elif isinstance(result, list):
            failures = [item for item in result if item.get("status") == "error"]
            if failures:
                QMessageBox.warning(self, "Some PAKs were not imported",
                                    "\n".join(item["name"] + ": " + item["error"]
                                              for item in failures[:8])[:1500])
        elif isinstance(result, dict) and result.get("corrupt", []) + result.get("missing", []):
            QMessageBox.warning(self, "Asset Library integrity",
                "%d damaged and %d missing files. Remove and re-import "
                "the affected archives to repair them." %
                (len(result["corrupt"]), len(result["missing"])))
        self._sync()
