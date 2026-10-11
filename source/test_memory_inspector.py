"""Safe scan engine tests using synthetic memory only."""
import ctypes
import os
import struct
import threading
import unittest
from unittest.mock import MagicMock, patch

import developer_memory_inspector as m


class InspectorTests(unittest.TestCase):
    def test_types_and_value_validation(self):
        self.assertEqual(m.encode_value('100', '4 Bytes (signed)'), struct.pack('<i', 100))
        self.assertEqual(m.encode_value('3.5', 'Float'), struct.pack('<f', 3.5))
        self.assertEqual(m.encode_value('-1', '8 Bytes (signed)'), struct.pack('<q', -1))
        self.assertEqual(m.encode_value('DE AD BE EF', 'Hex Bytes'), bytes.fromhex('DEADBEEF'))
        self.assertEqual(m.encode_value('Goliath', 'Text (UTF-8)'), b'Goliath')
        self.assertEqual(m.decode_value(struct.pack('<i', 10), '4 Bytes (signed)'), '10')
        for text, kind in [('abc', 'Float'), ('nan', 'Double'),
                           ('4294967296', '4 Bytes (unsigned)'),
                           ('-1', '4 Bytes (unsigned)'), ('', 'Text (UTF-8)')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.encode_value(text, kind)

    def test_protection_and_hex(self):
        self.assertEqual(m.protection(0x04), (True, True))
        self.assertEqual(m.protection(0x20), (True, False))
        self.assertEqual(m.protection(0x100 | 0x04), (False, False))
        self.assertEqual(m.protection(0x01), (False, False))
        dump = m.format_hex(0x1000, b'AB\x00\xff')
        self.assertIn('0x0000000000001000', dump)
        self.assertIn('41 42 00 FF', dump)
        self.assertIn('AB..', dump)

    def test_overlap_and_refinement(self):
        matches = m.match_bytes(b'AXAAA', b'AA', 0x1000)
        self.assertEqual([x[0] for x in matches], [0x1002, 0x1003])
        previous = [(0x2000, struct.pack('<i', 100)),
                    (0x3000, struct.pack('<i', 100))]
        after = {0x2000: struct.pack('<i', 120), 0x3000: struct.pack('<i', 100)}
        read = lambda addr, n: after.get(addr)
        self.assertEqual([x[0] for x in m.filter_matches(previous, read, '4 Bytes (signed)', 'Increased')], [0x2000])
        self.assertEqual([x[0] for x in m.filter_matches(previous, read, '4 Bytes (signed)', 'Changed')], [0x2000])
        self.assertEqual([x[0] for x in m.filter_matches(previous, read, '4 Bytes (signed)', 'Unchanged')], [0x3000])
        self.assertEqual([x[0] for x in m.filter_matches(previous, read, '4 Bytes (signed)', 'Exact', expected=struct.pack('<i', 100))], [0x3000])
        with self.assertRaises(ValueError):
            m.filter_matches(previous, read, '4 Bytes (signed)', 'Unsupported')
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual(m.filter_matches(previous, read, '4 Bytes (signed)', 'Changed', cancel=cancelled), [])

    def test_chunk_boundary_pattern(self):
        data = b'XXABCYYABC'
        region = {'base': 0x10000, 'size': len(data), 'readable': True, 'writable': True}
        def factory(_k, _handle):
            def read(addr, size):
                offset = addr - 0x10000
                if 0 <= offset and offset + size <= len(data):
                    return data[offset:offset+size]
                return None
            return read
        k = MagicMock()
        with patch.object(m, 'CHUNK', 4), \
             patch.object(m, 'list_regions', return_value=[region]), \
             patch.object(m, '_game_handle', return_value=(k, 1)), \
             patch.object(m, '_reader', factory):
            result = m.scan_process(33, b'ABC', max_bytes=64)
        self.assertEqual([a for a, _ in result['matches']], [0x10002, 0x10007])
        self.assertEqual(result['reason'], '')
        k.CloseHandle.assert_called_once()

    def test_cancel_reports_status(self):
        cancelled = threading.Event()
        cancelled.set()
        region = {'base': 0x10000, 'size': 64, 'readable': True, 'writable': True}
        with patch.object(m, 'list_regions', return_value=[region]), \
             patch.object(m, '_game_handle', return_value=(MagicMock(), 1)):
            result = m.scan_process(33, b'X', cancel=cancelled, max_bytes=32)
        self.assertEqual(result['reason'], 'Cancelled')

    def test_win64_abi_sizes(self):
        if os.name == 'nt' and ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(m.MEMORY_BASIC_INFORMATION), 48)
            self.assertEqual(ctypes.sizeof(m.MODULEINFO), 24)


if __name__ == '__main__':
    unittest.main()
