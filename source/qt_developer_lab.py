"""Developer tools for inspected process state and isolated network tests.

UI is constructed only after the existing Developer unlock. Reads/writes are
scoped to a verified Evolve.exe process; network test fixtures never connect
to public game services.
"""
from __future__ import annotations
import json
from pathlib import Path
import threading

from PySide6.QtCore import QObject,Signal,QRunnable,QThreadPool,Qt,QTimer
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
from developer_diagnostics_116 import (make_watch_profile, resolve_watch_profile,
    process_build, relay_timeline, run_private_scenario)


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
    def __init__(self,game_root_provider,parent=None,trace_env_callback=None,
                 write_permission=None):
        super().__init__(parent)
        self.game_root_provider=game_root_provider
        self.trace_env_callback=trace_env_callback
        self._write_permission=write_permission
        self.last_log=()
        self._service=None
        self.snapshots={}
        self.entity_watches=[]
        self.entity_history=[]
        self.entity_timer=QTimer(self)
        self.entity_timer.setInterval(1000)
        self.entity_timer.timeout.connect(self._record_entities)
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
        self._entity_ui()
        self._trace_ui()
        from qt_game_events import GameEventsPanel
        self.events_panel = GameEventsPanel(self)
        self.tabs.addTab(self.events_panel, 'Game Events')

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
        chain.addWidget(button('Find Pointers',self.pointer_scan))
        layout.addLayout(chain)
        sig=QHBoxLayout()
        self.signature=QLineEdit('48 8B ?? ??')
        sig.addWidget(QLabel('AOB signature in Snapshot A'))
        sig.addWidget(self.signature,1)
        sig.addWidget(button('Find',self.signature_search))
        layout.addLayout(sig)
        patch=QHBoxLayout()
        self.patch_data=QLineEdit()
        self.patch_data.setPlaceholderText('Replacement hex bytes for Snapshot A')
        patch.addWidget(self.patch_data,1)
        patch.addWidget(button('Export Patch Preset…',self.export_patch))
        self.apply_patch_button = button('Apply Patch…',self.apply_patch)
        self.apply_patch_button.setEnabled(False)
        patch.addWidget(self.apply_patch_button)
        layout.addLayout(patch)
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
        actions.addWidget(button('Timeline Multiple Logs…',self.timeline_logs))
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
        protocol.addWidget(button('Run Private Scenario…',self.private_scenario))
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

    def _entity_ui(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.addWidget(QLabel(
            'Watch known typed entity addresses in an offline session. '
            'No character/entity offsets are assumed or discovered automatically.'))
        controls=QHBoxLayout()
        self.entity_label=QLineEdit()
        self.entity_label.setPlaceholderText('Name, e.g. Goliath Health')
        controls.addWidget(self.entity_label,1)
        self.entity_address=QLineEdit()
        self.entity_address.setPlaceholderText('0x…')
        controls.addWidget(self.entity_address,1)
        self.entity_type=QComboBox()
        self.entity_type.addItems(('Float','4 Bytes (signed)','4 Bytes (unsigned)',
                                   '8 Bytes (signed)','Double'))
        controls.addWidget(self.entity_type,1)
        controls.addWidget(button('Add Watch',self.add_entity_watch))
        layout.addLayout(controls)
        actions=QHBoxLayout()
        actions.addWidget(button('Sample Once',self._record_entities))
        actions.addWidget(button('Start Recorder',self.start_entity_recorder))
        actions.addWidget(button('Stop',self.entity_timer.stop))
        actions.addWidget(button('Compare with XML…',self.compare_xml_values))
        actions.addWidget(button('Export Events…',self.export_entity_events))
        actions.addWidget(button('Save Watch Profile…',self.save_watch_profile))
        actions.addWidget(button('Load Watch Profile…',self.load_watch_profile))
        layout.addLayout(actions)
        self.entity_output=QPlainTextEdit()
        self.entity_output.setReadOnly(True)
        layout.addWidget(self.entity_output,1)
        self.tabs.addTab(page,'Entity Watches / Events')

    def add_entity_watch(self):
        try:
            from developer_memory_inspector import encode_value
            name=self.entity_label.text().strip()
            if not name or len(name)>80 or len(self.entity_watches)>=32:
                raise ValueError('Enter a label (80 characters max, 32 watches max).')
            address=parse_address(self.entity_address.text())
            kind=self.entity_type.currentText()
            encode_value('0',kind)
            if any(w[0]==name for w in self.entity_watches):
                raise ValueError('Watch names must be unique.')
            self.entity_watches.append((name,address,kind))
            self.entity_output.setPlainText(
                '%d typed watches configured.'%len(self.entity_watches))
        except (OSError,ValueError) as exc:
            self.entity_output.setPlainText(str(exc))

    def save_watch_profile(self):
        try:
            if not self.entity_watches:
                raise ValueError('Add at least one typed module-relative watch first.')
            from developer_memory_inspector import list_modules
            pid=self._pid()
            profile=make_watch_profile(self.entity_watches,process_build(pid),list_modules(pid))
            path,_=QFileDialog.getSaveFileName(self,'Save build-checked watch profile',
                  'Evolve-Memory-Watches.json','JSON (*.json)')
            if path:
                Path(path).write_text(json.dumps(profile,indent=2),encoding='utf-8')
                self.entity_output.setPlainText('Profile saved with an Evolve.exe build fingerprint.')
        except (OSError,ValueError,PermissionError) as exc:
            self.entity_output.setPlainText('Could not save watch profile: '+str(exc))

    def load_watch_profile(self):
        path,_=QFileDialog.getOpenFileName(self,'Load build-checked watch profile','',
                  'JSON (*.json)')
        if not path:
            return
        try:
            from developer_memory_inspector import list_modules
            file=Path(path)
            if file.stat().st_size>256*1024:
                raise ValueError('Watch profile exceeds 256 KiB.')
            pid=self._pid()
            restored=resolve_watch_profile(json.loads(file.read_text('utf-8')),
                                process_build(pid),list_modules(pid))
            self.entity_timer.stop()
            self.entity_watches=list(restored)
            self.entity_history.clear()
            self.entity_output.setPlainText('%d module-relative watches loaded and build-checked. '
                'Recorder remains stopped; start explicitly to sample live values.'%len(restored))
        except (OSError,ValueError,PermissionError,TypeError) as exc:
            self.entity_output.setPlainText('Profile rejected: '+str(exc))

    def start_entity_recorder(self):
        try:
            self._pid()
            if not self.entity_watches:
                raise ValueError('Add a typed watch first.')
            self.entity_timer.start()
            self._record_entities()
        except (OSError,ValueError) as exc:
            self.entity_output.setPlainText(str(exc))

    def _record_entities(self):
        if self._closed:
            self.entity_timer.stop()
            return
        try:
            import time
            pid=self._pid()
            values=sample_watchlist(pid,self.entity_watches)
            event={'time':round(time.time(),3),'pid':pid,
                   'values':{label:value for label,addr,kind,value in values}}
            self.entity_history.append(event)
            self.entity_history=self.entity_history[-5000:]
            self.entity_output.setPlainText(
                json.dumps(event,indent=2)+'\n\n%d samples recorded.'%
                len(self.entity_history))
        except (OSError,ValueError) as exc:
            self.entity_timer.stop()
            self.entity_output.setPlainText('Recorder stopped: '+str(exc))

    def compare_xml_values(self):
        path,_=QFileDialog.getOpenFileName(
            self,'Compare XML values to runtime watches','','XML (*.xml)')
        if not path:return
        try:
            from studio_tools import numeric_fields
            file=Path(path)
            if file.stat().st_size>1024*1024:
                raise ValueError('XML comparison limited to 1 MiB')
            xml=file.read_text('utf-8-sig')
            expected={field.attribute:field.value for field in numeric_fields(xml)}
            if not self.entity_history:
                raise ValueError('Sample the entity watchlist first.')
            observations=self.entity_history[-1]['values']
            report=runtime_xml_check(expected,observations)
            report=[row for row in report if row[2] is not None]
            self.entity_output.setPlainText(
                json.dumps(report[:100],indent=2) or
                'Name a watch after an XML attribute to compare its runtime value.')
        except (OSError,ValueError) as exc:
            self.entity_output.setPlainText(str(exc))

    def export_entity_events(self):
        if not self.entity_history:
            self.entity_output.setPlainText('Record at least one sample first.')
            return
        path,_=QFileDialog.getSaveFileName(self,
            'Export runtime event observations','Evolve-Events.json',
            'JSON (*.json)')
        if not path:return
        try:
            with Path(path).open('x',encoding='utf-8') as file:
                json.dump({'format':'evolve-local-runtime-watch-v1',
                           'events':self.entity_history},file,indent=2)
        except (OSError,ValueError) as exc:
            self.entity_output.setPlainText(str(exc))

    def _trace_ui(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.addWidget(QLabel('Stage2Trace log flag discovery and support '
                               'captures. No undocumented runtime flags are guessed.'))
        flags=QHBoxLayout()
        self.trace_key=QLineEdit()
        self.trace_key.setPlaceholderText('STAGE2TRACE_EXAMPLE_FLAG')
        self.trace_value=QComboBox()
        self.trace_value.addItem('Enabled','1')
        self.trace_value.addItem('Disabled','0')
        flags.addWidget(self.trace_key,2)
        flags.addWidget(self.trace_value,1)
        flags.addWidget(button('Set Offline Launch Flag',self.set_trace_flag))
        layout.addLayout(flags)
        layout.addWidget(QLabel(
            'Only actual flags recognized by your installed DLL have an effect. '
            'These settings apply to this manager session only, and only to '
            'an offline modded launcher process.'))
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

    def pointer_scan(self):
        try:
            from developer_runtime_lab import reverse_pointer_scan
            pid=self._pid()
            target=parse_address(self.address.text())
            self._run('memory',lambda:json.dumps(
                reverse_pointer_scan(pid,target),indent=2))
        except (OSError,ValueError) as exc:
            self.memory_output.setPlainText(str(exc))

    def export_patch(self):
        try:
            from developer_runtime_lab import module_patch_preset
            from developer_memory import parse_hex
            snapshot=self.snapshots.get('A')
            if snapshot is None:
                raise ValueError('Take a module memory Snapshot A first.')
            new=parse_hex(self.patch_data.text())
            preset=module_patch_preset(snapshot.pid,snapshot.address,
                                       snapshot.data,new)
            path,_=QFileDialog.getSaveFileName(self,'Export offline patch preset',
                                                'Evolve-Patch.json','JSON (*.json)')
            if not path:return
            with Path(path).open('x',encoding='utf-8') as out:
                json.dump(preset,out,indent=2)
            self.memory_output.setPlainText(
                'Patch preset exported. Original bytes and module RVA preserved.')
        except (OSError,ValueError) as exc:
            self.memory_output.setPlainText('Cannot export patch: '+str(exc))

    def set_write_enabled(self,enabled):
        self.apply_patch_button.setEnabled(bool(enabled))

    def apply_patch(self):
        try:
            from developer_runtime_lab import resolve_patch_preset
            from developer_memory import write_memory
            pid=self._pid()
            if self._write_permission is None:
                raise PermissionError('Memory writes are not enabled for this session.')
            mode=self._write_permission()
            path,_=QFileDialog.getOpenFileName(
                self,'Import offline memory preset','','JSON (*.json)')
            if not path:return
            file=Path(path)
            if file.stat().st_size>8192:
                raise ValueError('Patch preset exceeds size limit.')
            data=json.loads(file.read_text('utf-8'))
            address,before,after=resolve_patch_preset(pid,data)
            if QMessageBox.question(self,'Apply isolated memory patch',
                'Write %d bytes at 0x%X in Evolve PID %d? '
                'Session write policy: '+mode+'. Confirm this change?'%
                (len(after),address,pid),
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                return
            written=write_memory(pid,address,after)
            if written!=len(after):
                raise OSError('Partial memory patch write.')
            self.memory_output.setPlainText(
                'Patched %d bytes. Original bytes preserved in the preset; '
                'restore them through the existing Hex Editor undo/restore workflow.'
                %written)
        except (OSError,ValueError,PermissionError) as exc:
            self.memory_output.setPlainText('Patch refused: '+str(exc))

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

    def timeline_logs(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Choose diagnostic logs to correlate','',
                                             'Logs (*.log *.txt)')
        if not paths:
            return
        try:
            result=relay_timeline(paths)
            self.network_output.setPlainText(json.dumps(result,indent=2))
        except (OSError,ValueError) as exc:
            self.network_output.setPlainText('Timeline failed: '+str(exc))

    def private_scenario(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose offline scenario JSON','',
                                           'JSON (*.json)')
        if not path:
            return
        try:
            source=Path(path)
            if source.stat().st_size>1024*1024:
                raise ValueError('Scenario exceeds 1 MiB.')
            report=run_private_scenario(json.loads(source.read_text('utf-8')))
            self.network_output.setPlainText(json.dumps(report,indent=2))
        except (OSError,ValueError,TypeError) as exc:
            self.network_output.setPlainText('Offline scenario rejected: '+str(exc))

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

    def set_trace_flag(self):
        try:
            from developer_network_lab import validate_trace_environment
            if self.trace_env_callback is None:
                raise RuntimeError('Launcher environment control is unavailable.')
            name,value=validate_trace_environment(
                self.trace_key.text(),self.trace_value.currentData())
            self.trace_env_callback(name,value)
            self.trace_output.setPlainText(
                '%s=%s set for this manager session. Start an isolated '
                'offline modded launch to apply it. Restart the game if already running.'
                %(name,value))
        except (ValueError,RuntimeError) as exc:
            self.trace_output.setPlainText(str(exc))

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
        self.entity_timer.stop()
        self.stop_local_service()
        self._closed=True
        self._generation+=1
