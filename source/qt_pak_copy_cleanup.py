"""Settings > Cleanup Duplicate PAK Copies: no background deletions."""
from __future__ import annotations
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QFile
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QAbstractItemView, QMessageBox, QInputDialog)

from pak_copy_cleanup import scan_duplicate_paks, validate_recycle


class _CleanupSignals(QObject):
    finished = Signal(str, object, object)


class DuplicatePAKCleanupDialog(QDialog):
    def __init__(self, parent, game_root, swap_root, journal_roots=()):
        super().__init__(parent)
        self.game = Path(game_root).resolve()
        self.swap = Path(swap_root).resolve()
        self.journal_roots = tuple(Path(root) for root in journal_roots)
        self.rows = ()
        self.pending = False
        self.signals = _CleanupSignals(self)
        self.signals.finished.connect(self._finished)
        self.setWindowTitle("Review duplicate game PAK copies")
        self.resize(1050, 600)
        lay = QVBoxLayout(self)
        desc = QLabel(
            "Read-only inventory first. Active .pak files, .customkey-original, "
            "prepared/incomplete copies and unknown backup suffixes are protected. "
            "Only SHA-256-identical legacy copies may be selected for Recycle Bin "
            "after confirming there is no active swap or known recovery reference.")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        self.summary = QLabel("Not scanned")
        lay.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("duplicatePAKReview")
        self.table.setHorizontalHeaderLabels((
            "PAK copy", "Size (MiB)", "Classification", "Cleanup", "Explanation"))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        self.table.itemSelectionChanged.connect(self._update_controls)
        bar = QHBoxLayout()
        self.scan_button = QPushButton("Scan / refresh")
        self.scan_button.clicked.connect(self.scan)
        bar.addWidget(self.scan_button)
        self.recycle_button = QPushButton("Move selected verified duplicates to Recycle Bin…")
        self.recycle_button.setEnabled(False)
        self.recycle_button.clicked.connect(self.recycle)
        bar.addWidget(self.recycle_button)
        bar.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        bar.addWidget(close_button)
        lay.addLayout(bar)
        self.scan()

    def _start(self, kind, callback):
        if self.pending:
            return
        self.pending = True
        self._update_controls()
        self.scan_button.setEnabled(False)
        self.summary.setText("Scanning and hashing PAK copies…"
                             if kind == "scan" else "Rechecking and recycling selected files…")

        def work():
            try:
                result = callback()
                self.signals.finished.emit(kind, result, None)
            except Exception as exc:
                self.signals.finished.emit(kind, None, str(exc))
        threading.Thread(target=work, daemon=True).start()

    def scan(self):
        self._start("scan", lambda: scan_duplicate_paks(
            self.game, journal_roots=self.journal_roots))

    def _selected(self):
        indexes = sorted({item.row() for item in self.table.selectedItems()})
        return tuple(self.rows[i] for i in indexes
                     if i < len(self.rows) and self.rows[i].reclaimable)

    def _update_controls(self):
        self.recycle_button.setEnabled(
            not self.pending and bool(self._selected()))
        self.scan_button.setEnabled(not self.pending)

    def recycle(self):
        selected = self._selected()
        if not selected:
            return
        total = sum(row.bytes for row in selected)
        reply, ok = QInputDialog.getText(
            self, "Confirm duplicate PAK cleanup",
            "Selected: %d SHA-256-identical redundant copy/copies (%.1f MiB).\n"
            "Only copies matching the installed .pak are eligible.\n"
            "These may have been created for old recovery workflows; manifests "
            "outside known manager folders cannot be detected.\n"
            "Your live PAKs and .customkey-original backups will NEVER be moved.\n"
            "Close the game and launcher. Type RECYCLE to continue:" %
            (len(selected), total / (1024 * 1024)))
        if not ok or reply != "RECYCLE":
            return

        def job():
            paths = validate_recycle(
                self.game, self.swap, selected, journal_roots=self.journal_roots)
            moved = []
            for file in paths:
                # Revalidate selection immediately before relocating each file.
                validate_recycle(
                    self.game, self.swap,
                    (next(r for r in selected if r.path == file),),
                    journal_roots=self.journal_roots)
                if not QFile.moveToTrash(str(file)):
                    raise OSError(
                        "Could not move to Recycle Bin: " + str(file) +
                        ". Previously moved files are in the Recycle Bin.")
                moved.append(file)
            return tuple(moved)
        self._start("recycle", job)

    def _finished(self, kind, result, error):
        self.pending = False
        self.scan_button.setEnabled(True)
        if error is not None:
            self.summary.setText("Unable to complete: " + str(error)[:240])
            QMessageBox.warning(self, "PAK cleanup", str(error)[:1200])
            self._update_controls()
            return
        if kind == "recycle":
            count = len(result)
            QMessageBox.information(
                self, "Moved to Recycle Bin",
                str(count) + " verified duplicate copy/copies moved to the "
                "Windows Recycle Bin. Empty the Recycle Bin only when you "
                "no longer need these historical backups.")
            self.scan()
            return
        self.rows = result
        self.table.setRowCount(len(result))
        for i, row in enumerate(result):
            values = (row.path.name,
                      "%.2f" % (row.bytes / 1048576.0),
                      row.kind,
                      "Verified duplicate" if row.reclaimable else "Protected",
                      row.detail)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(row.path))
                self.table.setItem(i, column, item)
        candidates = [r for r in result if r.reclaimable]
        self.summary.setText(
            "%d backup-like files reviewed · %d exact duplicate candidates · "
            "%.1f MiB potentially recoverable after Recycle Bin is emptied" %
            (len(result), len(candidates),
             sum(x.bytes for x in candidates) / 1048576))
        self.table.clearSelection()
        self._update_controls()
