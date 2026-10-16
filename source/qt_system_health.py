"""One collapsed, opt-in System Health section on the Home dashboard."""
from __future__ import annotations

import os
import platform
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QFrame, QMessageBox, QFileDialog, QDialog, QPlainTextEdit, QToolButton,
    QMenu, QApplication, QScrollArea, QGridLayout, QSizePolicy
)

from qt_ui_components import DisclosurePanel
from system_health import (
    collect_snapshot, change_offline_rule, configure_dumps,
    capture_minidump, cleanup_stale_temps, support_zip
)
from system_health_extras import (
    windows_crash_history, format_discord_report, redact_support_text
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
        self._last_crashes = ()
        self._checked_time = None
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

        # Five *bounded* widgets, not five full-width expandable panels.
        # Keep Home navigation visible even when the health section is open.
        self.cards_scroll = QScrollArea(self)
        self.cards_scroll.setObjectName("systemHealthCardsScroll")
        self.cards_scroll.setFrameShape(QFrame.NoFrame)
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_scroll.setMaximumHeight(370)
        self.cards_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        cards = QWidget(self.cards_scroll)
        cards.setObjectName("systemHealthCards")
        grid = QGridLayout(cards)
        grid.setContentsMargins(0, 0, 5, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.labels = {}
        for position, (key, name, subtitle) in enumerate(self.LABELS):
            card = QFrame(cards)
            card.setMinimumHeight(146)
            card.setMaximumHeight(165)
            card.setObjectName("systemHealthCheck")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(9, 6, 9, 6)
            layout.setSpacing(3)
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
            output.setMaximumHeight(43)
            output.setToolTip("Select Details to view the full check output.")
            layout.addWidget(output)
            self.labels[key] = output

            actions = QHBoxLayout()
            actions.setSpacing(6)
            if key in ("firewall", "crash"):
                menu_button = QToolButton(card)
                menu_button.setObjectName("systemHealthActions_" + key)
                menu_button.setText("Actions ▾")
                menu_button.setPopupMode(QToolButton.InstantPopup)
                menu = QMenu(menu_button)
                menu_button.setMenu(menu)
                if key == "firewall":
                    menu.addAction("Enforce Offline Block").triggered.connect(
                        lambda _checked=False: self._firewall(True))
                    menu.addAction("Remove Manager Block").triggered.connect(
                        lambda _checked=False: self._firewall(False))
                else:
                    menu.addAction("Enable Crash Dumps").triggered.connect(
                        lambda _checked=False: self._dumps(True))
                    menu.addAction("Disable Crash Dumps").triggered.connect(
                        lambda _checked=False: self._dumps(False))
                    menu.addAction("Capture Dump…").triggered.connect(
                        lambda _checked=False: self._capture_dump())
                if os.name != "nt":
                    for action in menu.actions():
                        action.setEnabled(False)
                actions.addWidget(menu_button)
            if key == "crash":
                # Long rows of action buttons forced this card to take an
                # entire screen width. Keep all actions in one compact menu.
                crash_menu = menu_button.menu()
                crash_menu.addSeparator()
                crash_menu.addAction("Crash History…").triggered.connect(
                    lambda _checked=False: self._crash_history())
                crash_menu.addAction("Support Report…").triggered.connect(
                    lambda _checked=False: self._report())
                crash_menu.addAction("Support ZIP…").triggered.connect(
                    lambda _checked=False: self._support_zip())
                self.crash_history_action = crash_menu.actions()[-3]
            if key == "storage":
                self.recovery_button = self._button(
                    "Open Recovery", self._open_recovery, "systemHealthRecovery")
                self.recovery_button.setVisible(False)
                actions.addWidget(self.recovery_button)
                actions.addWidget(self._button(
                    "Clean Stale Temp Files", self._cleanup, "systemHealthCleanTemp"))
            details = self._button(
                "Details…", lambda k=key: self._show_check_details(k),
                "systemHealthDetails_" + key)
            actions.addWidget(details)
            actions.addStretch(1)
            layout.addLayout(actions)
            grid.addWidget(card, position // 2, position % 2)
        self.cards_scroll.setWidget(cards)
        self.disclosure.addWidget(self.cards_scroll)
        foot = QLabel("Offline play protection remains fail-closed. Dumps and support logs "
                      "may contain private information; review them before sharing.")
        foot.setObjectName("muted")
        foot.setWordWrap(True)
        self.disclosure.addWidget(foot)

        if os.name != "nt":
            self.crash_history_action.setEnabled(False)

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

    def _show_check_details(self, key):
        entry = self._last.get(key, ("Not checked", "Run Check System Health first."))
        label = next((title for code, title, _ in self.LABELS if code == key), key)
        state, details = entry
        self._open_preview(label + " details",
                           str(state) + "\n" + str(details))

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
            self._checked_time = datetime.now().strftime("%H:%M")
            states = []
            for key, _name, _subtitle in self.LABELS:
                state, detail = result.get(key, ("Unavailable", "No result returned"))
                summary = next((line.strip() for line in str(detail).splitlines()
                                if line.strip()), "No details")
                self.labels[key].setText(str(state) + "\n" + summary[:115])
                self.labels[key].setToolTip(str(state) + "\n" + str(detail)[:2000])
                states.append(str(state).casefold())
            issues = sum(state in ("attention needed", "unavailable", "unknown")
                         for state in states)
            self.toggle.setText(
                "System Health  ·  " +
                (str(issues) + " check(s) need attention" if issues else "Checked") +
                "  ·  " + self._checked_time)
            storage = result.get("storage", ("", ""))[1].casefold()
            self.recovery_button.setVisible(
                "swap journal: unsafe" in storage or
                "swap journal: interrupted" in storage or
                "swap journal: swapped" in storage or
                "swap journal: restoring" in storage or
                "swap journal: swapping" in storage)
            return
        if kind == "history":
            self._last_crashes = tuple(result)
            self._open_preview(
                "Recent Evolve crashes (Event Viewer)",
                "\n".join(result) if result else
                "No matching Evolve/launcher errors were found in recent Windows Application events.",
                allow_copy=True)
            return
        QMessageBox.information(self, "System Health", str(result))
        self.refresh()

    def _confirm(self, title, body):
        return QMessageBox.question(self, title, body,
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _open_preview(self, title, content, *, allow_copy=True):
        """Editable privacy preview: never exports raw logs or machine identifiers."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setObjectName("systemHealthPreview")
        dialog.resize(735, 480)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Review this text before copying or saving. Local paths, "
                      "emails, and common secrets are redacted automatically.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setObjectName("systemHealthPreviewText")
        editor.setPlainText(redact_support_text(content))
        layout.addWidget(editor)
        buttons = QHBoxLayout()
        if allow_copy:
            copy = self._button("Copy", lambda: QApplication.clipboard().setText(
                redact_support_text(editor.toPlainText())), "systemHealthPreviewCopy")
            buttons.addWidget(copy)
        save = self._button("Save Text…", lambda: self._save_preview(editor),
                            "systemHealthPreviewSave")
        buttons.addWidget(save)
        buttons.addStretch()
        buttons.addWidget(self._button("Close", dialog.accept,
                                       "systemHealthPreviewClose"))
        layout.addLayout(buttons)
        dialog.exec()

    def _save_preview(self, editor):
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save reviewed report", str(Path.home() / "Evolve-Support.txt"),
            "Text (*.txt)")
        if chosen:
            path = Path(chosen)
            if path.exists() and not self._confirm("Overwrite report?",
                                                   "Replace the selected text report?"):
                return
            path.write_text(redact_support_text(editor.toPlainText()),
                            encoding="utf-8")

    def _report(self):
        from version_info import app_version
        from installed_game_versions import installed_versions
        game, _data, _projects, _stage, swap = self._args()
        try:
            versions = installed_versions(game, swap)
            client = versions.get("client") or "Unknown"
            actual_game = versions.get("game") or "Unknown"
        except (OSError, ValueError, KeyError):
            client, actual_game = "Unavailable", "Unavailable"
        report = format_discord_report(
            os_version=platform.platform(), manager_version=app_version(),
            client_version=client, game_version=actual_game, checks=self._last,
            crashes=self._last_crashes)
        self._open_preview("Discord-ready Support Report", report)

    def _crash_history(self):
        self._submit("history", windows_crash_history)

    def _open_recovery(self):
        """Navigation only; never restore or delete game files automatically."""
        from qt_manager import SECTIONS
        self.manager.open_page(SECTIONS.index("Settings"))
        button = next((b for b in self.manager.pages["settings"].findChildren(QPushButton)
                       if b.text() == "Restore Game Files"), None)
        if button:
            button.setFocus(Qt.OtherFocusReason)

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
