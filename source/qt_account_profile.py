"""Developer-only offline profile inspector, isolated from account services."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from qt_ui_components import WorkspaceHeader

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QComboBox
)

from account_profile_snapshot import (
    build_copy, classification, compare_snapshots, export_new_copy,
    load_snapshot, read_catalog
)


class AccountProfileInspector(QDialog):
    """Load a readable backup, compare revisions, and export an offline test copy."""

    def __init__(self, parent=None, *, client=None):
        super().__init__(parent)
        self.setWindowTitle("Account profile • client details and offline copies")
        self.setMinimumSize(850, 570)
        self.resize(1040, 700)
        self.source = None
        self.profile = None
        self.other = None
        self.skin_names = set()
        self.perk_names = set()
        self._ownable_checks = {}
        self._progression = {}
        self.client = client
        # Inherit shared Qt design tokens; never force dark colors in Light mode.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(WorkspaceHeader(
            "Account Profile",
            "View local client details and compare readable test backups."))
        warning = QLabel(
            "The current TRSEVLVPC cache is encoded. This tool cannot edit server balances, "
            "grant permanent online ownership or import a copy into a live account. "
            "Never select a live profile as the export destination.")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        actions = QHBoxLayout()
        for label, callback in (
            ("Open readable backup…", self.open_backup),
            ("Compare backup…", self.compare_backup),
            ("Load skins catalog…", lambda: self.load_catalog("skin")),
            ("Load perks catalog…", lambda: self.load_catalog("perk")),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(callback)
            actions.addWidget(btn)
        layout.addLayout(actions)
        self.summary = QLabel("Offline backup: none selected. Client details are shown separately.")
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        owner = QWidget()
        own_lay = QVBoxLayout(owner)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter skin, perk, character or item ID…")
        self.search.textChanged.connect(self.filter_ownables)
        own_lay.addWidget(self.search)
        self.owned_table = QTableWidget(0, 2)
        self.owned_table.setHorizontalHeaderLabels(["Owned in test copy", "Ownable ID  ·  classification"])
        self.owned_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        own_lay.addWidget(self.owned_table)
        self.tabs.addTab(owner, "Unlocks / skins / perks")
        progression = QWidget()
        pg = QVBoxLayout(progression)
        self.progress_table = QTableWidget(0, 3)
        self.progress_table.setHorizontalHeaderLabels(["Category", "Level", "XP"])
        self.progress_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        pg.addWidget(self.progress_table)
        self.tabs.addTab(progression, "Progression")
        self.comparison = QLabel("Compare two readable snapshots to inspect additions and level changes.")
        self.comparison.setWordWrap(True)
        self.tabs.addTab(self.comparison, "Backup comparison")
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        description = QLabel('Automatically discovered local client details. Cached values may differ from the server. Missing fields are not inferred. Select a source to inspect it; refresh after the client updates your profile.')
        description.setWordWrap(True)
        detail_layout.addWidget(description)
        source_row = QHBoxLayout()
        self.client_sources = QComboBox()
        self.client_sources.setAccessibleName('Client profile source')
        source_row.addWidget(self.client_sources, 1)
        refresh = QPushButton('Refresh client details')
        refresh.clicked.connect(self.refresh_client_details)
        source_row.addWidget(refresh)
        detail_layout.addLayout(source_row)
        self.client_status = QLabel()
        self.client_status.setTextFormat(Qt.PlainText)
        self.client_status.setWordWrap(True)
        detail_layout.addWidget(self.client_status)
        self.client_table = QTableWidget(0, 2)
        self.client_table.setHorizontalHeaderLabels(['Profile detail', 'Local client value'])
        self.client_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.client_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.client_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        detail_layout.addWidget(self.client_table)
        self.tabs.addTab(details, 'Client details')
        from client_profile_details import profile_sources
        for label, path in profile_sources(client):
            self.client_sources.addItem(label + ' — ' + str(path), path)
        # Prefer an existing source, while keeping its location visible.
        for index in range(self.client_sources.count()):
            try:
                exists = self.client_sources.itemData(index).is_file()
            except OSError:
                exists = False
            if exists:
                self.client_sources.setCurrentIndex(index)
                break
        self.client_sources.currentIndexChanged.connect(self.refresh_client_details)
        self.refresh_client_details()
        self.tabs.setCurrentWidget(details)
        footer = QHBoxLayout()
        self.allow_changes = QCheckBox("Enable offline test-copy changes (never the active account)")
        self.allow_changes.toggled.connect(self._editing_changed)
        footer.addWidget(self.allow_changes)
        footer.addStretch()
        self.export_btn = QPushButton("Export NEW test JSON…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_copy)
        footer.addWidget(self.export_btn)
        layout.addLayout(footer)

    def refresh_client_details(self, *_):
        from client_profile_details import read_details
        self.client_table.setRowCount(0)
        path = self.client_sources.currentData()
        if path is None:
            self.client_status.setText('No configured client or local launcher profile location is available.')
            return
        try:
            rows = read_details(path)
            self.client_table.setRowCount(len(rows))
            for row, (label, value) in enumerate(rows):
                self.client_table.setItem(row, 0, QTableWidgetItem(label))
                self.client_table.setItem(row, 1, QTableWidgetItem(value))
            self.client_status.setText('Read-only local source: ' + str(path))
        except (OSError, ValueError) as exc:
            self.client_status.setText(str(path) + '\n' + str(exc))

    def _editing_changed(self, enabled):
        self.export_btn.setEnabled(bool(enabled and self.profile))
        for check in self._ownable_checks.values():
            check.setEnabled(enabled)
        self.progress_table.setEditTriggers(
            QTableWidget.AllEditTriggers if enabled else QTableWidget.NoEditTriggers)

    def open_backup(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose readable historical profile", str(Path.home()), "JSON files (*.json)")
        if not filename:
            return
        try:
            self.profile = load_snapshot(filename)
            self.source = Path(filename).resolve()
            self._rebuild()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Unsupported account profile", str(exc))

    def load_catalog(self, kind):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose ownables catalog", str(Path.home()), "Catalogs (*.txt)")
        if not filename:
            return
        try:
            names = set(read_catalog(filename))
            if kind == "skin":
                self.skin_names = names
            else:
                self.perk_names = names
            if self.profile:
                self._rebuild()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Invalid catalog", str(exc))

    def _rebuild(self):
        if self.profile is None:
            return
        owned = set(self.profile["Ownables"])
        all_names = sorted(owned | self.skin_names | self.perk_names, key=str.casefold)
        self.owned_table.setRowCount(len(all_names))
        self._ownable_checks.clear()
        self.owned_table.setUpdatesEnabled(False)
        for row, name in enumerate(all_names):
            check = QCheckBox()
            check.setChecked(name in owned)
            check.setEnabled(self.allow_changes.isChecked())
            self._ownable_checks[name] = check
            widget = QWidget()
            inner = QHBoxLayout(widget)
            inner.setContentsMargins(10, 0, 0, 0)
            inner.addWidget(check)
            inner.addStretch()
            self.owned_table.setCellWidget(row, 0, widget)
            item = QTableWidgetItem(name + "   ·   " + classification(name, self.skin_names, self.perk_names))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.owned_table.setItem(row, 1, item)
        self.owned_table.setUpdatesEnabled(True)
        rows = self.profile["XPProgression"]
        self.progress_table.setRowCount(len(rows))
        for i, value in enumerate(rows):
            for j, text in enumerate((value["Category"], value["Level"], value["XP"])):
                item = QTableWidgetItem(str(text))
                if j == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.progress_table.setItem(i, j, item)
        self._editing_changed(self.allow_changes.isChecked())
        self.filter_ownables(self.search.text())
        self.summary.setText(
            f"Source (read-only): {self.source.name}  •  "
            f"{len(owned):,} owned items  •  {len(rows)} progression entries  •  "
            f"{len(self.skin_names)} known skins/outfits  •  {len(self.perk_names)} known perks")
        
    def filter_ownables(self, text):
        needle = text.strip().casefold()
        for i in range(self.owned_table.rowCount()):
            cell = self.owned_table.item(i, 1)
            self.owned_table.setRowHidden(i, bool(needle and cell and needle not in cell.text().casefold()))

    def compare_backup(self):
        if not self.profile:
            QMessageBox.information(self, "Choose source", "Open a readable backup first.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose second readable backup", str(self.source.parent), "JSON files (*.json)")
        if not filename:
            return
        try:
            comp = compare_snapshots(self.profile, load_snapshot(filename))
            self.comparison.setText(
                f"Added: {len(comp['added'])}  |  Removed: {len(comp['removed'])}  |  "
                f"Progression changes: {len(comp['progression'])}\n\n"
                "Added:\n" + ", ".join(comp["added"][:100]) +
                "\n\nRemoved:\n" + ", ".join(comp["removed"][:100]) +
                "\n\nProgression:\n" +
                "\n".join(f"{k}: {before} → {after}" for k, before, after in comp["progression"][:100]))
            self.tabs.setCurrentIndex(2)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Compare failed", str(exc))

    def export_copy(self):
        if not self.profile or not self.allow_changes.isChecked():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export NEW offline test profile (never active userdata)",
            str(Path.home() / "evolve-profile-test-copy.json"), "JSON files (*.json)")
        if not filename:
            return
        try:
            progression = []
            for i in range(self.progress_table.rowCount()):
                original = self.profile["XPProgression"][i]
                category = original["Category"]
                level = int(self.progress_table.item(i, 1).text())
                xp = int(self.progress_table.item(i, 2).text())
                progression.append((category, level, xp))
            owned = [name for name, check in self._ownable_checks.items() if check.isChecked()]
            updated = build_copy(self.profile, owned, progression)
            output = export_new_copy(updated, filename)
            QMessageBox.information(
                self, "Offline copy exported",
                f"Saved {output.name}. Original remains unchanged. "
                "This is not imported to the launcher or account service.")
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
