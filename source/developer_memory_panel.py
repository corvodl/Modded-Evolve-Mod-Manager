"""Authenticated Developer memory workbench; never built before unlock.

Qt UI operations that enumerate/scan process memory run on worker threads.
No game data, process list, addresses, or access state are written to disk.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QCheckBox, QProgressBar, QMessageBox, QFormLayout, QAbstractItemView
)

from developer_memory import (
    list_evolve_processes, read_memory, write_memory, parse_address, parse_hex)
from developer_memory_inspector import (
    TYPES, MAX_MATCHES, encode_value, decode_value, format_hex,
    list_modules, list_regions, scan_process, refine_process
)


class _Signals(QObject):
    completed = Signal(int, str, int, object, str)
    progressed = Signal(int, int)


def _button(text, callback):
    button = QPushButton(text)
    button.clicked.connect(lambda _checked=False: callback())
    return button


def _table(headings):
    widget = QTableWidget(0, len(headings))
    widget.setHorizontalHeaderLabels(headings)
    widget.verticalHeader().hide()
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setAlternatingRowColors(True)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def _fill(table, rows):
    table.setUpdatesEnabled(False)
    try:
        table.setRowCount(len(rows))
        for row, cells in enumerate(rows):
            for column, value in enumerate(cells):
                table.setItem(row, column, QTableWidgetItem(str(value)))
    finally:
        table.setUpdatesEnabled(True)


class DeveloperMemoryPanel(QWidget):
    """Attach, scan, inspect, edit, undo and watch one Evolve.exe process."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = False
        self._job_serial = 0
        self._jobs = set()
        self._scan_cancel = threading.Event()
        self._pid = None
        self._regions = []
        self._modules = []
        self._matches = []
        self._scan_kind = ''
        self._snapshot = None  # (pid, address, bytes)
        self._undo = []
        self.signals = _Signals(self)
        self.signals.completed.connect(self._completed)
        self.signals.progressed.connect(self._progress)

        self.setMinimumHeight(510)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)
        header = QHBoxLayout()
        header.addWidget(QLabel('Running game'))
        self.processes = QComboBox()
        self.processes.setMinimumWidth(230)
        header.addWidget(self.processes, 1)
        header.addWidget(_button('Refresh Processes', self.refresh_processes))
        root.addLayout(header)
        self.processes.currentIndexChanged.connect(self._process_changed)
        self.connection = QLabel('Waiting for Evolve.exe…')
        root.addWidget(self.connection)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        modules_page = QWidget()
        modules_layout = QVBoxLayout(modules_page)
        modules_layout.addWidget(QLabel('Double-click a module to inspect its base address.'))
        self.modules = _table(('Module', 'Base address', 'Size', 'Location'))
        self.modules.cellDoubleClicked.connect(self._open_module)
        modules_layout.addWidget(self.modules)
        self.tabs.addTab(modules_page, 'Modules')

        regions_page = QWidget()
        regions_layout = QVBoxLayout(regions_page)
        regions_layout.addWidget(QLabel('Committed regions. Double-click a readable region to inspect it.'))
        self.regions = _table(('Base address', 'Size', 'Access', 'Kind'))
        self.regions.cellDoubleClicked.connect(self._open_region)
        regions_layout.addWidget(self.regions)
        self.tabs.addTab(regions_page, 'Memory Regions')

        scan_page = QWidget()
        scan_layout = QVBoxLayout(scan_page)
        entry = QHBoxLayout()
        entry.addWidget(QLabel('Value'))
        self.scan_value = QLineEdit()
        self.scan_value.setPlaceholderText('e.g. 100 or 3.5')
        entry.addWidget(self.scan_value, 2)
        entry.addWidget(QLabel('Type'))
        self.scan_type = QComboBox()
        self.scan_type.addItems(TYPES)
        entry.addWidget(self.scan_type, 2)
        scan_layout.addLayout(entry)
        operations = QHBoxLayout()
        self.writable = QCheckBox('Writable regions only (faster)')
        self.writable.setChecked(True)
        operations.addWidget(self.writable)
        operations.addWidget(_button('First Scan', self.first_scan))
        self.refine_mode = QComboBox()
        self.refine_mode.addItems(('Exact', 'Changed', 'Unchanged', 'Increased', 'Decreased'))
        operations.addWidget(self.refine_mode)
        operations.addWidget(_button('Next Scan', self.next_scan))
        operations.addWidget(_button('Cancel', self.cancel_scan))
        scan_layout.addLayout(operations)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        scan_layout.addWidget(self.scan_progress)
        self.scan_label = QLabel('Enter a value and click First Scan.')
        scan_layout.addWidget(self.scan_label)
        self.results = _table(('Address', 'Value', 'Raw bytes'))
        self.results.cellDoubleClicked.connect(self._open_result)
        scan_layout.addWidget(self.results, 1)
        self.tabs.addTab(scan_page, 'Value Scanner')

        hex_page = QWidget()
        hex_layout = QVBoxLayout(hex_page)
        location = QHBoxLayout()
        location.addWidget(QLabel('Address'))
        self.address = QLineEdit()
        self.address.setPlaceholderText('Select a module, region or scan result; or enter 0x…')
        location.addWidget(self.address, 1)
        location.addWidget(QLabel('Bytes'))
        self.byte_count = QComboBox()
        self.byte_count.addItems(('16', '32', '64', '128', '256'))
        self.byte_count.setCurrentText('64')
        location.addWidget(self.byte_count)
        location.addWidget(_button('Read', self.read_hex))
        hex_layout.addLayout(location)
        self.hex_dump = QPlainTextEdit()
        self.hex_dump.setReadOnly(True)
        self.hex_dump.setPlaceholderText('Select a readable memory address to display a hex + ASCII dump.')
        self.hex_dump.setMinimumHeight(130)
        hex_layout.addWidget(self.hex_dump)
        hex_layout.addWidget(QLabel('Editable bytes (hex). Requires a matching read of the same address.'))
        self.hex_edit = QPlainTextEdit()
        self.hex_edit.setPlaceholderText('Read a region, then edit its hex byte values here.')
        self.hex_edit.setMaximumHeight(90)
        hex_layout.addWidget(self.hex_edit)
        actions = QHBoxLayout()
        actions.addWidget(_button('Write Edited Hex', self.write_hex))
        self.undo_button = _button('Undo Last Write', self.undo_write)
        self.undo_button.setEnabled(False)
        actions.addWidget(self.undo_button)
        self.watch = QCheckBox('Live refresh')
        actions.addWidget(self.watch)
        self.refresh_interval = QComboBox()
        self.refresh_interval.addItems(('250 ms', '500 ms', '1000 ms', '2000 ms'))
        self.refresh_interval.setCurrentText('1000 ms')
        actions.addWidget(self.refresh_interval)
        hex_layout.addLayout(actions)
        self.hex_status = QLabel('Writes require confirmation. Undo is available until you leave this page.')
        hex_layout.addWidget(self.hex_status)
        self.tabs.addTab(hex_page, 'Hex Viewer / Editor')

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(3000)
        self.poll_timer.timeout.connect(self.refresh_processes)
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._watch_tick)
        self.watch.toggled.connect(self._watch_toggled)
        self.refresh_interval.currentIndexChanged.connect(self._watch_toggled)

    def start(self):
        if self._closed:
            return
        self.refresh_processes()
        self.poll_timer.start()

    def stop(self):
        self._closed = True
        self._scan_cancel.set()
        self.poll_timer.stop()
        self.watch_timer.stop()
        self._snapshot = None
        self._undo.clear()
        self._matches.clear()
        self._regions.clear()
        self._modules.clear()

    def _run(self, kind, pid, function):
        """Worker result identity is checked on receipt, never updates a closed tab."""
        self._job_serial += 1
        token = self._job_serial
        self._jobs.add(kind)
        def background():
            payload = None
            error = ''
            try:
                payload = function(token)
            except Exception as exc:
                error = str(exc)
            self.signals.completed.emit(token, kind, pid, payload, error)
        threading.Thread(target=background, daemon=True).start()

    def refresh_processes(self):
        if self._closed:
            return
        try:
            pids = list_evolve_processes()
        except Exception as exc:
            self.connection.setText('Cannot enumerate game processes: ' + str(exc))
            return
        old = self.processes.currentData()
        chosen = old if old in pids else (pids[0] if pids else None)
        current_options = [self.processes.itemData(i)
                           for i in range(self.processes.count())]
        if current_options != pids:
            self.processes.blockSignals(True)
            self.processes.clear()
            for pid in pids:
                self.processes.addItem('Evolve.exe — PID ' + str(pid), pid)
            if chosen is not None:
                self.processes.setCurrentIndex(pids.index(chosen))
            self.processes.blockSignals(False)
        if chosen != self._pid:
            self._attach(chosen)
        elif chosen is None:
            self.connection.setText('No running Evolve.exe. Launch the game to attach automatically.')

    def _process_changed(self, _index):
        if not self._closed:
            self._attach(self.processes.currentData())

    def _attach(self, pid):
        if pid == self._pid:
            return
        self._scan_cancel.set()
        self._pid = pid
        self._matches = []
        self._regions = []
        self._modules = []
        self._snapshot = None
        self._undo = []
        self.undo_button.setEnabled(False)
        _fill(self.modules, [])
        _fill(self.regions, [])
        _fill(self.results, [])
        self.hex_dump.clear()
        self.hex_edit.clear()
        if pid is None:
            self.connection.setText('No running Evolve.exe. Launch the game to attach automatically.')
            self.scan_label.setText('No attached process.')
            return
        self.connection.setText('Attached to Evolve.exe — PID %d; loading process map…' % pid)
        self.scan_label.setText('Ready for a first scan.')
        self._run('map', pid, lambda _token: (list_modules(pid), list_regions(pid)))

    def _pid_required(self):
        if self._pid is None or self._pid != self.processes.currentData():
            raise ValueError('Waiting for a running Evolve.exe process.')
        return self._pid

    def _completed(self, token, kind, pid, payload, error):
        if self._closed or pid != self._pid:
            return
        self._jobs.discard(kind)
        if kind == 'map':
            if error:
                self.connection.setText('Evolve.exe PID %d: %s' % (pid, error))
                return
            modules, regions = payload
            self._modules = modules
            self._regions = regions
            _fill(self.modules, [(m['name'], '0x%016X' % m['base'],
                                  '%d KiB' % (m['size'] // 1024), m['path'])
                                  for m in modules[:2000]])
            _fill(self.regions, [
                ('0x%016X' % m['base'], '%d KiB' % (m['size']//1024),
                 ('RW' if m['writable'] else 'Read' if m['readable'] else 'No access'),
                 m['kind']) for m in regions[:2000]])
            self.connection.setText(
                'Attached: Evolve.exe PID %d • %d modules • %d committed regions'
                % (pid, len(modules), len(regions)))
            if modules and self._snapshot is None:
                preferred = next((item for item in modules
                                  if item['name'].casefold() == 'evolve.exe'),
                                 modules[0])
                self.address.setText('0x%016X' % preferred['base'])
                self.read_hex()
        elif kind == 'scan':
            self.scan_progress.setRange(0, 1)
            self.scan_progress.setValue(1)
            if error:
                self.scan_label.setText('Scan error: ' + error)
                return
            if payload is None:
                return
            self._matches = payload['matches']
            self._render_matches(payload.get('reason', ''),
                                 payload.get('scanned', 0))
        elif error:
            self.connection.setText('Memory operation failed: ' + error)

    def _progress(self, token, scanned):
        if self._closed or 'scan' not in self._jobs:
            return
        self.scan_label.setText('Scanning… %.1f MiB read (Cancel to stop)' %
                                (scanned / 1048576.0))

    def _open_module(self, row, _col):
        if 0 <= row < len(self._modules):
            self._jump(self._modules[row]['base'])

    def _open_region(self, row, _col):
        if 0 <= row < len(self._regions):
            region = self._regions[row]
            if region['readable']:
                self._jump(region['base'])
            else:
                self.connection.setText('That memory region is not readable.')

    def _open_result(self, row, _col):
        if 0 <= row < min(len(self._matches), 500):
            self._jump(self._matches[row][0])

    def _jump(self, address):
        self.tabs.setCurrentIndex(3)
        self.address.setText('0x%016X' % address)
        self.read_hex()

    def first_scan(self):
        try:
            pid = self._pid_required()
            if 'scan' in self._jobs:
                raise RuntimeError('A scan is already running.')
            kind = self.scan_type.currentText()
            pattern = encode_value(self.scan_value.text(), kind)
            self._scan_kind = kind
            self._scan_cancel = threading.Event()
            self._matches = []
            _fill(self.results, [])
            self.scan_label.setText('Scanning process memory…')
            self.scan_progress.setRange(0, 0)
            only_writable = self.writable.isChecked()
            self._run('scan', pid, lambda token: scan_process(
                pid, pattern, writable_only=only_writable,
                cancel=self._scan_cancel,
                progress=lambda count: self.signals.progressed.emit(token, count)))
        except Exception as exc:
            self.scan_label.setText(str(exc))

    def next_scan(self):
        try:
            pid = self._pid_required()
            if 'scan' in self._jobs:
                raise RuntimeError('Wait for the current scan or cancel it.')
            if not self._matches:
                raise ValueError('No results to refine. Run First Scan.')
            kind = self.scan_type.currentText()
            if kind != self._scan_kind:
                raise ValueError('The value type cannot change between scans.')
            mode = self.refine_mode.currentText()
            expected = encode_value(self.scan_value.text(), kind) if mode == 'Exact' else None
            if expected is not None and len(expected) != len(self._matches[0][1]):
                raise ValueError('The new exact value must have the same byte width.')
            snapshot = list(self._matches)
            self._scan_cancel = threading.Event()
            self.scan_progress.setRange(0, 0)
            self.scan_label.setText('Refining %d addresses…' % len(snapshot))
            self._run('scan', pid, lambda _token: refine_process(
                pid, snapshot, kind, mode, expected=expected, cancel=self._scan_cancel))
        except Exception as exc:
            self.scan_label.setText(str(exc))

    def cancel_scan(self):
        if 'scan' in self._jobs:
            self._scan_cancel.set()
            self.scan_label.setText('Stopping scan…')

    def _render_matches(self, reason, scanned):
        _fill(self.results, [
            ('0x%016X' % address, decode_value(raw, self._scan_kind),
             raw.hex(' ').upper()) for address, raw in self._matches[:500]
        ])
        suffix = (' • ' + reason) if reason else ''
        self.scan_label.setText('%d matches; displaying first %d • %s examined%s'
            % (len(self._matches), min(len(self._matches), 500),
               ('%.1f MiB' % (scanned / 1048576.0)
                if scanned > 100000 else '%d addresses' % scanned), suffix))

    def read_hex(self):
        try:
            pid = self._pid_required()
            address = parse_address(self.address.text())
            size = int(self.byte_count.currentText())
            data = read_memory(pid, address, size)
            self._snapshot = (pid, address, data)
            self.hex_dump.setPlainText(format_hex(address, data))
            self.hex_edit.setPlainText(data.hex(' ').upper())
            self.hex_status.setText('Read %d bytes at 0x%016X.' % (len(data), address))
        except Exception as exc:
            self.hex_status.setText('Read failed: ' + str(exc))

    def write_hex(self):
        try:
            pid = self._pid_required()
            address = parse_address(self.address.text())
            if self._snapshot is None or (pid, address) != self._snapshot[:2]:
                raise ValueError('Read this address first to capture the original bytes.')
            original = self._snapshot[2]
            replacement = parse_hex(self.hex_edit.toPlainText())
            if len(replacement) != len(original):
                raise ValueError('Write must have the same number of bytes as the read.')
            if replacement == original:
                self.hex_status.setText('No byte changes to write.')
                return
            if read_memory(pid, address, len(original)) != original:
                raise RuntimeError('Memory changed since last read. Refresh before writing.')
            if QMessageBox.question(self, 'Confirm memory write',
                '%d bytes at 0x%016X in PID %d will change.\n\nBefore: %s\nAfter:  %s'
                % (len(replacement), address, pid,
                   original[:32].hex(' ').upper(), replacement[:32].hex(' ').upper()),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            done = write_memory(pid, address, replacement)
            if done != len(replacement):
                raise OSError('Not all bytes were written.')
            self._undo.append((pid, address, original, replacement))
            self._undo = self._undo[-32:]
            self.undo_button.setEnabled(True)
            self.hex_status.setText('Wrote %d bytes. Undo is available.' % done)
            self.read_hex()
        except Exception as exc:
            self.hex_status.setText('Write rejected: ' + str(exc))

    def undo_write(self):
        try:
            pid = self._pid_required()
            if not self._undo:
                raise ValueError('No writes to undo.')
            last_pid, address, before, after = self._undo[-1]
            if pid != last_pid:
                raise RuntimeError('The game process changed; undo is no longer safe.')
            if read_memory(pid, address, len(after)) != after:
                raise RuntimeError('Memory changed since the write; undo refused.')
            if QMessageBox.question(self, 'Undo memory change',
                'Restore the previous %d bytes at 0x%016X?' % (len(before), address),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            write_memory(pid, address, before)
            self._undo.pop()
            self.undo_button.setEnabled(bool(self._undo))
            self.address.setText('0x%016X' % address)
            self.read_hex()
            self.hex_status.setText('Restored the previous byte values.')
        except Exception as exc:
            self.hex_status.setText('Undo rejected: ' + str(exc))

    def _watch_toggled(self, *_):
        self.watch_timer.stop()
        if self.watch.isChecked() and not self._closed:
            ms = int(self.refresh_interval.currentText().split()[0])
            self.watch_timer.setInterval(ms)
            self.watch_timer.start()

    def _watch_tick(self):
        if self._closed or self._pid is None or self._snapshot is None:
            return
        pid, address, previous = self._snapshot
        try:
            same_address = parse_address(self.address.text()) == address
        except ValueError:
            same_address = False
        if pid != self._pid or not same_address:
            self.watch_timer.stop()
            self.watch.setChecked(False)
            return
        try:
            current = read_memory(pid, address, len(previous))
            if current != previous:
                self.hex_dump.setPlainText(format_hex(address, current))
                # Do not overwrite a pending manual hex edit during live polling.
                if self.hex_edit.toPlainText().strip().replace(' ', '').lower() == previous.hex():
                    self.hex_edit.setPlainText(current.hex(' ').upper())
                self._snapshot = (pid, address, current)
                self.hex_status.setText('Live value changed at 0x%016X.' % address)
        except Exception as exc:
            self.watch_timer.stop()
            self.watch.setChecked(False)
            self.hex_status.setText('Live refresh stopped: ' + str(exc))
