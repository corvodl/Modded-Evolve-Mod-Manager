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
    QHeaderView, QAbstractItemView, QCheckBox, QPlainTextEdit,
    QApplication
)

from mod_profiles import ModProfiles
from universal_stage import allowed_paks
from mod_install_receipts import list_receipts
from mod_install_safety import verify_collection, repair_mod_source
from qt_background_exports import begin_file_operation, exports_busy


class ModProfilesDialog(QDialog):
    def __init__(self, parent, root, stage_path, on_apply, suggested_pak=None):
        super().__init__(parent)
        self.store = ModProfiles(root)
        self.stage_path = Path(stage_path)
        self.on_apply = on_apply
        self.suggested_pak = suggested_pak
        self._filling = False
        self.setWindowTitle('Installed Mods — Library and Collections')
        self.resize(910, 625)
        self.setMinimumSize(690, 480)
        root_layout = QVBoxLayout(self)
        root_layout.addWidget(QLabel(
            'Manage signed PAKs in profiles. Enabling two mods for one PAK is a conflict even when reordered: '
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
        tools = QHBoxLayout()
        for label, callback in (
                ('Duplicate', self.duplicate_profile),
                ('Import Shared Profile…', self.import_shared),
                ('Export Profile…', self.export_shared)):
            tools.addWidget(self._button(label,callback))
        self.verify_button = self._button('Verify & Repair…', self.verify_and_repair)
        self.verify_button.setObjectName('verifyInstalledMods')
        tools.addWidget(self.verify_button)
        tools.addWidget(self._button('Installation History…', self.show_receipts))
        tools.addStretch()
        root_layout.addLayout(tools)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("installedModsTable")
        self.table.setHorizontalHeaderLabels(
            ('Enabled', 'Mod', 'Target PAK', 'Compatibility', 'State'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._toggled)
        root_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.addWidget(self._button('Add signed PAK…', self.add_mod))
        repair_button = self._button('Repair Selected Source…', self.repair_selected_source)
        repair_button.setObjectName('repairSelectedModSource')
        actions.addWidget(repair_button)
        actions.addWidget(self._button('Remove selected', self.remove_mod))
        actions.addWidget(self._button('Move Up', lambda: self.move_selected(-1)))
        actions.addWidget(self._button('Move Down', lambda: self.move_selected(1)))
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
                try:
                    verified_targets = set(allowed_paks(self.stage_path))
                    from universal_stage import digest
                    plan_path = self.stage_path / 'rekey_plan.json'
                    current_fingerprint = digest(plan_path) if plan_path.is_file() else None
                    signing_ready = True
                except (OSError, ValueError, RuntimeError):
                    verified_targets, current_fingerprint, signing_ready = set(), None, False
                state = self.store.status()
                applied_mod_ids = set()
                if (state['profile_id'] == profile['id']
                        and not state['in_progress'] and not state['pending']
                        and self.store.applied_path.is_file()):
                    import json
                    applied = json.loads(
                        self.store.applied_path.read_text(encoding='utf-8'))
                    if Path(applied.get('stage', '')).resolve() == self.stage_path.resolve():
                        applied_mod_ids = {entry.get('mod_id')
                                           for entry in applied.get('entries', [])}
                for i, mod in enumerate(mods):
                    checked = QTableWidgetItem()
                    checked.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    checked.setCheckState(Qt.Checked if mod['enabled'] else Qt.Unchecked)
                    checked.setData(Qt.UserRole, mod['id'])
                    self.table.setItem(i, 0, checked)
                    source = Path(mod['source'])
                    if not source.is_file():
                        compatibility = 'Missing source'
                    elif not signing_ready:
                        compatibility = 'Check stage'
                    elif mod['relative'].casefold() not in verified_targets:
                        compatibility = 'Incompatible target'
                    elif (current_fingerprint and
                          mod.get('verified_stage_fingerprint') == current_fingerprint):
                        compatibility = 'Verified for stage'
                    elif mod.get('verified_stage_fingerprint') and current_fingerprint:
                        compatibility = 'Review this build'
                    else:
                        compatibility = 'Target recognized'
                    status = ('Staged' if mod['enabled'] and mod['id'] in applied_mod_ids
                              else 'Enabled' if mod['enabled'] else 'Disabled')
                    title = mod.get('display_name') or source.stem
                    values = (title, mod['relative'], compatibility, status)
                    for col, value in enumerate(values, 1):
                        item = QTableWidgetItem(str(value))
                        item.setToolTip('Signed PAK: ' + str(source) +
                                        '\nVerified SHA256: ' + mod['sha256'] +
                                        '\nCompatibility checks target identity, not runtime behavior.')
                        self.table.setItem(i, col, item)
                self.table.resizeRowsToContents()
                _, problems = self.store.plan(profile['id'], self.stage_path, verify_files=False)
                self.status.setText(
                    '%d mods, %d enabled. %s' %
                    (len(mods), sum(bool(m['enabled']) for m in mods),
                     'Conflict: ' + '; '.join(problems[:3]) if problems
                     else 'No enabled-target conflicts detected.'))
            else:
                self.status.setText('Create a collection to organize installed mods.')
            state = self.store.status()
            if state['in_progress'] or state['pending']:
                self.status.setText(self.status.text() +
                                    ' A profile operation is interrupted; inspect recovery before changing mods.')
            elif state['profile_id']:
                active = next((p['name'] for p in self.store.list_profiles()
                               if p['id'] == state['profile_id']), 'Earlier profile')
                self.status.setText(self.status.text() + ' Staged: ' + active + '.')
        except Exception as exc:
            self.status.setText('Mod library status unavailable: ' + str(exc))
        finally:
            self._filling = False

    def verify_and_repair(self):
        """Read-only, explicitly requested collection verification."""
        try:
            profile_id = self._selected()
            if exports_busy(self):
                return
            self.verify_button.setEnabled(False)
            def completed(issues):
                self.verify_button.setEnabled(True)
                if not issues:
                    QMessageBox.information(
                        self, 'Mod Verification',
                        'All tracked mod sources match their SHA256 hashes, '
                        'PAK targets are recognized, and there are no enabled-target '
                        'collisions. This does not guarantee runtime compatibility.')
                    self.refresh()
                    return
                lines = [f'{x.target}: {x.problem}' for x in issues]
                QMessageBox.warning(
                    self, 'Mod Verification — attention needed',
                    '\n'.join(lines[:16]) +
                    ('\n…More issues exist.' if len(lines) > 16 else '') +
                    '\n\nTo relink a missing or altered source, select that mod '
                    'and choose Repair Selected Source… below. No data was changed.')
                self.refresh()
            def failed(error):
                self.verify_button.setEnabled(True)
                QMessageBox.warning(self, 'Mod Verification failed', str(error))
            begin_file_operation(
                self, 'Verify installed mods',
                'Reading signed mod hashes and conflicts (no writes)…',
                lambda _progress, cancelled:
                verify_collection(self.store.root, profile_id, self.stage_path)
                if not cancelled() else (_ for _ in ()).throw(
                    InterruptedError('Cancelled')),
                on_success=completed, on_error=failed)
        except Exception as exc:
            self.verify_button.setEnabled(True)
            self._error(exc)

    def repair_selected_source(self):
        """Only relink after the user identifies and approves a byte-identical PAK."""
        try:
            profile_id = self._selected()
            mod_id = self._selected_mod_id()
            source, _ = QFileDialog.getOpenFileName(
                self, 'Locate exact replacement for missing/changed PAK',
                '', 'Signed PAK files (*.pak)')
            if not source:
                return
            if QMessageBox.question(
                    self, 'Confirm mod source repair',
                    'Verify the selected replacement against the saved SHA256, '
                    'current signed stage and RSA archive layout, then register '
                    'a durable local copy? This will not stage or modify game PAKs.',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
            if exports_busy(self):
                return
            self.verify_button.setEnabled(False)
            def completed(destination):
                self.verify_button.setEnabled(True)
                self.reload(select=profile_id)
                QMessageBox.information(
                    self, 'Source repaired',
                    'The exact verified mod has been relinked to the local library. '
                    'No signing-stage or installed game files were changed.')
            def failed(error):
                self.verify_button.setEnabled(True)
                QMessageBox.warning(self, 'Mod repair refused', str(error))
            begin_file_operation(
                self, 'Repair verified mod source',
                'Verifying exact SHA256 and signed PAK before relinking…',
                lambda _progress, cancelled:
                repair_mod_source(self.store.root, profile_id, mod_id,
                                  source, self.stage_path)
                if not cancelled() else (_ for _ in ()).throw(
                    InterruptedError('Cancelled')),
                on_success=completed, on_error=failed)
        except Exception as exc:
            self._error(exc)

    def show_receipts(self):
        """On-demand, copyable installation history with status from journals."""
        rows = list_receipts(self.store.root)
        if not rows:
            QMessageBox.information(self, 'Installation History',
                                    'No verified mod imports recorded yet.')
            return
        dialog = QDialog(self)
        dialog.setObjectName('modInstallationHistory')
        dialog.setWindowTitle('Installation History')
        dialog.resize(690, 410)
        layout = QVBoxLayout(dialog)
        summary = QPlainTextEdit(dialog)
        summary.setObjectName('modInstallationHistoryText')
        summary.setReadOnly(True)
        lines = []
        for receipt in rows:
            lines.append(receipt.get('created_utc', '?') +
                         '  ·  ' + receipt.get('status', 'unknown'))
            lines.extend('    ' + record.get('target', 'Unknown') +
                         ' · ' + str(record.get('changed_assets', '?')) + ' assets'
                         for record in receipt.get('entries', []))
            lines.append('    ' + receipt.get('note', ''))
            lines.append('')
        summary.setPlainText('\n'.join(lines))
        layout.addWidget(summary)
        actions = QHBoxLayout()
        copy = self._button('Copy Summary', lambda: QApplication.clipboard().setText(
            summary.toPlainText()))
        actions.addWidget(copy)
        actions.addStretch()
        actions.addWidget(self._button('Close', dialog.accept))
        layout.addLayout(actions)
        dialog.exec()

    def duplicate_profile(self):
        try:
            original=self._current_profile()
            if not original:
                raise ValueError('Choose a profile to duplicate.')
            name,ok=QInputDialog.getText(
                self,'Duplicate Profile','New name:',
                text=original['name']+' (copy)')
            if ok:
                pid=self.store.duplicate_profile(original['id'],name)
                self.reload(select=pid)
        except (ValueError,OSError) as exc:
            self._error(exc)

    def move_selected(self, direction):
        try:
            pid=self._selected()
            mid=self._selected_mod_id()
            self.store.move_mod(pid,mid,direction)
            self.reload(select=pid)
            for row in range(self.table.rowCount()):
                if self.table.item(row,0).data(Qt.UserRole)==mid:
                    self.table.selectRow(row)
                    break
        except (ValueError,OSError) as exc:
            self._error(exc)

    def export_shared(self):
        try:
            pid=self._selected()
            profile=self._current_profile()
            destination,_=QFileDialog.getSaveFileName(
                self,'Export shareable profile',profile['name']+'.json','JSON (*.json)')
            if not destination:
                return
            self.store.export_manifest(pid,destination)
            QMessageBox.information(
                self,'Profile exported',
                'Exported file references and checksums, without game files '
                'or local computer paths. Share signed mod PAKs separately.')
        except (ValueError,OSError) as exc:
            self._error(exc)

    def import_shared(self):
        source,_=QFileDialog.getOpenFileName(
            self,'Import shared profile','','JSON (*.json)')
        if not source:
            return
        folder=QFileDialog.getExistingDirectory(
            self,'Locate the shared signed PAK files (same filenames)')
        if not folder:
            return
        try:
            profile_id=self.store.import_manifest(
                source,folder,self.stage_path)
            self.reload(select=profile_id)
            QMessageBox.information(
                self,'Profile imported',
                'Verified mod files and hashes. Review target conflicts '
                'before applying; nothing was automatically staged.')
        except (ValueError,OSError,RuntimeError) as exc:
            self._error(exc)

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
