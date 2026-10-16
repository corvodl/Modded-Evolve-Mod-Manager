"""Compact Game Storage settings card: measured breakdown, review-only cleanup.

Disk and metadata walks run in worker threads. No startup scans, moves,
automatic cleanup, or unsafe replacement of an active stage/swap pair.
"""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QHBoxLayout, QVBoxLayout,
    QFrame, QFileDialog, QMessageBox, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QDialogButtonBox
)

from qt_ui_components import DisclosurePanel
from game_storage import (
    CATEGORIES, data_location, measure_storage, cleanup_candidates,
    delete_old_setup, validate_storage_root
)

SEGMENTS = {
    "Active setup": "#DC3648",
    "Old setups": "#7285B6",
    "Projects": "#B99B65",
    "Asset Library": "#54A6A0",
    "Other": "#777988",
}


def _size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MiB"
    return f"{n / 1024**3:.2f} GiB"


class _Signals(QObject):
    completed = Signal(str, object, str)


class _Work(QRunnable):
    def __init__(self, task, job, signals):
        super().__init__()
        self.task = task
        self.job = job
        self.signals = signals

    def run(self):
        try:
            value, error = self.job(), ""
        except Exception as exc:
            value, error = None, f"{type(exc).__name__}: {exc}"
        from shiboken6 import isValid
        try:
            if isValid(self.signals):
                self.signals.completed.emit(self.task, value, error)
        except RuntimeError:
            pass


class StorageReview(QDialog):
    def __init__(self, parent, entries, on_confirm):
        super().__init__(parent)
        self.entries = tuple(entries)
        self.on_confirm = on_confirm
        self.setWindowTitle("Review Old Setup Storage")
        self.setObjectName("gameStorageCleanupReview")
        self.resize(805, 460)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Only verified, unused GameSets are selectable. No stage, swap, "
            "project or recovery records are removed without explicit confirmation. "
            "Deleted setup folders cannot be recovered through Undo.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        table = QTableWidget(len(entries), 4, self)
        self.table = table
        table.setObjectName("gameStorageCleanupTable")
        table.setHorizontalHeaderLabels(("Remove", "Setup folder", "Size", "Safety"))
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for row, (folder, size, reason) in enumerate(entries):
            checkbox = QTableWidgetItem()
            checkbox.setData(Qt.UserRole, str(folder))
            checkbox.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable if reason is None
                              else Qt.ItemIsEnabled)
            checkbox.setCheckState(Qt.Unchecked)
            table.setItem(row, 0, checkbox)
            for col, value in enumerate((folder.name, _size(size), reason or "Eligible")):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(folder) if col == 1 else str(value))
                table.setItem(row, col + 1, item)
        layout.addWidget(table)
        bottom = QHBoxLayout()
        self.remove_button = QPushButton("Remove Selected…")
        self.remove_button.setObjectName("gameStorageRemoveSelected")
        self.remove_button.clicked.connect(self._remove)
        bottom.addWidget(self.remove_button)
        bottom.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    def _remove(self):
        selected = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.flags() & Qt.ItemIsUserCheckable and item.checkState() == Qt.Checked:
                selected.append(Path(item.data(Qt.UserRole)))
        if not selected:
            QMessageBox.information(self, "Old Setup Cleanup",
                                    "Choose one or more eligible setups first.")
            return
        if QMessageBox.question(
                self, "Permanently remove old setups?",
                "Remove these verified old setup folders?\n\n" +
                "\n".join(str(path) for path in selected) +
                "\n\nThis cannot be undone. Files will be rechecked before deletion.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) == QMessageBox.Yes:
            self.on_confirm(tuple(selected))
            self.accept()


class GameStorageSettings(QWidget):
    def __init__(self, manager, fields, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.fields = fields
        self._working = False
        self._scanned = False
        self._signals = _Signals(self)
        self._signals.completed.connect(self._complete)
        self._thread_pool = QThreadPool.globalInstance()
        self.setObjectName("gameStorageSettings")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        path_row = QHBoxLayout()
        location = QLabel("Mod Data Location")
        path_row.addWidget(location)
        self.root_input = QLineEdit(manager.game_storage_root)
        self.root_input.setObjectName("gameStorageRoot")
        self.root_input.setToolTip("Root for NEW setups. Existing staged PAKs and recovery journals are not moved.")
        path_row.addWidget(self.root_input, 1)
        self.root_button = QPushButton("Browse…")
        self.root_button.setObjectName("gameStorageRootBrowse")
        self.root_button.clicked.connect(self._browse)
        path_row.addWidget(self.root_button)
        root.addLayout(path_row)
        explanation = QLabel(
            "Signing stage and launch recovery folders are managed together. "
            "Changing this location affects FUTURE setups only; it does not "
            "move or replace your active signed archives.")
        explanation.setObjectName("muted")
        explanation.setWordWrap(True)
        root.addWidget(explanation)
        self.active_label = QLabel("Active setup: " +
                                   str(data_location(manager.stage_path, manager.swap_path,
                                                     manager.game_storage_root)))
        self.active_label.setObjectName("muted")
        self.active_label.setWordWrap(True)
        root.addWidget(self.active_label)

        panel = QFrame(self)
        panel.setObjectName("gameStorageBreakdownCard")
        box = QVBoxLayout(panel)
        title = QHBoxLayout()
        title.addWidget(QLabel("Storage Breakdown"))
        self.total_label = QLabel("Not analyzed")
        self.total_label.setObjectName("muted")
        title.addStretch()
        title.addWidget(self.total_label)
        box.addLayout(title)
        self.bar = QWidget(panel)
        self.bar.setObjectName("gameStorageUsageBar")
        self.bar.setFixedHeight(13)
        self.bar_layout = QHBoxLayout(self.bar)
        self.bar_layout.setContentsMargins(0, 0, 0, 0)
        self.bar_layout.setSpacing(1)
        self.bars = {}
        for category in CATEGORIES:
            segment = QFrame(self.bar)
            segment.setObjectName("gameStorageSegment" + category.replace(" ", ""))
            segment.setStyleSheet("background-color: " + SEGMENTS[category] + "; border: 0;")
            self.bar_layout.addWidget(segment, 1)
            self.bars[category] = segment
        box.addWidget(self.bar)
        self.legend = {}
        for index in (0, 1):
            row = QHBoxLayout()
            for key in CATEGORIES[index*3:(index+1)*3]:
                dot = QLabel("■")
                dot.setStyleSheet("color: " + SEGMENTS[key] + ";")
                row.addWidget(dot)
                label = QLabel(key + ": —")
                label.setObjectName("gameStorageLegend" + key.replace(" ", ""))
                self.legend[key] = label
                row.addWidget(label)
                row.addStretch()
            box.addLayout(row)
        buttons = QHBoxLayout()
        self.scan_button = QPushButton("Analyze Storage")
        self.scan_button.setObjectName("gameStorageAnalyze")
        self.scan_button.clicked.connect(self.refresh)
        buttons.addWidget(self.scan_button)
        self.review_button = QPushButton("Review Storage & Cleanup…")
        self.review_button.setObjectName("gameStorageReviewCleanup")
        self.review_button.clicked.connect(self.review_cleanup)
        buttons.addWidget(self.review_button)
        buttons.addStretch()
        box.addLayout(buttons)
        root.addWidget(panel)

        self.advanced = DisclosurePanel("Advanced folder paths", expanded=False, parent=self)
        self.advanced.setObjectName("gameStorageAdvanced")
        root.addWidget(self.advanced)
        for label, name in (("Signing stage", "stage"), ("Launch/swap folder", "swap"),
                            ("Projects", "projects")):
            line = QHBoxLayout()
            line.addWidget(QLabel(label))
            line.addWidget(fields[name], 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, key=name: self.manager.browse_folder(key))
            line.addWidget(browse)
            self.advanced.addLayout(line)
        self.advanced.addWidget(QLabel(
            "Only change advanced paths if you understand the existing signing "
            "and swap journals. Saving does not migrate old files."))

    def _browse(self):
        selected = QFileDialog.getExistingDirectory(
            self, "Choose storage root for future setups", self.root_input.text())
        if selected:
            self.root_input.setText(selected)

    def _args(self):
        from app_runtime import DATA_HOME
        root = validate_storage_root(
            self.root_input.text(),
            game=self.manager.game_root or self.manager._find_game_root())
        return (DATA_HOME, root, self.manager.stage_path, self.manager.swap_path,
                self.manager.projects_path)

    def refresh(self):
        if self._working:
            return
        try:
            args = self._args()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Game Storage", str(error))
            return
        self._submit("scan", lambda: measure_storage(*args))

    def _submit(self, task, callback):
        if self._working:
            return
        self._working = True
        self.scan_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.total_label.setText("Checking storage…")
        self._thread_pool.start(_Work(task, callback, self._signals))

    def _complete(self, task, value, error):
        self._working = False
        self.scan_button.setEnabled(True)
        self.review_button.setEnabled(True)
        if error:
            self.total_label.setText("Storage check failed")
            QMessageBox.warning(self, "Game Storage", error)
            return
        if task == "scan":
            self._scanned = True
            self.total_label.setText(_size(value["total"]))
            total = value["total"] or 1
            for key in CATEGORIES:
                count = value["bytes"][key]
                self.legend[key].setText(key + ": " + _size(count))
                self.bar_layout.setStretchFactor(self.bars[key],
                                                 max(0, round(count * 1000 / total)))
            return
        if task == "review":
            dialog = StorageReview(self, value, self._remove_selected)
            dialog.exec()
            return
        if task == "delete":
            self.total_label.setText("Removed " + str(value) + " old setup(s)")
            self.refresh()

    def review_cleanup(self):
        if self._working:
            return
        try:
            _data, root, stage, swap, projects = self._args()
            from app_runtime import DATA_HOME
            profiles = DATA_HOME / "Profiles"
            game = self.manager.game_root or self.manager._find_game_root()
            self._submit("review", lambda: cleanup_candidates(
                root, stage, swap, projects, profiles, game_root=game))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Game Storage", str(exc))

    def _remove_selected(self, paths):
        try:
            _data, root, stage, swap, projects = self._args()
            from app_runtime import DATA_HOME
            profiles = DATA_HOME / "Profiles"
            game = self.manager.game_root or self.manager._find_game_root()
            def remove():
                for index, path in enumerate(paths):
                    delete_old_setup(path, root, stage, swap, projects, profiles,
                                     game_root=game)
                return len(paths)
            self._submit("delete", remove)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Game Storage", str(exc))
