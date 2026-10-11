"""v1.1.16 scoped developer diagnostics: build-checked watches, timelines and fixtures.

Only local user-provided files or the explicitly verified Evolve.exe process are
inspected. Network scenarios never send, replay or redirect live traffic.
"""
from __future__ import annotations
from datetime import datetime
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import re

from developer_memory import _game_handle, parse_address
from developer_network_lab import parse_relay_log, redact
from developer_memory_inspector import TYPES

MAX_LOGS = 12
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 5000
_TIME = re.compile(r'(?<!\d)(?:(\d{4}-\d\d-\d\d)[ T])?(\d\d):(\d\d):(\d\d)(?:[.,](\d{1,6}))?')


def process_build(pid):
    """Hash the executable of a verified Evolve.exe PID (not its loaded modules)."""
    k, handle = _game_handle(int(pid), 0x1000)
    try:
        k.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD))
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        target = ctypes.create_unicode_buffer(32768)
        count = wintypes.DWORD(len(target))
        if not k.QueryFullProcessImageNameW(handle, 0, target, ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        file = Path(target.value)
    finally:
        k.CloseHandle(handle)
    if file.stat().st_size > 256*1024*1024:
        raise ValueError('Executable too large for build verification.')
    sha = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            sha.update(block)
    return {'name': file.name, 'size': file.stat().st_size, 'sha256': sha.hexdigest()}


def make_watch_profile(watches, build, modules):
    """Store module-relative offsets so ASLR does not invalidate saved watches."""
    if not isinstance(build,dict) or not re.fullmatch(r'[0-9a-f]{64}',str(build.get('sha256',''))):
        raise ValueError('Missing verified Evolve executable SHA-256.')
    if not isinstance(watches,(list,tuple)) or not 1<=len(watches)<=128:
        raise ValueError('Choose 1–128 watches.')
    saved=[]
    for label,address,kind in watches:
        if not isinstance(label,str) or not label or len(label)>100 or kind not in TYPES or TYPES[kind] is None:
            raise ValueError('Watches must have short names and supported numeric types.')
        address=parse_address(address)
        match=next((m for m in modules if m['base']<=address<m['base']+m['size']),None)
        if match is None:
            raise ValueError('Watch '+label+' is heap/absolute memory, not a relocatable module address.')
        saved.append({'label':label,'module':match['name'],
                      'offset':address-match['base'],'type':kind})
    if len(set(x['label'] for x in saved))!=len(saved):
        raise ValueError('Watch labels must be unique.')
    return {'format':'evolve-memory-watch-profile','version':1,
            'build':{'sha256':build['sha256'],'size':int(build['size'])},
            'watches':saved}


def resolve_watch_profile(data, build, modules):
    """Reject mismatched game versions BEFORE translating or sampling pointers."""
    if not isinstance(data,dict) or data.get('format')!='evolve-memory-watch-profile' or data.get('version')!=1:
        raise ValueError('Not an Evolve watch profile.')
    expected=data.get('build')
    if not isinstance(expected,dict) or expected.get('sha256')!=build.get('sha256') or expected.get('size')!=build.get('size'):
        raise ValueError('Evolve.exe version/build mismatch: profile not loaded.')
    watches=data.get('watches')
    if not isinstance(watches,list) or not 1<=len(watches)<=128:
        raise ValueError('Invalid watch list size.')
    index={}
    for module in modules:
        index.setdefault(module['name'].casefold(),[]).append(module)
    loaded=[]
    for row in watches:
        if not isinstance(row,dict):
            raise ValueError('Malformed watch row.')
        label=row.get('label')
        kind=row.get('type')
        name=row.get('module')
        offset=row.get('offset')
        if not isinstance(label,str) or not label or len(label)>100 or kind not in TYPES or TYPES[kind] is None:
            raise ValueError('Invalid watch name/type.')
        if not isinstance(name,str) or not name or not isinstance(offset,int) or offset<0:
            raise ValueError('Invalid module offset.')
        matching=index.get(name.casefold(),[])
        if len(matching)!=1 or offset>=matching[0]['size']:
            raise ValueError('Module missing, ambiguous or changed: '+name)
        loaded.append((label,parse_address(matching[0]['base']+offset),kind))
    if len(set(x[0] for x in loaded))!=len(loaded):
        raise ValueError('Duplicate profile labels.')
    return loaded


def _seconds(line):
    found=_TIME.search(line[:120])
    if not found:
        return None, None
    day,hour,minute,second,fraction=found.groups()
    try:
        point=(int(hour)*3600+int(minute)*60+int(second)+
               float('0.'+(fraction or '0')))
        if day:
            point+=datetime.fromisoformat(day).toordinal()*86400
        if not (0<=int(hour)<24 and 0<=int(minute)<60 and 0<=int(second)<60):
            return None,None
        return point,found.group(0)
    except ValueError:
        return None,None


def relay_timeline(sources):
    """Merge redacted diagnostic log events by real timestamps where available.

    Untimed events retain original file/line order rather than fabricated time.
    """
    if not isinstance(sources,(list,tuple)) or not 1<=len(sources)<=MAX_LOGS:
        raise ValueError('Choose 1–12 diagnostic logs.')
    output=[]
    for log_index,source in enumerate(sources):
        path=Path(source)
        if not path.is_file() or path.is_symlink() or path.stat().st_size>MAX_LOG_BYTES:
            raise ValueError('Invalid or oversized diagnostic log.')
        for e in parse_relay_log(path.read_text('utf-8',errors='replace'),limit=MAX_EVENTS):
            value,label=_seconds(e['message'])
            output.append({'file':path.name,'file_order':log_index,
                           'line':e['line'],'timestamp':label,
                           'seconds':value,'stage':e['stage'],'error':e['error'],
                           'message':redact(e['message'])})
            if len(output)>=MAX_EVENTS:
                break
        if len(output)>=MAX_EVENTS:
            break
    timed=sum(1 for e in output if e['seconds'] is not None)
    if timed==len(output):
        output.sort(key=lambda e:(e['seconds'],e['file_order'],e['line']))
    else:
        # Explicitly avoid pretending incomparable time formats are aligned.
        output.sort(key=lambda e:(e['file_order'],e['line']))
    transitions=[]
    for index,e in enumerate(output):
        previous=output[index-1] if index else None
        if previous and e['stage']!=previous['stage']:
            delta=(e['seconds']-previous['seconds'] if
                   e['seconds'] is not None and previous['seconds'] is not None else None)
            transitions.append({'from':previous['stage'],'to':e['stage'],
                                'seconds':round(delta,3) if delta is not None and delta>=0 else None})
    return {'format':'evolve-redacted-relay-timeline','total':len(output),
            'timed':timed,'mode':'timestamp' if timed==len(output) else 'file/line order (mixed or missing timestamps)',
            'errors':sum(e['error'] for e in output),
            'stages':{stage:sum(e['stage']==stage for e in output)
                      for stage in ('client','backend','party','lobby','relay','match','disconnect')},
            'transitions':transitions[:200],'events':output}


def validate_private_scenario(value):
    """Declarative GET fixture tests only; no arbitrary hosts/URLs or packets."""
    if not isinstance(value,dict) or value.get('format')!='evolve-private-scenario-v1':
        raise ValueError('Scenario format must be evolve-private-scenario-v1.')
    steps=value.get('steps')
    if not isinstance(steps,list) or not 1<=len(steps)<=200:
        raise ValueError('Choose 1–200 private fixture steps.')
    validated=[]
    for row in steps:
        if not isinstance(row,dict):
            raise ValueError('Invalid scenario step.')
        path=row.get('path')
        if (not isinstance(path,str) or not path.startswith('/') or
                path.startswith('//') or '..' in path or '://' in path or len(path)>120):
            raise ValueError('Only relative local HTTP paths are supported.')
        status=row.get('status',200)
        delay=row.get('delay_ms',0)
        loss=row.get('drop',False)
        if type(status) is not int or not 100<=status<=599 or type(delay) is not int or not 0<=delay<=10000 or type(loss) is not bool:
            raise ValueError('Invalid status, delay or packet-drop value.')
        body=row.get('body',{})
        if len(json.dumps(body).encode('utf-8'))>65536:
            raise ValueError('Fixture response exceeds 64 KiB.')
        validated.append({'path':path,'status':status,'delay_ms':delay,'drop':loss,'body':body})
    return validated


def run_private_scenario(value):
    """Deterministic local response/failure simulation; zero network IO."""
    steps=validate_private_scenario(value)
    clock=0
    events=[]
    for number,step in enumerate(steps,1):
        clock+=step['delay_ms']
        events.append({'step':number,'at_ms':clock,'request':'GET '+step['path'],
                       'result':'timeout / dropped' if step['drop'] else 'HTTP '+str(step['status']),
                       'body':None if step['drop'] else step['body']})
    return {'local_simulation_only':True,'steps':len(events),
            'total_delay_ms':clock,'dropped':sum(e['body'] is None for e in events),
            'events':events}
