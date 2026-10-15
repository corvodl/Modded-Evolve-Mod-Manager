"""One collapsed, opt-in System Health section on the Home dashboard."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QFrame, QMessageBox, QFileDialog
)

from qt_ui_components import DisclosurePanel
from system_health import (
    collect_snapshot, change_offline_rule, configure_dumps,
    capture_minidump, cleanup_stale_temps, support_zip
)


class _HealthSignals(QObject):
    done = Signal(str, object, str)


class _HealthJob(QRunnable):
    def __init__(self, kind, callback, signals):
        super().__init__()
        self.kind = kind
        self.callback = callback
        self.signals = signals

    def run(self):
        try:
            value = self.callback()
            error = ""
        except Exception as exc:
            value, error = None, f"{type(exc).__name__}: {exc}"
        # Closing the manager while an optional diagnostic is running can
        # destroy its receiver. Never emit against a deleted Qt wrapper.
        from shiboken6 import isValid
        try:
            if isValid(self.signals):
                self.signals.done.emit(self.kind, value, error)
        except RuntimeError:
            pass


class SystemHealthPanel(QWidget):
    """Never polls, alters a registry key or scans disks until the user asks."""

    LABELS = (
        ("firewall", "Windows Firewall", "Only the manager's Evolve outbound offline rule"),
        ("process", "Game Process", "Evolve/launcher CPU, memory and uptime"),
        ("crash", "Crash Dumps & Support", "Opt-in WER minidumps and private support ZIP"),
        ("storage", "Storage & Backups", "Available space, swap state and stale partial files"),
        ("environment", "Game Environment", "Evolve.exe, Windows components and DirectX presence"),
    )

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.setObjectName("systemHealthPanel")
        self.manager = manager
        self._running = False
        self._scanned = False
        self._last = {}
        self._signals = _HealthSignals(self)
        self._signals.done.connect(self._finished)
        self._thread_pool = QThreadPool.globalInstance()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.disclosure = DisclosurePanel("System Health  ·  On-demand diagnostics",
                                          expanded=False, parent=self)
        self.toggle = self.disclosure.toggle
        self.toggle.setObjectName("systemHealthToggle")
        self.toggle.toggled.connect(self._expanded)
        outer.addWidget(self.disclosure)

        explanation = QLabel(
            "Nothing is scanned while this panel is closed. Checks are read-only; "
            "firewall, registry, dump and cleanup changes always require your confirmation.")
        explanation.setWordWrap(True)
        explanation.setObjectName("muted")
        self.disclosure.addWidget(explanation)

        refresh_row = QHBoxLayout()
        self.refresh_button = self._button("Check System Health", self.refresh, "systemHealthRefresh")
        refresh_row.addWidget(self.refresh_button)
        self.progress = QLabel("Not checked")
        self.progress.setObjectName("muted")
        refresh_row.addWidget(self.progress)
        refresh_row.addStretch(1)
        self.disclosure.addLayout(refresh_row)

        self.labels = {}
        for key, name, subtitle in self.LABELS:
            card = QFrame()
            card.setObjectName("systemHealthCheck")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(9, 7, 9, 7)
            layout.setSpacing(4)
            title = QLabel(name)
            title.setObjectName("panelTitle")
            layout.addWidget(title)
            hint = QLabel(subtitle)
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            output = QLabel("Not checked")
            output.setObjectName("systemHealthResult_" + key)
            output.setTextInteractionFlags(Qt.TextSelectableByMouse)
            output.setWordWrap(True)
            layout.addWidget(output)
            self.labels[key] = output

            actions = QHBoxLayout()
            actions.setSpacing(6)
            if key == "firewall":
                actions.addWidget(self._button("Enforce Offline Block",
                                               lambda: self._firewall(True), "systemHealthEnforceOffline"))
                actions.addWidget(self._button("Remove Manager Block",
                                               lambda: self._firewall(False), "systemHealthRemoveOffline"))
            if key == "crash":
                actions.addWidget(self._button("Enable Crash Dumps",
                                               lambda: self._dumps(True), "systemHealthEnableDumps"))
                actions.addWidget(self._button("Disable Crash Dumps",
                                               lambda: self._dumps(False), "systemHealthDisableDumps"))
                actions.addWidget(self._button("Capture Dump…", self._capture_dump,
                                               "systemHealthCaptureDump"))
                actions.addWidget(self._button("Support ZIP…", self._support_zip,
                                               "systemHealthSupportZip"))
            if key == "storage":
                actions.addWidget(self._button("Clean Stale Temp Files",
                                               self._cleanup, "systemHealthCleanTemp"))
            actions.addStretch(1)
            layout.addLayout(actions)
            self.disclosure.addWidget(card)
        foot = QLabel("Offline play protection remains fail-closed. Dumps and support logs "
                      "may contain private information; review them before sharing.")
        foot.setObjectName("muted")
        foot.setWordWrap(True)
        self.disclosure.addWidget(foot)

        if os.name != "nt":
            for name in ("systemHealthEnforceOffline", "systemHealthRemoveOffline",
                         "systemHealthEnableDumps", "systemHealthDisableDumps",
                         "systemHealthCaptureDump"):
                self.findChild(QPushButton, name).setEnabled(False)

    @staticmethod
    def _button(title, callback, object_name):
        button = QPushButton(title)
        button.setObjectName(object_name)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def _args(self):
        manager = self.manager
        game = manager.game_root or manager.settings.get("game_root", "") or manager._find_game_root()
        from app_runtime import DATA_HOME
        return (str(game or ""), str(DATA_HOME), str(manager.projects_path),
                str(manager.stage_path), str(manager.swap_path))

    def _expanded(self, expanded):
        if expanded and not self._scanned:
            self.refresh()

    def _submit(self, kind, callback):
        if self._running:
            return
        self._running = True
        self.refresh_button.setEnabled(False)
        self.progress.setText("Working…")
        self._thread_pool.start(_HealthJob(kind, callback, self._signals))

    def refresh(self):
        if self._running:
            return
        args = self._args()
        self._submit("snapshot", lambda: collect_snapshot(*args))

    def _finished(self, kind, result, error):
        self._running = False
        self.refresh_button.setEnabled(True)
        self.progress.setText("Check failed" if error else "Checked")
        if error:
            QMessageBox.warning(self, "System Health", error)
            return
        if kind == "snapshot":
            self._scanned = True
            self._last = result
            for key, _name, _subtitle in self.LABELS:
                state, detail = result.get(key, ("Unavailable", "No result returned"))
                self.labels[key].setText(state + "\n" + detail)
            return
        QMessageBox.information(self, "System Health", str(result))
        self.refresh()

    def _confirm(self, title, body):
        return QMessageBox.question(self, title, body,
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _firewall(self, enabled):
        game, _data, _projects, _stage, swap_root = self._args()
        action = "enforce" if enabled else "remove"
        if not self._confirm("Offline firewall rule",
                             f"Do you want to {action} the manager-owned outbound block for Evolve.exe?\n\n"
                             "Close the game first. This does not modify unrelated rules. "
                             "Removing the block can allow online connections; never launch "
                             "modified game files without offline protection."):
            return
        self._submit("firewall", lambda: (
            change_offline_rule(game, enabled, swap_root=swap_root),
            "Manager-owned rule updated.")[1])

    def _dumps(self, enabled):
        _game, data, *_rest = self._args()
        action = "register" if enabled else "remove"
        if not self._confirm("Evolve crash dumps",
                             f"{action.title()} Evolve.exe-specific Windows Error Reporting "
                             "minidumps?\n\nNo global Windows crash reporting settings will change. "
                             "Existing external configurations are preserved. "
                             "Dump files are sensitive and remain on your PC."):
            return
        self._submit("crash", lambda: configure_dumps(data, enabled))

    def _capture_dump(self):
        if not self._confirm("Manual game dump",
                             "Capture a minidump of the currently running Evolve.exe? "
                             "The game may pause briefly. Dumps may contain private data."):
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save Evolve dump", str(Path.home() / "Evolve-diagnostic.dmp"),
            "Windows dump (*.dmp)")
        if chosen:
            self._submit("dump", lambda: capture_minidump(chosen))

    def _support_zip(self):
        if not self._confirm("Export support ZIP",
                             "Create a local support ZIP containing a system health report "
                             "and small application/game logs? It excludes memory dumps, "
                             "PAKs, credentials and game assets. Review logs before sharing."):
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save support ZIP", str(Path.home() / "Evolve-System-Health.zip"),
            "ZIP archive (*.zip)")
        if chosen:
            game, data, projects, stage, swap = self._args()
            self._submit("support", lambda: support_zip(
                chosen, data, game, stage, swap, projects))

    def _cleanup(self):
        if not self._confirm("Clean manager temp files",
                             "Delete only partial .tmp/.part/.partial files older than "
                             "24 hours from the manager's Data/Temp and Data/Downloads "
                             "directories? No PAKs, projects or original backups are removed."):
            return
        _game, data, *_ = self._args()
        def cleanup():
            count, freed = cleanup_stale_temps(data)
            return f"Removed {count} old partial files ({freed / 1048576:.1f} MiB)."
        self._submit("cleanup", cleanup)
