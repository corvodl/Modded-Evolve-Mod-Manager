"""Qt profile-library dialog for the existing Stage 2 PAK manager.

Profiles organize references to signed user-built PAKs. Applying one calls the
manager's existing worker framework; no long-running work occurs in this UI.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFileDialog, QInputDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCheckBox
)

from mod_profiles import ModProfiles
from universal_stage import allowed_paks


class ModProfilesDialog(QDialog):
    def __init__(self, parent, root, stage_path, on_apply, suggested_pak=None):
        super().__init__(parent)
        self.store = ModProfiles(root)
        self.stage_path = Path(stage_path)
        self.on_apply = on_apply
        self.suggested_pak = suggested_pak
        self._filling = False
        self.setWindowTitle('Mod Profiles & Library')
        self.resize(910, 625)
        self.setMinimumSize(690, 480)
        root_layout = QVBoxLayout(self)
        root_layout.addWidget(QLabel(
            'Manage signed PAKs in profiles. Enabling two mods for one PAK is a conflict: '
            'combine their edits in one workspace first.'))
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel('Profile'))
        self.profiles = QComboBox()
        self.profiles.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.profiles, 1)
        for label, callback in (('New', self.new_profile),
                                ('Rename', self.rename_profile),
                                ('Delete', self.delete_profile)):
            toolbar.addWidget(self._button(label, callback))
        root_layout.addLayout(toolbar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ('Enabled', 'PAK target', 'Mod file', 'Verified SHA256'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._toggled)
        root_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.addWidget(self._button('Add signed PAK…', self.add_mod))
        actions.addWidget(self._button('Remove selected', self.remove_mod))
        actions.addStretch()
        actions.addWidget(self._button('Refresh', self.refresh))
        root_layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root_layout.addWidget(self.status)
        root_layout.addWidget(QLabel(
            'Applying a profile modifies only prepared staging copies and preserves '
            'a dated restoration backup for each changed PAK. It does not edit the '
            'installed game. Close both Evolve and its launcher first.'))
        bottom = QHBoxLayout()
        bottom.addWidget(self._button('Restore prior staged state', self.restore_baseline))
        bottom.addStretch()
        bottom.addWidget(self._button('Apply selected profile', self.apply_profile))
        bottom.addWidget(self._button('Close', self.close))
        root_layout.addLayout(bottom)
        self.reload()

    @staticmethod
    def _button(title, callback):
        button = QPushButton(title)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def _error(self, exc):
        QMessageBox.warning(self, 'Mod Profiles', str(exc))

    def _selected(self):
        profile_id = self.profiles.currentData()
        if not profile_id:
            raise ValueError('Create or select a profile first.')
        return profile_id

    def _current_profile(self):
        profile_id = self.profiles.currentData()
        return next((p for p in self.store.list_profiles()
                     if p['id'] == profile_id), None)

    def reload(self, select=None):
        current = select or self.profiles.currentData()
        try:
            profiles = self.store.list_profiles()
            self.profiles.blockSignals(True)
            self.profiles.clear()
            for profile in profiles:
                self.profiles.addItem(profile['name'], profile['id'])
            matches = [i for i, p in enumerate(profiles) if p['id'] == current]
            if matches:
                self.profiles.setCurrentIndex(matches[0])
            self.profiles.blockSignals(False)
            self.refresh()
        except Exception as exc:
            self._error(exc)

    def refresh(self, *_):
        if self._filling:
            return
        try:
            profile = self._current_profile()
            self._filling = True
            self.table.setRowCount(0)
            if profile:
                mods = profile['mods']
                self.table.setRowCount(len(mods))
                for i, mod in enumerate(mods):
                    checked = QTableWidgetItem()
                    checked.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    checked.setCheckState(Qt.Checked if mod['enabled'] else Qt.Unchecked)
                    checked.setData(Qt.UserRole, mod['id'])
                    self.table.setItem(i, 0, checked)
                    values = (mod['relative'], Path(mod['source']).name,
                              mod['sha256'][:16] + '…')
                    for j, value in enumerate(values, 1):
                        item = QTableWidgetItem(value)
                        item.setToolTip(mod['source'] if j == 2 else mod['sha256'])
                        self.table.setItem(i, j, item)
                self.table.resizeRowsToContents()
                _, problems = self.store.plan(profile['id'], self.stage_path,
                                               verify_files=False)
                self.status.setText(
                    '%d mods, %d enabled. %s' %
                    (len(mods), sum(bool(m['enabled']) for m in mods),
                     'Conflict: ' + '; '.join(problems[:3]) if problems
                     else 'No enabled-target conflicts detected.'))
            else:
                self.status.setText('Create a profile to organize your modified PAKs.')
            state = self.store.status()
            if state['in_progress'] or state['pending']:
                self.status.setText(self.status.text() +
                                    ' Previous apply was interrupted; restore or resume before changing profiles.')
            elif state['profile_id']:
                active = next((p['name'] for p in self.store.list_profiles()
                               if p['id'] == state['profile_id']), 'Earlier profile')
                self.status.setText(self.status.text() + ' Applied: ' + active + '.')
        except Exception as exc:
            self.status.setText('Profile status unavailable: ' + str(exc))
        finally:
            self._filling = False

    def new_profile(self):
        name, ok = QInputDialog.getText(self, 'New Profile', 'Profile name:')
        if not ok:
            return
        try:
            profile_id = self.store.create(name)
            self.reload(select=profile_id)
        except Exception as exc:
            self._error(exc)

    def rename_profile(self):
        try:
            profile = self._current_profile()
            if not profile:
                raise ValueError('Select a profile.')
            name, ok = QInputDialog.getText(self, 'Rename Profile', 'Name:',
                                            text=profile['name'])
            if ok:
                self.store.rename(profile['id'], name)
                self.reload(select=profile['id'])
        except Exception as exc:
            self._error(exc)

    def delete_profile(self):
        try:
            pid = self._selected()
            if QMessageBox.question(
                    self, 'Delete Profile',
                    'Delete the profile definition? Signed PAK files will be retained.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.store.delete(pid)
            self.reload()
        except Exception as exc:
            self._error(exc)

    def add_mod(self):
        try:
            profile_id = self._selected()
            suggested = str(self.suggested_pak) if self.suggested_pak else ''
            source, _ = QFileDialog.getOpenFileName(
                self, 'Add signed mod file', suggested, 'Signed PAK files (*.pak)')
            if not source:
                return
            name = Path(source).name.casefold()
            allowed = list(allowed_paks(self.stage_path).values())
            matches = [rel for rel in allowed if Path(rel).name.casefold() == name]
            if not matches:
                raise ValueError('The mod filename does not match any signed PAK in this setup.')
            if len(matches) == 1:
                relative = matches[0]
            else:
                relative, ok = QInputDialog.getItem(
                    self, 'Choose PAK target', 'Multiple signed archives share this name:',
                    matches, 0, False)
                if not ok:
                    return
            self.store.add_mod(profile_id, source, relative, self.stage_path)
            self.reload(select=profile_id)
        except Exception as exc:
            self._error(exc)

    def _selected_mod_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            raise ValueError('Select a mod in the list first.')
        return item.data(Qt.UserRole)

    def _toggled(self, item):
        if self._filling or item.column() != 0:
            return
        try:
            profile_id = self._selected()
            self.store.set_enabled(profile_id, item.data(Qt.UserRole),
                                   item.checkState() == Qt.Checked)
            self.refresh()
        except Exception as exc:
            self._error(exc)
            self.refresh()

    def remove_mod(self):
        try:
            profile_id = self._selected()
            self.store.remove_mod(profile_id, self._selected_mod_id())
            self.reload(select=profile_id)
        except Exception as exc:
            self._error(exc)

    def apply_profile(self):
        try:
            pid = self._selected()
            mods, errors = self.store.plan(pid, self.stage_path, verify_files=False)
            if errors:
                raise ValueError('Resolve these problems before applying:\n'
                                 + '\n'.join(errors))
            if QMessageBox.question(
                    self, 'Apply Mod Profile',
                    'Stage %d enabled signed PAKs? All profile-controlled previous '
                    'staging edits will first be restored using recorded backups. '
                    'Installed Evolve game files remain unchanged.' % len(mods),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.on_apply(pid)
            self.accept()
        except Exception as exc:
            self._error(exc)

    def restore_baseline(self):
        if QMessageBox.question(
                self, 'Restore previous staged state',
                'Undo only profile-managed staging changes using their backups? '
                'Existing manually staged mods are not reset.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            try:
                self.on_apply(None)
                self.accept()
            except Exception as exc:
                self._error(exc)
