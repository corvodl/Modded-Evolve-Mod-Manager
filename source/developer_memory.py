"""Live Evolve.exe process memory access. No on-disk game DLL modifications."""
import csv
import ctypes
from ctypes import wintypes
import io
import os
import subprocess


def _require_windows():
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError("Requires 64-bit Windows.")


def parse_address(value):
    try:
        address = int(str(value).strip(), 0)
    except (ValueError, TypeError) as exc:
        raise ValueError("Enter a hex address such as 0x7FF600001000.") from exc
    if not 0 < address < (1 << 64):
        raise ValueError("Address must be a positive 64-bit pointer.")
    return address


def parse_hex(value):
    try:
        data = bytes.fromhex(str(value).strip())
    except ValueError as exc:
        raise ValueError("Enter complete hex bytes, e.g. 00 00 80 3F.") from exc
    if not 0 < len(data) <= 256:
        raise ValueError("Write between 1 and 256 bytes.")
    return data


def list_evolve_processes():
    _require_windows()
    process = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Evolve.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=10)
    if process.returncode:
        raise RuntimeError("Unable to inspect running processes.")
    ids = []
    for row in csv.reader(io.StringIO(process.stdout)):
        if len(row) >= 2 and row[0].casefold() == "evolve.exe":
            try:
                pid = int(row[1].replace(",", ""))
            except ValueError:
                continue
            if pid > 0:
                ids.append(pid)
    return sorted(set(ids))


def _game_handle(pid, access):
    _require_windows()
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("Choose a valid game PID.")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    handle = k.OpenProcess(access, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        if not k.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        if name.value.replace("/", "\\").split("\\")[-1].casefold() != "evolve.exe":
            raise PermissionError("Selected process is not Evolve.exe.")
        return k, handle
    except BaseException:
        k.CloseHandle(handle)
        raise


def read_memory(pid, address, size):
    address = parse_address(address)
    if not isinstance(size, int) or not 0 < size <= 4096:
        raise ValueError("Read size must be 1 through 4096 bytes.")
    k, handle = _game_handle(pid, 0x1010)
    try:
        k.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.ReadProcessMemory.restype = wintypes.BOOL
        buffer = (ctypes.c_ubyte * size)()
        actual = ctypes.c_size_t()
        if not k.ReadProcessMemory(handle, ctypes.c_void_p(address),
                                   buffer, size, ctypes.byref(actual)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buffer[:actual.value])
    finally:
        k.CloseHandle(handle)


def write_memory(pid, address, payload):
    address = parse_address(address)
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 256:
        raise ValueError("Write requires 1 through 256 bytes.")
    k, handle = _game_handle(pid, 0x1038)
    try:
        k.WriteProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.WriteProcessMemory.restype = wintypes.BOOL
        data = ctypes.create_string_buffer(payload)
        actual = ctypes.c_size_t()
        if not k.WriteProcessMemory(handle, ctypes.c_void_p(address),
                                    data, len(payload), ctypes.byref(actual)):
            raise ctypes.WinError(ctypes.get_last_error())
        if actual.value != len(payload):
            raise OSError("Partial memory write.")
        return actual.value
    finally:
        k.CloseHandle(handle)
