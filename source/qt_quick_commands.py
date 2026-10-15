"""Keyboard-first command palette. No new sidebar item or background index."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel
)


class QuickCommandPalette(QDialog):
    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager
        self.setObjectName("managerQuickCommands")
        self.setWindowTitle("Quick Actions")
        self.resize(510, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Search common manager commands · Enter to run · Esc to close"))
        self.search = QLineEdit()
        self.search.setObjectName("quickCommandSearch")
        self.search.setPlaceholderText("Install Mod, Mods Library, restore, project…")
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setObjectName("quickCommandResults")
        layout.addWidget(self.results)
        # Only public user-visible commands; never Developer switches, online
        # firewall overrides, or privileged operations without confirmation.
        self.commands = (
            ("Install Mod…", manager.open_easy_mod_installer),
            ("Installed Mods / Profiles…", manager.open_mod_profiles),
            ("Undo Profile Staging…", manager.undo_mod_profile),
            ("Open Modding", lambda: manager.open_page(manager.section_index("Modding"))),
            ("Open Asset Library", lambda: manager.open_page(manager.section_index("Asset Library"))),
            ("Open Settings", lambda: manager.open_page(manager.section_index("Settings"))),
            ("Open Home", lambda: manager.open_page(manager.section_index("Home"))),
            ("Open Projects", manager.open_project_dashboard),
            ("Check for Updates", manager.check_updates),
            ("Restore Game Files…", manager.restore),
            ("Show Activity", manager.toggle_activity),
        )
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.invoke)
        self.results.itemActivated.connect(lambda _item: self.invoke())
        self.refresh("")
        self.search.setFocus(Qt.OtherFocusReason)

    def refresh(self, query):
        token = str(query).casefold().strip()
        self.results.clear()
        for position, (label, _) in enumerate(self.commands):
            if not token or all(t in label.casefold() for t in token.split()):
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, position)
                self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def invoke(self):
        selected = self.results.currentItem()
        if selected is None:
            return
        index = selected.data(Qt.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self.commands):
            return
        callback = self.commands[index][1]
        self.accept()
        callback()
