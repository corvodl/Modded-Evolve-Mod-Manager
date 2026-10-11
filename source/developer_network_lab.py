"""Private-session relay and network diagnostics for Evolve developer workflows.

Observes Windows TCP connections and user-provided log files. Does NOT
intercept, forge, replay or redirect traffic to live third-party services.
Local transport tests are bound exclusively to loopback.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import socket
import time

MAX_LOG=8*1024*1024
STAGES=('client','backend','party','lobby','relay','match','disconnect')
PATTERNS=(
 ('relay',re.compile(r'\brelay\b',re.I)),
 ('party',re.compile(r'\bparty\b|\bsession\b',re.I)),
 ('lobby',re.compile(r'\blobby\b|\bmatchmaking\b',re.I)),
 ('backend',re.compile(r'\bprofile service\b|\bbackend\b|\bhttp\b',re.I)),
 ('match',re.compile(r'\bmatch\b|\bhost\b',re.I)),
)
STATUS_ERROR=re.compile(r'\b(?:error|fail(?:ed|ure)?|timeout|disconnect|refused|unable)\b',re.I)
IP_RE=re.compile(r'(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)')
AUTH_RE=re.compile(r'(?i)(authorization|token|password|secret|key)(\s*[:=]\s*)(\S+)')


def redact(line):
    line=AUTH_RE.sub(r'\1\2[REDACTED]',line)
    return IP_RE.sub('[IP]',line)


def parse_relay_log(text,*,limit=400):
    if len(text)>MAX_LOG:
        raise ValueError('Log exceeds safe parser size')
    results=[]
    for number,line in enumerate(text.splitlines(),1):
        kind=next((name for name,pattern in PATTERNS if pattern.search(line)),None)
        if kind:
            results.append({'line':number,'stage':kind,
                            'error':bool(STATUS_ERROR.search(line)),
                            'message':redact(line[:400])})
            if len(results)>=limit:
                break
    return tuple(results)


def parse_log_file(path):
    p=Path(path)
    if p.stat().st_size>MAX_LOG:
        raise ValueError('Log too large for relay inspection')
    return parse_relay_log(p.read_text('utf-8',errors='replace'))


def tcp_connections_for_pid(pid):
    """Read established TCP endpoint metadata for one Evolve.exe PID."""
    from developer_memory import _game_handle
    pid=int(pid)
    k,handle=_game_handle(pid,0x1000)  # limited query / validated executable
    k.CloseHandle(handle)
    if not 0<pid<2**32:
        raise ValueError('Invalid process id')
    command=(
        '$p = '+str(pid)+'; '
        'Get-NetTCPConnection -OwningProcess $p -ErrorAction SilentlyContinue '
        '| Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State '
        '| ConvertTo-Json -Compress'
    )
    completed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive',
        '-Command',command],capture_output=True,text=True,timeout=10)
    if completed.returncode!=0:
        raise OSError('Windows TCP connection enumeration failed')
    decoded=json.loads(completed.stdout or '[]')
    if isinstance(decoded,dict):
        decoded=[decoded]
    return tuple({
        'local_port':int(x.get('LocalPort',0)),
        'remote_port':int(x.get('RemotePort',0)),
        'state':str(x.get('State','Unknown')),
        'remote_address':str(x.get('RemoteAddress','')),
    } for x in decoded[:300])


def parse_protocol_fixture(raw,encoding='JSON'):
    """Inspect a local capture/fixture, not decrypt protected online traffic."""
    if len(raw)>1024*1024:
        raise ValueError('Protocol fixture limited to 1 MiB')
    if encoding=='JSON':
        parsed=json.loads(raw.decode('utf-8'))
        if not isinstance(parsed,(list,dict)):
            raise ValueError('Expected structured JSON event/messages')
        return {'message_type':'structured JSON','preview':json.dumps(parsed,indent=2)[:10000]}
    if encoding=='Hex':
        return {'message_type':'unclassified bytes','length':len(raw),
                'preview':raw[:256].hex(' ').upper()}
    raise ValueError('Unsupported fixture encoding')


def simulate_network_conditions(events,*,latency_ms=0,jitter_ms=0,loss_percent=0):
    """Deterministic offline model for fixture event delivery only.

    No packets are delayed/dropped on any operating-system network interface.
    """
    if not (0<=latency_ms<=5000 and 0<=jitter_ms<=2000 and 0<=loss_percent<=100):
        raise ValueError('Network simulation parameters are out of range')
    results=[]
    for index,item in enumerate(events[:10000]):
        # Deterministic distribution for reproducible private tests.
        discard=((index*37+11)%100)<loss_percent
        jitter=((index*17)%7-3)*jitter_ms/3
        results.append({'index':index,'delivered':not discard,
                        'expected_delay_ms':max(0,latency_ms+jitter),
                        'event':item})
    return tuple(results)


def loopback_service_probe(port,*,timeout=2):
    """Only loopback endpoints; cannot send requests to a public service."""
    port=int(port)
    if not 1<=port<=65535:
        raise ValueError('Invalid local test port')
    with socket.create_connection(('127.0.0.1',port),timeout=timeout):
        return {'host':'127.0.0.1','port':port,'tcp':'connected'}


def compare_session_events(host,client):
    def signature(x):
        return (x.get('stage'),bool(x.get('error')))
    left=[signature(x) for x in host[:2000]]
    right=[signature(x) for x in client[:2000]]
    return {'host_events':len(left),'client_events':len(right),
            'first_difference':next((i for i,(a,b) in enumerate(zip(left,right)) if a!=b),
                                    None),
            'host_errors':sum(flag for _,flag in left),
            'client_errors':sum(flag for _,flag in right)}


TRACE_ENV=re.compile(r'^(?:STAGE2TRACE|EVOLVE|MODDED_EVOLVE)_[A-Z][A-Z0-9_]{1,62}$')

def validate_trace_environment(name,value):
    """Restrict launcher-side debug flags to non-sensitive project prefixes."""
    name=str(name).strip().upper()
    if not TRACE_ENV.fullmatch(name):
        raise ValueError('Trace variable must use STAGE2TRACE_, EVOLVE_, or MODDED_EVOLVE_ prefix.')
    value=str(value).strip()
    if value not in ('0','1'):
        raise ValueError('Trace options support only boolean 0 or 1.')
    return name,value
