"""Developer tools for inspected process state and isolated network tests.

UI is constructed only after the existing Developer unlock. Reads/writes are
scoped to a verified Evolve.exe process; network test fixtures never connect
to public game services.
"""
from __future__ import annotations
import json
from pathlib import Path
import threading

from PySide6.QtCore import QObject,Signal,QRunnable,QThreadPool,Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,
 QLineEdit,QComboBox,QPushButton,QPlainTextEdit,QFileDialog,QMessageBox,
 QTabWidget,QSpinBox,QSlider)

from developer_memory import list_evolve_processes,read_memory,parse_address
from developer_runtime_lab import (parse_aob,signature_matches,take_snapshot,
 compare_snapshot,read_pointer_chain, sample_watchlist, runtime_xml_check)
from developer_network_lab import (parse_log_file,tcp_connections_for_pid,
 parse_protocol_fixture, simulate_network_conditions,loopback_service_probe,
 compare_session_events)
from developer_support_lab import create_support_archive,discover_logs


class _AsyncSignals(QObject):
    done=Signal(object,object,object)


class _Task(QRunnable):
    def __init__(self,task,tag,signals):
        super().__init__()
        self.task=task
        self.tag=tag
        self.signals=signals

    def run(self):
        try:
            self.signals.done.emit(self.tag,self.task(),None)
        except Exception as exc:
            self.signals.done.emit(self.tag,None,str(exc))


def button(label,callback):
    widget=QPushButton(label)
    widget.clicked.connect(lambda checked=False:callback())
    return widget


def row(*items):
    container=QHBoxLayout()
    for item in items:
        container.addWidget(item)
    return container


class DeveloperLabPanel(QWidget):
    def __init__(self,game_root_provider,parent=None):
        super().__init__(parent)
        self.game_root_provider=game_root_provider
        self.last_log=()
        self._service=None
        self.snapshots={}
        self._generation=0
        self._closed=False
        self.pool=QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals=_AsyncSignals(self)
        self.signals.done.connect(self._completed)
        root=QVBoxLayout(self)
        root.addWidget(QLabel('Read-only memory tracing and private/local '
                             'network testing. Live public services are never modified.'))
        self.tabs=QTabWidget()
        root.addWidget(self.tabs,1)
        self._memory_ui()
        self._network_ui()
        self._trace_ui()

    def _memory_ui(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        head=QHBoxLayout()
        self.pids=QComboBox()
        head.addWidget(QLabel('Evolve.exe PID'))
        head.addWidget(self.pids,1)
        head.addWidget(button('Refresh',self.refresh_pids))
        layout.addLayout(head)
        address=QHBoxLayout()
        self.address=QLineEdit('0x0')
        self.address.setPlaceholderText('Verified runtime address')
        self.length=QComboBox()
        self.length.addItems(('256','1024','4096','16384','65536'))
        address.addWidget(QLabel('Address'))
        address.addWidget(self.address,1)
        address.addWidget(QLabel('Snapshot bytes'))
        address.addWidget(self.length)
        layout.addLayout(address)
        actions=QHBoxLayout()
        actions.addWidget(button('Snapshot A',lambda:self.snapshot('A')))
        actions.addWidget(button('Snapshot B',lambda:self.snapshot('B')))
        actions.addWidget(button('Compare A/B',self.compare))
        layout.addLayout(actions)
        chain=QHBoxLayout()
        self.offsets=QLineEdit('0x0, 0x18')
        self.offsets.setPlaceholderText('Pointer offsets: 0x0, 0x18')
        chain.addWidget(QLabel('Pointer chain'))
        chain.addWidget(self.offsets,1)
        chain.addWidget(button('Trace',self.pointer))
        layout.addLayout(chain)
        sig=QHBoxLayout()
        self.signature=QLineEdit('48 8B ?? ??')
        sig.addWidget(QLabel('AOB signature in Snapshot A'))
        sig.addWidget(self.signature,1)
        sig.addWidget(button('Find',self.signature_search))
        layout.addLayout(sig)
        self.memory_output=QPlainTextEdit()
        self.memory_output.setReadOnly(True)
        self.memory_output.setPlaceholderText(
            'Snapshot differences, pointer chains, and signature matches appear here.')
        layout.addWidget(self.memory_output,1)
        self.tabs.addTab(page,'Memory Research')

    def _network_ui(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        actions=QHBoxLayout()
        actions.addWidget(button('Inspect Evolve TCP Connections',self.tcp))
        actions.addWidget(button('Open Relay Log…',self.open_log))
        actions.addWidget(button('Compare Two Logs…',self.compare_logs))
        layout.addLayout(actions)
        local=QHBoxLayout()
        self.port=QLineEdit('8080')
        local.addWidget(QLabel('Local test service port'))
        local.addWidget(self.port)
        local.addWidget(button('Test 127.0.0.1',self.local_probe))
        layout.addLayout(local)
        protocol=QHBoxLayout()
        protocol.addWidget(button('Open Protocol Fixture…',self.fixture))
        protocol.addWidget(button('Simulate Network Conditions',self.simulate))
        layout.addLayout(protocol)
        self.latency=QSlider()
        self.latency.setOrientation(Qt.Horizontal)
        self.latency.setRange(0,1000)
        self.loss=QSlider()
        self.loss.setOrientation(Qt.Horizontal)
        self.loss.setRange(0,100)
        layout.addWidget(QLabel('Simulated fixture latency (0–1000 ms)'))
        layout.addWidget(self.latency)
        layout.addWidget(QLabel('Simulated fixture packet loss (0–100%)'))
        layout.addWidget(self.loss)
        service=QHBoxLayout()
        service.addWidget(QLabel('Private mock service'))
        service.addWidget(button('Start Loopback Fixture',self.start_local_service))
        service.addWidget(button('Stop Fixture',self.stop_local_service))
        layout.addLayout(service)
        self.network_output=QPlainTextEdit()
        self.network_output.setReadOnly(True)
        layout.addWidget(self.network_output,1)
        self.tabs.addTab(page,'Relay / Protocol')

    def _trace_ui(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.addWidget(QLabel('Stage2Trace log flag discovery and support '
                               'captures. No undocumented runtime flags are guessed.'))
        layout.addWidget(button('Read Stage2Trace Status',self.trace))
        layout.addWidget(button('Export Redacted Support ZIP…',self.support))
        self.trace_output=QPlainTextEdit()
        self.trace_output.setReadOnly(True)
        layout.addWidget(self.trace_output,1)
        self.tabs.addTab(page,'Tracing / Support')

    def _pid(self):
        number=self.pids.currentData()
        if not isinstance(number,int) or number<=0:
            raise ValueError('Choose a running Evolve.exe process first.')
        return number

    def refresh_pids(self):
        try:
            current=self.pids.currentData()
            self.pids.clear()
            for pid in list_evolve_processes():
                self.pids.addItem('Evolve.exe • '+str(pid),pid)
            if current:
                idx=self.pids.findData(current)
                if idx>=0:self.pids.setCurrentIndex(idx)
        except (OSError,RuntimeError) as exc:
            self.memory_output.setPlainText(str(exc))

    def _run(self,name,task):
        self._generation+=1
        token=(self._generation,name)
        self.pool.start(_Task(task,token,self.signals))

    def _completed(self,token,result,error):
        if self._closed or token[0]!=self._generation:
            return
        target=self.memory_output if token[1]=='memory' else (
            self.trace_output if token[1]=='trace' else self.network_output)
        target.setPlainText('Error: '+str(error) if error else str(result))

    def snapshot(self,key):
        try:
            pid=self._pid()
            address=parse_address(self.address.text())
            count=int(self.length.currentText())
            def take():
                snap=take_snapshot(pid,address,count)
                self.snapshots[key]=snap
                return '%s: %d bytes at 0x%X • SHA256 %s'%(
                    key,len(snap.data),snap.address,snap.sha256)
            self._run('memory',take)
        except (OSError,ValueError) as exc:
            self.memory_output.setPlainText(str(exc))

    def compare(self):
        try:
            old,new=self.snapshots['A'],self.snapshots['B']
            result=compare_snapshot(old,new)
            self.memory_output.setPlainText(
                '%d bytes changed\n'%result['total_changed']+
                '\n'.join('0x%X: %02X -> %02X'%x for x in result['diffs']))
        except (KeyError,ValueError) as exc:
            self.memory_output.setPlainText('Take comparable A and B snapshots: '+str(exc))

    def pointer(self):
        try:
            pid=self._pid()
            base=parse_address(self.address.text())
            offsets=[int(s.strip(),0) for s in self.offsets.text().split(',') if s.strip()]
            self._run('memory',lambda:' -> '.join('0x%X'%x for x
                                                in read_pointer_chain(pid,base,offsets)))
        except (ValueError,OSError) as exc:
            self.memory_output.setPlainText(str(exc))

    def signature_search(self):
        try:
            snap=self.snapshots['A']
            found=signature_matches(snap.data,snap.address,parse_aob(self.signature.text()))
            self.memory_output.setPlainText('%d hit(s)\n'%len(found)+
                                            '\n'.join('0x%X'%x for x in found))
        except (ValueError,KeyError) as exc:
            self.memory_output.setPlainText('Take Snapshot A first: '+str(exc))

    def tcp(self):
        try:
            pid=self._pid()
            self._run('network',lambda:json.dumps(tcp_connections_for_pid(pid),indent=2))
        except (ValueError,OSError) as exc:
            self.network_output.setPlainText(str(exc))

    def open_log(self):
        file,_=QFileDialog.getOpenFileName(self,'Open Evolve session log','','Logs (*.log *.txt)')
        if file:
            try:
                self.last_log=parse_log_file(file)
                self.network_output.setPlainText(json.dumps(self.last_log,indent=2))
            except (OSError,ValueError) as exc:
                self.network_output.setPlainText(str(exc))

    def compare_logs(self):
        path,_=QFileDialog.getOpenFileName(self,'Select second session log','','Logs (*.log *.txt)')
        if path:
            try:
                report=compare_session_events(self.last_log,parse_log_file(path))
                self.network_output.setPlainText(json.dumps(report,indent=2))
            except (OSError,ValueError) as exc:
                self.network_output.setPlainText(str(exc))

    def start_local_service(self):
        try:
            if self._service is not None:
                raise ValueError('Local fixture is already running')
            from developer_private_services import LocalFixtureService
            self._service=LocalFixtureService({
                '/health':{'status':'ok','test_mode':True},
                '/session':{'status':'fixture','session':'offline-test-only'},
                '/relay':{'status':'fixture','relay':'localhost'},
            })
            endpoint=self._service.start()
            self.network_output.setPlainText(
                'Local mock started at '+endpoint+
                '\nGET /health, /session, /relay are synthetic JSON fixtures. '
                'Point only an explicitly configured private test client at '
                'this endpoint. No live game endpoint was changed.')
        except (OSError,ValueError) as exc:
            self.network_output.setPlainText(str(exc))

    def stop_local_service(self):
        if self._service is not None:
            self._service.stop()
            self._service=None
            self.network_output.setPlainText('Local fixture server stopped.')

    def local_probe(self):
        try:
            port=int(self.port.text())
            self._run('network',lambda:json.dumps(loopback_service_probe(port),indent=2))
        except (OSError,ValueError) as exc:
            self.network_output.setPlainText(str(exc))

    def fixture(self):
        path,_=QFileDialog.getOpenFileName(self,'Open local protocol fixture',
              '','Fixtures (*.json *.bin *.dat)')
        if path:
            try:
                p=Path(path)
                if p.stat().st_size>1024*1024:raise ValueError('Fixture too large')
                kind='JSON' if p.suffix.lower()=='.json' else 'Hex'
                self.network_output.setPlainText(json.dumps(
                    parse_protocol_fixture(p.read_bytes(),kind),indent=2))
            except (OSError,ValueError) as exc:
                self.network_output.setPlainText(str(exc))

    def simulate(self):
        report=simulate_network_conditions(self.last_log,latency_ms=self.latency.value(),
                                          loss_percent=self.loss.value())
        self.network_output.setPlainText(json.dumps(report[:80],indent=2))

    def trace(self):
        try:
            root=Path(self.game_root_provider())
            paths=[p for p in root.rglob('Stage2Trace*.log') if p.is_file() and not p.is_symlink()]
            if not paths:raise FileNotFoundError('No Stage2Trace log found in game directory')
            path=paths[0]
            if path.stat().st_size>8*1024*1024:raise ValueError('Trace log too large')
            lines=[line.strip()[:300] for line in path.read_text(
                    'utf-8',errors='replace').splitlines()
                    if '_mode=' in line or 'menu_mode=' in line]
            self.trace_output.setPlainText('\n'.join(lines[:250]) or
                                           'No trace mode lines available.')
        except (OSError,ValueError) as exc:
            self.trace_output.setPlainText(str(exc))

    def support(self):
        path,_=QFileDialog.getSaveFileName(self,'Create redacted support ZIP',
            'Evolve-Support-Redacted.zip','ZIP (*.zip)')
        if not path:return
        try:
            root=Path(self.game_root_provider())
            files=create_support_archive(root,path)
            self.trace_output.setPlainText(
                'Created redacted local support ZIP with %d logs.'%len(files))
        except (OSError,ValueError) as exc:
            self.trace_output.setPlainText(str(exc))

    def stop(self):
        self.stop_local_service()
        self._closed=True
        self._generation+=1
