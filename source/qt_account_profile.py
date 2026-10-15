"""Developer-only offline profile inspector, isolated from account services."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView
)

from account_profile_snapshot import (
    build_copy, classification, compare_snapshots, export_new_copy,
    load_snapshot, read_catalog
)


class AccountProfileInspector(QDialog):
    """Load a readable backup, compare revisions, and export an offline test copy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Account profile • offline inspector")
        self.setMinimumSize(850, 570)
        self.resize(1040, 700)
        self.source = None
        self.profile = None
        self.other = None
        self.skin_names = set()
        self.perk_names = set()
        self._ownable_checks = {}
        self._progression = {}
        self.setStyleSheet("QDialog {background:#202126; color:#e6e6e9;} "
                           "QTableWidget,QLineEdit {background:#292a30; color:#e6e6e9;} "
                           "QPushButton {padding:6px;}")
        layout = QVBoxLayout(self)
        title = QLabel("Readable account profile backups • local test workspace")
        title.setStyleSheet("font-size:17px;font-weight:600")
        layout.addWidget(title)
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
        self.summary = QLabel("No readable backup selected.")
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
