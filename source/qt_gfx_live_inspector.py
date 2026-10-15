"""Read-only live GFx snapshot tab. No memory editing or dynamic injection.

The game must be running offline and an independently verified Scaleform
provider must be registered by a trusted game-side adapter. The native event
bridge by itself does not expose the UI hierarchy.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
)

from gfx_live_ui_bridge import UiSnapshotBridgeClient
from developer_memory import list_evolve_processes


class _CaptureSignals(QObject):
    completed = Signal(object, object)


class LiveUiInspector(QWidget):
    def __init__(self, game_root_provider, parent=None):
        super().__init__(parent)
        self.game_root_provider = game_root_provider
        self._busy = False
        self._closed = False
        self.signals = _CaptureSignals(self)
        self.signals.completed.connect(self._captured)
        root = QVBoxLayout(self)
        info = QLabel(
            "Read-only live Scaleform inspector (experimental). Requires a "
            "verified in-game UI adapter, a session token and offline isolation. "
            "The transport alone cannot extract the game UI. No memory writes "
            "or persistent game changes are available here.")
        info.setWordWrap(True)
        root.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Evolve PID"))
        self.pids = QComboBox()
        self.pids.setObjectName("gfxLiveUiPid")
        row.addWidget(self.pids)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_pids)
        row.addWidget(refresh)
        row.addWidget(QLabel("Port"))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(22345)
        row.addWidget(self.port)
        self.token = QLineEdit()
        self.token.setObjectName("gfxLiveUiToken")
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("One-time in-game bridge session token")
        row.addWidget(self.token, 2)
        root.addLayout(row)
        actions = QHBoxLayout()
        self.capture = QPushButton("Capture live UI hierarchy")
        self.capture.setObjectName("gfxLiveUiCapture")
        self.capture.clicked.connect(self.capture_snapshot)
        actions.addWidget(self.capture)
        self.status = QLabel("No verified live snapshot captured.")
        self.status.setWordWrap(True)
        actions.addWidget(self.status, 1)
        root.addLayout(actions)
        self.tree = QTreeWidget()
        self.tree.setObjectName("gfxLiveUiHierarchy")
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            ("Runtime object", "Type", "Text", "X / Y", "W / H", "Visible"))
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 220)
        root.addWidget(self.tree, 1)
        self.pids.currentIndexChanged.connect(self._invalidate)
        self.port.valueChanged.connect(self._invalidate)
        self.token.textChanged.connect(self._invalidate)
        # PID enumeration is opt-in; do not block opening the GFX editor.
        self.status.setText("Click Refresh to discover a running offline Evolve.exe.")

    def refresh_pids(self):
        prior = self.pids.currentData()
        self.pids.clear()
        try:
            for pid in list_evolve_processes():
                self.pids.addItem(str(pid), pid)
            index = self.pids.findData(prior)
            if index >= 0:
                self.pids.setCurrentIndex(index)
        except (OSError, RuntimeError) as exc:
            self.status.setText("PID discovery unavailable: " + str(exc)[:130])
        self._invalidate()

    def _invalidate(self, *_):
        self.tree.clear()

    def capture_snapshot(self):
        if self._busy or self._closed:
            return
        try:
            client = UiSnapshotBridgeClient(
                self.pids.currentData(), self.port.value(),
                self.token.text(), self.game_root_provider)
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc)[:230])
            return
        self._busy = True
        self.capture.setEnabled(False)
        fingerprint = (client.pid, client.port, client.token)
        self.status.setText("Checking verified offline game and requesting UI…")
        def worker():
            try:
                result = client.snapshot()
                self.signals.completed.emit((fingerprint, result), None)
            except Exception as exc:
                self.signals.completed.emit(None, str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _captured(self, response, error):
        if self._closed:
            return
        self._busy = False
        self.capture.setEnabled(True)
        if error is not None:
            self.tree.clear()
            self.status.setText(str(error)[:350])
            return
        fingerprint, data = response
        if fingerprint != (self.pids.currentData(), self.port.value(), self.token.text()):
            self._invalidate()
            self.status.setText("Connection changed during capture. Retry.")
            return
        self.tree.clear()
        objects = {}
        for node in data["nodes"]:
            item = QTreeWidgetItem([
                node["name"] or node["id"], node["kind"], node["text"],
                f'{node["x"]:g}, {node["y"]:g}',
                f'{node["width"]:g}, {node["height"]:g}',
                "Yes" if node["visible"] else "No"])
            item.setToolTip(0, node["id"])
            objects[node["id"]] = item
        for node in data["nodes"]:
            item = objects[node["id"]]
            parent = node["parent"]
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                objects[parent].addChild(item)
        self.tree.expandToDepth(1)
        self.status.setText(
            f'Captured {len(data["nodes"])} objects · '
            f'{data["movie"]} · frame {data["frame"]}. '
            "Read-only; no gameplay edits applied.")

    def stop(self):
        self._closed = True
        self.token.clear()
        self.tree.clear()
