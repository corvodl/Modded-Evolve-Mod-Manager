"""Bounded signed CryPAK entry streaming with original CTR, length and CRC rules.

The caller must verify the archive RSA signature and validate archive paths
before calling. The destination must be an unpublished temporary output; a
failed or canceled entry must never be added to an import manifest.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
from pathlib import Path
import zlib
import time
from performance_trace import record, enabled

from evolve_gameplay_editor import key_index
from evolve_video_pak_tool_fixed import Twofish, file_iv, local_data_start


CHUNK_SIZE = 128 * 1024  # CTR-safe full-block boundaries, except final chunk.
_MASK128 = (1 << 128) - 1


class EntryStreamCanceled(InterruptedError):
    pass


def _xor_bulk(ciphertext, keystream):
    """Use native vectorized XOR when NumPy is installed; exact fallback."""
    try:
        import numpy as np
    except ImportError:
        return bytes(x ^ y for x, y in zip(ciphertext, keystream))
    return np.bitwise_xor(np.frombuffer(ciphertext, dtype=np.uint8),
                          np.frombuffer(keystream, dtype=np.uint8,
                                        count=len(ciphertext))).tobytes()


def stream_signed_entry(pak, info, entryname, destination, *, cancelled=None,
                        reader=None, cipher_sessions=None, chunk_size=CHUNK_SIZE):
    """Read, verify and write one entry with bounded encryption/DEFLATE buffers.

    Reuse the already-verified directory and optionally a reader/key schedule.
    Preserves little-endian 128-bit counters and the exact method 13/14 CRC.
    Returns size, SHA-256 and eight original bytes for CryXmlB detection.
    """
    if chunk_size <= 0 or chunk_size % 16:
        raise ValueError('Encrypted chunk size must be a positive multiple of 16')
    meta = info['entries'][entryname]
    method = int(meta['method'])
    expected = int(meta['original_size'])
    compressed = int(meta['compressed_size'])
    if method not in (13, 14) or int(meta['extra_length']) != 0:
        raise ValueError('Unsupported encrypted CryPAK entry method/extra bytes')
    if expected < 0 or compressed < 0:
        raise ValueError('Negative signed entry length')
    if method == 13 and expected != compressed:
        raise ValueError('Unexpected stored/original size mismatch in method 13')
    slot = key_index(meta)
    owns_cipher = cipher_sessions is None
    sessions = {} if owns_cipher else cipher_sessions
    cipher = sessions.get(slot)
    if cipher is None:
        cipher = Twofish(info['keys'][slot])
        sessions[slot] = cipher
    inflater = zlib.decompressobj(-15) if method == 14 else None
    digest = hashlib.sha256()
    crc = 0
    total = 0
    prefix = bytearray()
    tracing = enabled()
    totals = {'read': 0., 'ctr': 0., 'inflate': 0., 'output': 0.}
    input_bytes = 0
    chunks = 0

    def check_cancel():
        if cancelled is not None and cancelled():
            raise EntryStreamCanceled('Signed PAK entry import canceled.')

    def write_chunk(handle, data):
        nonlocal crc, total
        if not data:
            return
        total += len(data)
        if total > expected:
            raise ValueError('Decompressed size exceeds signed directory')
        if len(prefix) < 8:
            prefix.extend(data[:8-len(prefix)])
        started = time.perf_counter() if tracing else 0.
        handle.write(data)
        digest.update(data)
        crc = zlib.crc32(data, crc)
        if tracing:
            totals['output'] += (time.perf_counter() - started) * 1000

    def inflate_chunk(handle, data):
        if inflater is None:
            write_chunk(handle, data)
            return
        pending = data
        # max_length bounds each output, including highly compressible input.
        while True:
            check_cancel()
            started = time.perf_counter() if tracing else 0.
            original_pending = len(pending)
            block = inflater.decompress(pending, chunk_size)
            remaining_pending = inflater.unconsumed_tail
            if tracing:
                totals['inflate'] += (time.perf_counter() - started) * 1000
            write_chunk(handle, block)
            pending = remaining_pending
            if pending and not block and len(pending) >= original_pending:
                raise ValueError('Encrypted DEFLATE decoder made no progress')
            if pending or len(block) == chunk_size:
                continue
            break

    owns_reader = reader is None
    try:
        context = open(pak, 'rb') if owns_reader else nullcontext(reader)
        with context as source, Path(destination).open('xb') as output:
            start = local_data_start(source, meta, info['cdr_at'])
            source.seek(start)
            remaining = compressed
            iv = int.from_bytes(file_iv(meta), 'little')
            block_offset = 0
            while remaining:
                check_cancel()
                requested = min(chunk_size, remaining)
                parts = []
                received = 0
                read_started = time.perf_counter() if tracing else 0.
                while received < requested:
                    check_cancel()
                    part = source.read(requested - received)
                    if not part:
                        raise EOFError('Incomplete encrypted entry data')
                    parts.append(part)
                    received += len(part)
                ciphertext = b''.join(parts)
                if tracing:
                    totals['read'] += (time.perf_counter() - read_started) * 1000
                input_bytes += len(ciphertext)
                chunks += 1
                remaining -= requested
                ctr_started = time.perf_counter() if tracing else 0.
                blocks = (len(ciphertext) + 15) // 16
                counters = b''.join(
                    ((iv + block_offset + i) & _MASK128).to_bytes(16, 'little')
                    for i in range(blocks))
                clear = _xor_bulk(ciphertext, cipher.encrypt_blocks(counters))
                if tracing:
                    totals['ctr'] += (time.perf_counter() - ctr_started) * 1000
                inflate_chunk(output, clear)
                block_offset += blocks
            if inflater is not None:
                # Drain capped output without calling flush(), which can
                # return an unbounded allocation for a DEFLATE bomb.
                while not inflater.eof:
                    check_cancel()
                    tail = inflater.decompress(b'', chunk_size)
                    if not tail:
                        raise ValueError('Incomplete encrypted DEFLATE stream')
                    write_chunk(output, tail)
            check_cancel()
        if total != expected:
            raise ValueError('Decompressed size does not match signed directory')
        if (crc & 0xffffffff) != int(meta['crc']):
            raise ValueError('Entry decryption CRC32 FAILED; refusing import')
        if tracing:
            for stage, milliseconds in totals.items():
                record('pak', 'entry_' + stage,
                       elapsed_ms=milliseconds, input_bytes=input_bytes,
                       output_bytes=total, chunks=chunks, method=method)
        return {'bytes': total, 'sha256': digest.hexdigest(), 'head': bytes(prefix)}
    finally:
        if owns_cipher:
            for item in sessions.values():
                item.close()
