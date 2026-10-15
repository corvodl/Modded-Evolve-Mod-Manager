"""Native bulk Twofish ECB used only as a CTR keystream primitive.

GNU libgcrypt's Twofish provider processes complete 16-byte counter blocks in
one C call. We preserve little-endian counter construction in the caller.
The native library is optional; the original twofish Python binding remains a
fully supported reference fallback when no approved binary is packaged.

On Windows, load ONLY application-adjacent bundled DLLs, never a DLL from the
working directory or ambient PATH. Release packaging must include the license,
the exact DLLs and clean-machine runtime tests before declaring this backend
shipped.
"""
from __future__ import annotations

import ctypes
from ctypes.util import find_library
import os
from pathlib import Path
import sys


def candidate_libraries():
    if os.name == 'nt':
        roots = []
        bundled = getattr(sys, '_MEIPASS', None)
        if bundled:
            roots.append(Path(bundled))
        roots.append(Path(sys.executable).resolve().parent)
        roots.append(Path(__file__).resolve().parent / 'native')
        for root in roots:
            for name in ('libgcrypt-20.dll', 'libgcrypt.dll'):
                target = root / name
                if target.is_file():
                    yield str(target.resolve())
    else:
        found = find_library('gcrypt')
        if found:
            yield found


class GcryptBulkTwofish:
    """Worker-local ECB session with one native call per counter block batch."""

    def __init__(self, key, *, library=None):
        if len(key) != 16:
            raise ValueError('CryPAK Twofish key requires 16 bytes')
        if library is None:
            failures = []
            for candidate in candidate_libraries():
                try:
                    # Python 3.8+ excludes arbitrary PATH directories from
                    # dependency lookup. Keep the approved DLL directory
                    # scoped to this exact load so libgpg-error resolves on
                    # clean Windows installations without PATH modification.
                    if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
                        with os.add_dll_directory(str(Path(candidate).parent)):
                            library = ctypes.CDLL(candidate)
                    else:
                        library = ctypes.CDLL(candidate)
                    break
                except OSError as exc:
                    failures.append(str(exc))
            if library is None:
                raise OSError('No approved native libgcrypt bulk backend available')
        self.lib = library
        self.handle = ctypes.c_void_p()
        self._closed = False
        lib = self.lib
        lib.gcry_check_version.argtypes = [ctypes.c_char_p]
        lib.gcry_check_version.restype = ctypes.c_char_p
        if not lib.gcry_check_version(None):
            raise RuntimeError('libgcrypt version check failed')
        lib.gcry_cipher_map_name.argtypes = [ctypes.c_char_p]
        lib.gcry_cipher_map_name.restype = ctypes.c_int
        lib.gcry_cipher_open.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_int,
            ctypes.c_int, ctypes.c_uint]
        lib.gcry_cipher_open.restype = ctypes.c_int
        lib.gcry_cipher_setkey.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        lib.gcry_cipher_setkey.restype = ctypes.c_int
        lib.gcry_cipher_encrypt.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t]
        lib.gcry_cipher_encrypt.restype = ctypes.c_int
        lib.gcry_cipher_close.argtypes = [ctypes.c_void_p]
        lib.gcry_cipher_close.restype = None
        algorithm = lib.gcry_cipher_map_name(b'TWOFISH')
        if not algorithm:
            raise RuntimeError('Native libgcrypt has no Twofish provider')
        if lib.gcry_cipher_open(ctypes.byref(self.handle), algorithm, 1, 0):
            raise RuntimeError('Unable to create native Twofish ECB session')
        try:
            raw_key = ctypes.create_string_buffer(key, len(key))
            if lib.gcry_cipher_setkey(
                    self.handle, ctypes.cast(raw_key, ctypes.c_void_p),
                    len(key)):
                raise RuntimeError('Native libgcrypt rejected Twofish key')
        except BaseException:
            self.close()
            raise

    def encrypt_blocks(self, blocks):
        if self._closed:
            raise RuntimeError('Twofish session already closed')
        if len(blocks) % 16:
            raise ValueError('Native Twofish input must be complete 16-byte blocks')
        if not blocks:
            return b''
        source = ctypes.create_string_buffer(blocks, len(blocks))
        result = ctypes.create_string_buffer(len(blocks))
        if self.lib.gcry_cipher_encrypt(
                self.handle,
                ctypes.cast(result, ctypes.c_void_p), len(blocks),
                ctypes.cast(source, ctypes.c_void_p), len(blocks)):
            raise RuntimeError('Native Twofish encryption rejected the batch')
        return result.raw

    def close(self):
        if not self._closed:
            self._closed = True
            if self.handle:
                self.lib.gcry_cipher_close(self.handle)
                self.handle = ctypes.c_void_p()
