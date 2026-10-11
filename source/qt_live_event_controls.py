"""Controls for activating only game-side-advertised offline event handlers.

No dispatcher is guessed from the V3 RVAs. An authenticated companion module
inside the selected Evolve.exe must implement the V1 loopback bridge.
"""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QComboBox, QSpinBox, QLineEdit, QPushButton, QMessageBox)

from developer_memory import list_evolve_processes
from developer_event_catalog import make_fixture
from developer_event_live import EventBridgeClient, EventBridgeError


class _Signals(QObject):
    completed = Signal(str, object, object)


class LiveEventControls(QWidget):
    def __init__(self, *, game_root_provider, current_event, current_fields,
                 output, parent=None):
        super().__init__(parent)
        self.game_root_provider = game_root_provider
        self.current_event = current_event
        self.current_fields = current_fields
        self.output = output
        self.capabilities = set()
        self.verified = None
        self._busy = False
        self._closed = False
        self.signals = _Signals(self)
        self.signals.completed.connect(self._finished)

        root = QVBoxLayout(self)
        title = QLabel(
            "Live game events: requires a verified in-game companion. "
            "Outbound game traffic must be blocked; online activation is disabled.")
        title.setWordWrap(True)
        root.addWidget(title)
        row = QHBoxLayout()
        row.addWidget(QLabel("Evolve PID"))
        self.pids = QComboBox()
        row.addWidget(self.pids)
        refresh = QPushButton("Refresh PIDs")
        refresh.clicked.connect(self.refresh_pids)
        row.addWidget(refresh)
        row.addWidget(QLabel("Bridge port"))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(22345)
        row.addWidget(self.port)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Session token from game-side module")
        row.addWidget(self.token, 1)
        root.addLayout(row)

        row = QHBoxLayout()
        self.connect_button = QPushButton("Connect to running game")
        self.connect_button.clicked.connect(self.connect_bridge)
        row.addWidget(self.connect_button)
        self.activate_button = QPushButton("Activate selected event")
        self.activate_button.clicked.connect(self.activate)
        self.activate_button.setEnabled(False)
        row.addWidget(self.activate_button)
        self.status = QLabel("No verified in-game dispatcher.")
        row.addWidget(self.status, 1)
        root.addLayout(row)
        self.pids.currentIndexChanged.connect(self._invalidate)
        self.port.valueChanged.connect(self._invalidate)
        self.token.textChanged.connect(self._invalidate)
        self.refresh_pids()

    def refresh_pids(self):
        old = self.pids.currentData()
        self.pids.clear()
        try:
            for pid in list_evolve_processes():
                self.pids.addItem(str(pid), pid)
            if old is not None:
                index = self.pids.findData(old)
                if index >= 0:
                    self.pids.setCurrentIndex(index)
        except (OSError, RuntimeError) as exc:
            self.status.setText("PID discovery: " + str(exc)[:150])
        self._invalidate()

    def _invalidate(self, *_):
        self.capabilities.clear()
        self.verified = None
        self.update_selection()

    def _client(self):
        return EventBridgeClient(
            self.pids.currentData(), self.port.value(), self.token.text(),
            self.game_root_provider)

    def _fingerprint(self):
        return (self.pids.currentData(), self.port.value(), self.token.text())

    def update_selection(self):
        event = self.current_event()
        self.activate_button.setEnabled(bool(
            not self._busy and not self._closed
            and self.verified == self._fingerprint()
            and event is not None and not event.uncertain_id
            and event.name in self.capabilities))

    def _run(self, kind, task):
        if self._busy or self._closed:
            return
        self._busy = True
        self.connect_button.setEnabled(False)
        self.update_selection()
        self.status.setText("Checking offline game bridge…")

        def worker():
            try:
                result = task()
                self.signals.completed.emit(kind, result, None)
            except Exception as exc:
                self.signals.completed.emit(kind, None, str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def connect_bridge(self):
        try:
            client = self._client()
            fingerprint = self._fingerprint()
        except (ValueError, EventBridgeError) as exc:
            self.status.setText(str(exc))
            return
        self._invalidate()
        self._run("connect", lambda: (fingerprint, client.probe()))

    def activate(self):
        event = self.current_event()
        if (event is None or event.uncertain_id or
                event.name not in self.capabilities):
            return
        try:
            client = self._client()
            if self._fingerprint() != self.verified:
                raise EventBridgeError("Reconnect to the game-side module first.")
            values = self.current_fields()
            fixture = make_fixture(event, values)
            if fixture["unfilled_fields"]:
                raise ValueError(
                    "Fill required fields: " + ", ".join(fixture["unfilled_fields"]))
            if QMessageBox.question(
                self, "Activate in running offline Evolve",
                "Activate " + event.name + " in Evolve.exe PID " +
                str(client.pid) + "?\n"
                "The game-side module must support this event. "
                "Activation may destabilize your current match.",
                QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
            self._run("activate", lambda: client.activate(event, values))
        except (ValueError, EventBridgeError) as exc:
            self.status.setText(str(exc))

    def _finished(self, kind, result, error):
        if self._closed:
            return
        self._busy = False
        self.connect_button.setEnabled(True)
        if error is not None:
            self._invalidate()
            self.status.setText("Not activated: " + str(error)[:220])
            self.output.setPlainText(str(error))
            return
        if kind == "connect":
            fingerprint, capabilities = result
            if fingerprint != self._fingerprint():
                self._invalidate()
                self.status.setText("Bridge settings changed. Reconnect.")
                return
            self.verified = fingerprint
            self.capabilities = set(capabilities)
            self.status.setText(
                str(len(self.capabilities)) + " native handlers advertised.")
            self.output.setPlainText(
                "Game-side event handlers:\n" +
                "\n".join(sorted(self.capabilities)))
        elif kind == "activate":
            status = result.get("status")
            if status == "effect_confirmed":
                self.status.setText("Game-side module confirmed the effect.")
            else:
                self.status.setText(
                    "Game bridge accepted request; effect not confirmed.")
            self.output.setPlainText(json.dumps(result, indent=2))
        self.update_selection()

    def stop(self):
        self._closed = True
        self.capabilities.clear()
        self.verified = None
        self.token.clear()
        self.update_selection()
