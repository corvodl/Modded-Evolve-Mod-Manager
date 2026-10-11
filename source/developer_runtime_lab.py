"""Read-only runtime instrumentation for a user-owned Evolve.exe process.

Covers pointer chain validation, address signatures, bounded region snapshots,
memory watch sampling, and typed runtime snapshots. Never scans other processes,
patches executable code, or assumes game-specific offsets.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
import struct
import time

from developer_memory import read_memory, parse_address, parse_hex
from developer_memory_inspector import list_modules, list_regions, scan_process, encode_value, decode_value

MAX_SNAPSHOT_BYTES=256*1024
MAX_POINTER_DEPTH=8
MAX_WATCHES=128


def parse_aob(pattern):
    parts=str(pattern).strip().split()
    if not 1<=len(parts)<=128:
        raise ValueError('Use 1–128 hex bytes with optional ?? wildcards.')
    result=[]
    for token in parts:
        if token in ('??','?'):
            result.append(None)
        elif re.fullmatch(r'[0-9A-Fa-f]{2}',token):
            result.append(int(token,16))
        else:
            raise ValueError('Invalid signature byte '+token)
    if all(v is None for v in result):
        raise ValueError('Signature needs at least one fixed byte.')
    return tuple(result)


def signature_matches(data,base,signature,*,limit=256):
    if not isinstance(data,bytes) or len(data)>MAX_SNAPSHOT_BYTES:
        raise ValueError('Signature scanner buffer exceeds 256 KiB.')
    signature=tuple(signature)
    if not signature or len(signature)>len(data):
        return ()
    addresses=[]
    for offset in range(len(data)-len(signature)+1):
        if all(value is None or data[offset+i]==value for i,value in enumerate(signature)):
            addresses.append(int(base)+offset)
            if len(addresses)>=limit:
                break
    return tuple(addresses)


def read_pointer_chain(pid,base,offsets,*,pointer_width=8):
    """Trace up to 8 dereferences; does not guess pointer chains."""
    if pointer_width not in (4,8) or len(offsets)>MAX_POINTER_DEPTH:
        raise ValueError('Unsupported pointer width/depth.')
    address=parse_address(base)
    hops=[address]
    kind='<Q' if pointer_width==8 else '<I'
    for offset in offsets:
        if not isinstance(offset,int) or abs(offset)>0x1000000:
            raise ValueError('Invalid pointer offset.')
        value=struct.unpack(kind,read_memory(pid,address,pointer_width))[0]
        if not value:
            raise ValueError('Null pointer in chain.')
        address=parse_address(value+offset)
        hops.append(address)
    return tuple(hops)


@dataclass(frozen=True)
class MemorySnapshot:
    pid: int
    address: int
    data: bytes
    timestamp: float
    sha256: str


def take_snapshot(pid,address,length):
    if not 1<=int(length)<=MAX_SNAPSHOT_BYTES:
        raise ValueError('Snapshot must be 1 to 256 KiB.')
    # Existing read_memory limits reads to 4 KiB.
    address=parse_address(address)
    buffer=bytearray()
    for offset in range(0,int(length),4096):
        size=min(4096,int(length)-offset)
        chunk=read_memory(pid,address+offset,size)
        if len(chunk)!=size:
            raise OSError('Partial memory snapshot.')
        buffer.extend(chunk)
    data=bytes(buffer)
    return MemorySnapshot(int(pid),address,data,time.time(),hashlib.sha256(data).hexdigest())


def compare_snapshot(old,new,*,limit=512):
    if (old.pid!=new.pid or old.address!=new.address
            or len(old.data)!=len(new.data)):
        raise ValueError('Compare the same PID, address, and byte count.')
    offsets=[i for i,(a,b) in enumerate(zip(old.data,new.data)) if a!=b]
    return {'total_changed':len(offsets),
            'diffs':tuple((old.address+i,old.data[i],new.data[i]) for i in offsets[:limit])}


def sample_watchlist(pid,watches):
    if len(watches)>MAX_WATCHES:
        raise ValueError('Too many live watches.')
    result=[]
    for label,address,kind in watches:
        if len(label)>100:
            raise ValueError('Watch label is too long.')
        bytes_pattern=encode_value('0',kind)
        raw=read_memory(pid,parse_address(address),len(bytes_pattern))
        result.append((label,parse_address(address),kind,decode_value(raw,kind)))
    return tuple(result)


def module_summary(pid):
    return tuple(list_modules(pid))


def process_regions(pid):
    return tuple(list_regions(pid))


def runtime_xml_check(xml_values,sampled_values):
    """Pure comparison; no assumptions about CryEngine's live address layout."""
    result=[]
    for key,expected in xml_values.items():
        observed=sampled_values.get(key)
        result.append((key,expected,observed,observed is not None and str(expected)==str(observed)))
    return tuple(result)
