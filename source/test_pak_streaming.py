"""Signed method 13/14 streaming matches reference CTR byte for byte."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

import pak_streaming
from evolve_video_pak_tool_fixed import twofish_ctr


class FakeTwofish:
    """Deterministic reversible block ECB substitute for CTR state testing."""
    def __init__(self, key):
        self.key = key
    def encrypt_blocks(self, blocks):
        assert len(blocks) % 16 == 0
        return bytes(byte ^ 0xA5 for byte in blocks)
    def close(self):
        pass


class SignedPakStreamTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.key = b'k' * 16
        self.iv = (2**128 - 3).to_bytes(16, 'little')

    def _run_entry(self, plaintext, method, *, bad_crc=False, cancelled=None):
        if method == 14:
            compressor = zlib.compressobj(wbits=-15)
            compressed = compressor.compress(plaintext) + compressor.flush()
        else:
            compressed = plaintext
        with patch('evolve_video_pak_tool_fixed.Twofish', FakeTwofish):
            ciphertext = twofish_ctr(compressed, self.key, self.iv)
        source = self.root / 'fixture.pak'
        source.write_bytes(ciphertext)
        destination = self.root / 'entry.bin'
        if destination.exists():
            destination.unlink()
        expected_crc = (zlib.crc32(plaintext) + int(bad_crc)) & 0xffffffff
        meta = dict(method=method, compressed_size=len(ciphertext),
                    original_size=len(plaintext), extra_length=0,
                    crc=expected_crc)
        info = dict(entries={'fixture.bin': meta}, keys=[self.key]*16, cdr_at=0)
        with patch('pak_streaming.local_data_start', return_value=0), \
             patch('pak_streaming.file_iv', return_value=self.iv), \
             patch('pak_streaming.Twofish', FakeTwofish):
            result = pak_streaming.stream_signed_entry(
                source, info, 'fixture.bin', destination,
                cancelled=cancelled, chunk_size=32)
        return result, destination

    def test_method_13_random_sizes_and_counter_wrap(self):
        for length in (0, 1, 15, 16, 17, 32, 33, 127, 257, 513):
            payload = bytes((i * 29 + 7) % 256 for i in range(length))
            result, output = self._run_entry(payload, 13)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(result['bytes'], len(payload))
            self.assertEqual(result['sha256'], sha256(payload).hexdigest())
            self.assertEqual(result['head'], payload[:8])

    def test_method_14_deflate_stream_preserves_crc(self):
        payload = bytes((i * 13 + 7) % 256 for i in range(5000)) + b'A' * 50000
        result, output = self._run_entry(payload, 14)
        self.assertEqual(output.read_bytes(), payload)
        self.assertEqual(result['bytes'], len(payload))

    def test_crc_mismatch_refuses_archive_entry(self):
        with self.assertRaisesRegex(ValueError, 'CRC32'):
            self._run_entry(b'verified' * 100, 13, bad_crc=True)

    def test_cancellation_during_large_entry(self):
        calls = [0]
        def cancelled():
            calls[0] += 1
            return calls[0] > 6
        with self.assertRaises(pak_streaming.EntryStreamCanceled):
            self._run_entry(b'M' * 5000, 13, cancelled=cancelled)
        self.assertGreater(calls[0], 6)

    def test_requires_full_ctr_block_chunks(self):
        with self.assertRaisesRegex(ValueError, 'multiple of 16'):
            pak_streaming.stream_signed_entry(
                '', {'entries': {}}, '', 'unused', chunk_size=31)


if __name__ == '__main__':
    unittest.main()
