"""Opt-in performance diagnostics contain only stage names and numeric metrics."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from performance_trace import record, span, start_qt_heartbeat


class PerformanceTraceTests(unittest.TestCase):
    def test_disabled_tracing_never_writes(self):
        with TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'EVOLVE_PERF_TRACE': '0'}), \
             patch('performance_trace._trace_path', return_value=Path(folder) / 'trace.jsonl'):
            with span('model', 'decode', input_bytes=10):
                pass
            self.assertFalse((Path(folder) / 'trace.jsonl').exists())
            self.assertIsNone(start_qt_heartbeat(object()))

    def test_spans_are_monotonic_and_strip_untrusted_paths(self):
        with TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'EVOLVE_PERF_TRACE': '1'}), \
             patch('performance_trace._trace_path', return_value=Path(folder) / 'trace.jsonl'):
            with span('pak', 'entry_decrypt', input_bytes=64, leaked_path='C:/Private/secret.pak'):
                pass
            record('pak', 'published', files=3, source='/private/home/user')
            lines = (Path(folder) / 'trace.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), 2)
        entries = [json.loads(line) for line in lines]
        self.assertEqual(entries[0]['input_bytes'], 64)
        self.assertGreaterEqual(entries[0]['elapsed_ms'], 0)
        self.assertIn('t_monotonic_ns', entries[0])
        self.assertEqual(entries[1]['files'], 3)
        self.assertNotIn('leaked_path', entries[0])
        self.assertNotIn('source', entries[1])
        self.assertNotIn('C:/Private', ''.join(lines))

    def test_exception_is_recorded_without_suppression(self):
        with TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'EVOLVE_PERF_TRACE': '1'}), \
             patch('performance_trace._trace_path', return_value=Path(folder) / 'trace.jsonl'):
            with self.assertRaisesRegex(ValueError, 'source rejected'):
                with span('pak', 'verify'):
                    raise ValueError('source rejected')
            entry = json.loads((Path(folder) / 'trace.jsonl').read_text().strip())
        self.assertEqual(entry['outcome'], 'error')
        self.assertNotIn('source rejected', str(entry))


if __name__ == '__main__':
    unittest.main()
