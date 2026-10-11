"""Windows-only, Evolve.exe-scoped process map and exact-value scanning.

All long scans run outside the Qt GUI thread. Neither process memory nor
scan findings are persisted or sent over the network.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import struct
import threading

from developer_memory import _game_handle, read_memory, parse_address, parse_hex

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
WRITABLE = {0x04, 0x08, 0x40, 0x80}
MAX_MATCHES = 10000
MAX_SCAN_BYTES = 2 * 1024 * 1024 * 1024
CHUNK = 256 * 1024

TYPES = {
    '4 Bytes (signed)': '<i',
    '4 Bytes (unsigned)': '<I',
    '8 Bytes (signed)': '<q',
    'Float': '<f',
    'Double': '<d',
    'Hex Bytes': None,
    'Text (UTF-8)': None,
}


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p),
        ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wintypes.DWORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', wintypes.DWORD),
        ('Protect', wintypes.DWORD),
        ('Type', wintypes.DWORD),
    ]


class MODULEINFO(ctypes.Structure):
    _fields_ = [
        ('lpBaseOfDll', ctypes.c_void_p),
        ('SizeOfImage', wintypes.DWORD),
        ('EntryPoint', ctypes.c_void_p),
    ]


def protection(flags):
    access = flags & 0xff
    if flags & PAGE_GUARD or access in (0, PAGE_NOACCESS):
        return False, False
    return access in READABLE, access in WRITABLE


def encode_value(text, kind):
    if kind not in TYPES:
        raise ValueError('Unsupported value type.')
    value = str(text).strip()
    if not value:
        raise ValueError('Enter a value to scan.')
    if kind == 'Hex Bytes':
        return parse_hex(value)
    if kind == 'Text (UTF-8)':
        encoded = value.encode('utf-8')
        if len(encoded) > 256:
            raise ValueError('Text scan supports at most 256 UTF-8 bytes.')
        return encoded
    fmt = TYPES[kind]
    try:
        number = float(value) if kind in ('Float', 'Double') else int(value, 0)
        if isinstance(number, float) and not math.isfinite(number):
            raise ValueError('Enter a finite numeric value.')
        return struct.pack(fmt, number)
    except (ValueError, OverflowError, struct.error) as exc:
        raise ValueError('Value is out of range for ' + kind + '.') from exc


def decode_value(data, kind):
    fmt = TYPES.get(kind)
    if fmt is None:
        if kind == 'Text (UTF-8)':
            return data.decode('utf-8', errors='replace')
        return data.hex(' ').upper()
    if len(data) != struct.calcsize(fmt):
        return data.hex(' ').upper()
    return str(struct.unpack(fmt, data)[0])


def format_hex(address, data):
    """Readable hex dump with ASCII side-by-side."""
    rows = []
    for i in range(0, len(data), 16):
        block = data[i:i+16]
        hexpart = ' '.join('%02X' % byte for byte in block)
        ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in block)
        rows.append('0x%016X  %-47s  %s' % (address+i, hexpart, ascii_part))
    return '\n'.join(rows)


def _reader(k, handle):
    k.ReadProcessMemory.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    k.ReadProcessMemory.restype = wintypes.BOOL

    def read(address, length):
        if length < 1:
            return b''
        buffer = ctypes.create_string_buffer(length)
        actual = ctypes.c_size_t()
        ok = k.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer,
                                  length, ctypes.byref(actual))
        if not ok or actual.value != length:
            return None
        return buffer.raw[:actual.value]
    return read


def list_regions(pid, *, max_regions=16000):
    """Enumerate committed, reserved and free mapped address ranges."""
    k, handle = _game_handle(pid, 0x1010)
    try:
        k.VirtualQueryEx.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p,
            ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t)
        k.VirtualQueryEx.restype = ctypes.c_size_t
        address = 0x10000
        result = []
        for _ in range(max_regions):
            record = MEMORY_BASIC_INFORMATION()
            n = k.VirtualQueryEx(handle, ctypes.c_void_p(address),
                                 ctypes.byref(record), ctypes.sizeof(record))
            if not n:
                break
            base = record.BaseAddress or address
            size = int(record.RegionSize)
            if size == 0 or base + size <= address:
                break
            readable, writable = protection(int(record.Protect))
            if record.State == MEM_COMMIT:
                result.append({
                    'base': base,
                    'size': size,
                    'protect': int(record.Protect),
                    'readable': readable,
                    'writable': writable,
                    'kind': ('Image' if record.Type == 0x1000000 else
                             'Mapped' if record.Type == 0x40000 else 'Private'),
                })
            address = base + size
        return result
    finally:
        k.CloseHandle(handle)


def list_modules(pid):
    """Inspect modules using the 64-bit PSAPI module snapshot."""
    k, handle = _game_handle(pid, 0x1410)
    try:
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.EnumProcessModulesEx.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p),
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD)
        psapi.EnumProcessModulesEx.restype = wintypes.BOOL
        psapi.GetModuleInformation.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p,
            ctypes.POINTER(MODULEINFO), wintypes.DWORD)
        psapi.GetModuleInformation.restype = wintypes.BOOL
        psapi.GetModuleFileNameExW.argtypes = (
            wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD)
        psapi.GetModuleFileNameExW.restype = wintypes.DWORD
        capacity = 4096
        module_array = (ctypes.c_void_p * capacity)()
        used = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(
                handle, module_array, ctypes.sizeof(module_array),
                ctypes.byref(used), 3):
            raise ctypes.WinError(ctypes.get_last_error())
        if used.value > ctypes.sizeof(module_array):
            raise RuntimeError('Too many modules to display.')
        result = []
        for i in range(used.value // ctypes.sizeof(ctypes.c_void_p)):
            entry = MODULEINFO()
            module = module_array[i]
            if not psapi.GetModuleInformation(handle, module,
                    ctypes.byref(entry), ctypes.sizeof(entry)):
                continue
            path = ctypes.create_unicode_buffer(32768)
            psapi.GetModuleFileNameExW(handle, module, path, len(path))
            result.append({
                'base': entry.lpBaseOfDll or 0,
                'size': int(entry.SizeOfImage),
                'path': path.value,
                'name': path.value.replace('/', '\\').split('\\')[-1],
            })
        return sorted(result, key=lambda item: item['base'])
    finally:
        k.CloseHandle(handle)


def match_bytes(buffer, needle, base, *, max_count=MAX_MATCHES):
    """Pure byte matcher used by scans; returns all overlapping occurrences."""
    found = []
    index = 0
    while len(found) < max_count:
        at = buffer.find(needle, index)
        if at < 0:
            break
        found.append((base + at, bytes(needle)))
        index = at + 1
    return found


def filter_matches(matches, read, kind, mode, expected=None, cancel=None):
    """Pure refinement engine. Re-sampled bytes become the next baseline."""
    out = []
    fmt = TYPES.get(kind)
    for address, previous in matches:
        if cancel is not None and cancel.is_set():
            break
        current = read(address, len(previous))
        if current is None or len(current) != len(previous):
            continue
        if mode == 'Exact':
            include = current == expected
        elif mode == 'Changed':
            include = current != previous
        elif mode == 'Unchanged':
            include = current == previous
        elif mode in ('Increased', 'Decreased'):
            if fmt is None:
                raise ValueError('Choose a numeric value type for numeric comparisons.')
            before = struct.unpack(fmt, previous)[0]
            after = struct.unpack(fmt, current)[0]
            include = after > before if mode == 'Increased' else after < before
        else:
            raise ValueError('Invalid refinement mode.')
        if include:
            out.append((address, current))
    return out


def scan_process(pid, pattern, *, writable_only=True, cancel=None,
                 progress=None, max_bytes=MAX_SCAN_BYTES):
    """Chunked full scan; bounds matches/memory and supports cancellation."""
    if not isinstance(pattern, bytes) or not pattern:
        raise ValueError('Scan pattern is empty.')
    if not 0 < max_bytes <= MAX_SCAN_BYTES:
        raise ValueError('Invalid scan limit.')
    cancel = cancel or threading.Event()
    regions = [r for r in list_regions(pid)
               if r['readable'] and (r['writable'] or not writable_only)]
    k, handle = _game_handle(pid, 0x1010)
    matches = []
    scanned = 0
    reason = ''
    try:
        read = _reader(k, handle)
        for region in regions:
            if cancel.is_set():
                reason = 'Cancelled'
                break
            base = region['base']
            offset = 0
            tail = b''
            while offset < region['size']:
                if cancel.is_set():
                    reason = 'Cancelled'
                    break
                if scanned >= max_bytes:
                    reason = 'Scan limit reached'
                    break
                length = min(CHUNK, region['size'] - offset, max_bytes - scanned)
                block = read(base + offset, length)
                if block is not None:
                    combined = tail + block
                    origin = base + offset - len(tail)
                    for address, raw in match_bytes(
                            combined, pattern, origin,
                            max_count=MAX_MATCHES - len(matches)):
                        matches.append((address, raw))
                    tail = combined[-(len(pattern)-1):] if len(pattern) > 1 else b''
                else:
                    tail = b''  # never join bytes over an unreadable gap
                scanned += length
                offset += length
                if progress and (scanned % (CHUNK * 32) < CHUNK):
                    progress(scanned)
                if len(matches) >= MAX_MATCHES:
                    reason = 'Result limit reached'
                    break
            if reason:
                break
        return {'matches': matches, 'scanned': scanned, 'reason': reason}
    finally:
        k.CloseHandle(handle)


def refine_process(pid, matches, kind, mode, expected=None, cancel=None):
    if mode == 'Exact' and (not isinstance(expected, bytes) or not expected):
        raise ValueError('Provide an exact value for refinement.')
    if mode in ('Increased', 'Decreased') and TYPES.get(kind) is None:
        raise ValueError('Increased/decreased requires a numeric type.')
    k, handle = _game_handle(pid, 0x1010)
    try:
        found = filter_matches(matches, _reader(k, handle), kind, mode,
                               expected=expected, cancel=cancel)
        return {'matches': found,
                'scanned': len(matches),
                'reason': 'Cancelled' if cancel and cancel.is_set() else ''}
    finally:
        k.CloseHandle(handle)
