"""Searchable, read-only event registry and offline fixture workbench."""
from __future__ import annotations
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QPlainTextEdit, QFileDialog, QMessageBox, QFormLayout,
    QScrollArea, QSplitter, QAbstractItemView)

from developer_event_catalog import load_catalog, filter_catalog, make_fixture, inspect_capture


class GameEventsPanel(QWidget):
    """Available only inside the already-locked Developer tab.

    Local fixture inspection is read-only. A separate authenticated companion
    may handle live offline activation when it explicitly advertises support.
    """
    def __init__(self, parent=None, game_root_provider=None):
        super().__init__(parent)
        self.events = load_catalog()
        self.selected = None
        self.inputs = {}
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "V3 event catalog — inspection and OFFLINE fixture export only. "
            "Listing an event does not mean the manager can activate it. "
            "Networked describes the game's metadata, not this panel's behavior."))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search event, ID, category or field…")
        filters.addWidget(self.search, 2)
        self.category = QComboBox()
        self.category.addItems(["All categories"] +
            sorted({event.category for event in self.events}))
        filters.addWidget(self.category, 1)
        self.scope = QComboBox()
        self.scope.addItems(["All seats / local", "local", "all seats"])
        filters.addWidget(self.scope)
        self.recorder = QComboBox()
        self.recorder.addItems(["All events", "Kept by recorder",
                                "Not kept", "Uncertain ID"])
        filters.addWidget(self.recorder)
        root.addLayout(filters)
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Event", "ID", "Scope", "Recorded", "Category"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        splitter.addWidget(self.table)
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        self.summary = QLabel("Select an event to inspect its schema.")
        self.summary.setWordWrap(True)
        detail_layout.addWidget(self.summary)
        self.fields_scroll = QScrollArea()
        self.fields_scroll.setWidgetResizable(True)
        self.fields_widget = QWidget()
        self.field_form = QFormLayout(self.fields_widget)
        self.fields_scroll.setWidget(self.fields_widget)
        detail_layout.addWidget(self.fields_scroll, 1)
        actions = QHBoxLayout()
        make = QPushButton("Preview local fixture")
        make.clicked.connect(self.preview_fixture)
        actions.addWidget(make)
        save = QPushButton("Export JSON fixture…")
        save.clicked.connect(self.export_fixture)
        actions.addWidget(save)
        detail_layout.addLayout(actions)
        inspect = QPushButton("Inspect local event capture (JSON / JSONL)…")
        inspect.clicked.connect(self.open_capture)
        detail_layout.addWidget(inspect)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Offline fixture preview or capture summary.")
        detail_layout.addWidget(self.preview, 1)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)
        self.search.textChanged.connect(self.refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.recorder.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self.show_selected)
        from qt_live_event_controls import LiveEventControls
        self.live_controls = LiveEventControls(
            game_root_provider=game_root_provider,
            current_event=lambda: self.selected,
            current_fields=lambda: {name: widget.text() for name, widget in self.inputs.items()},
            output=self.preview, parent=self)
        root.insertWidget(2, self.live_controls)
        self.refresh()

    def refresh(self, *_):
        self.shown = filter_catalog(
            self.search.text(), self.category.currentText(),
            self.scope.currentText(), self.recorder.currentText())
        self.table.setRowCount(len(self.shown))
        for index, event in enumerate(self.shown):
            for col, value in enumerate((event.name, event.event_id,
                    event.scope, "Yes" if event.kept else "No",
                    event.category)):
                self.table.setItem(index, col, QTableWidgetItem(value))
        self.summary.setText(
            f"{len(self.shown)} of {len(self.events)} documented events. "
            "Entries with '?' have unverified IDs.")

    def show_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.shown):
            self.selected = None
            if hasattr(self, 'live_controls'):
                self.live_controls.update_selection()
            return
        event = self.shown[row]
        self.selected = event
        if hasattr(self, "live_controls"):
            self.live_controls.update_selection()
        self.summary.setText(
            f"{event.name}   {event.event_id}\n"
            f"Category: {event.category}\n"
            f"Scope: {event.scope}; recorder kept: {event.kept}\n"
            f"Exe build: 870227EC\n"
            f"Send sites (RVA): {', '.join(event.send_sites) or 'not found'}\n"
            f"Parent functions (RVA): {', '.join(event.functions) or 'not found'}\n"
            f"Observed host/clients: {event.counts or 'not available'}\n"
            f"Runtime dispatch: requires verified in-game companion handler")
        while self.field_form.rowCount():
            self.field_form.removeRow(0)
        self.inputs = {}
        for name, field_type in event.fields:
            edit = QLineEdit()
            edit.setPlaceholderText(
                "entity reference (not automatically resolved)"
                if field_type == "entity" else field_type)
            self.field_form.addRow(f"{name} ({field_type})", edit)
            self.inputs[name] = edit
        if not event.fields:
            self.field_form.addRow(QLabel("No event parameters"))

    def _fixture(self):
        if self.selected is None:
            raise ValueError("Select an event first")
        return make_fixture(self.selected,
                {key: widget.text() for key, widget in self.inputs.items()})

    def preview_fixture(self):
        try:
            self.preview.setPlainText(json.dumps(self._fixture(), indent=2))
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Fixture parameters", str(exc))

    def export_fixture(self):
        try:
            fixture = self._fixture()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Fixture parameters", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save offline test fixture", self.selected.name + ".json",
            "JSON files (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(fixture, indent=2),
                                      encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def open_capture(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open local event capture", "",
            "Event captures (*.json *.jsonl);;All files (*)")
        if not path:
            return
        try:
            source = Path(path)
            if source.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("Capture exceeds 8 MiB")
            result = inspect_capture(source.read_text("utf-8"))
            self.preview.setPlainText(json.dumps(result, indent=2))
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "Capture import failed", str(exc))

    def stop(self):
        if hasattr(self, 'live_controls'):
            self.live_controls.stop()
