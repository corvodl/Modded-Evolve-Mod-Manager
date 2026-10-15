"""Regression for real counted progress and safe cancellation in file operations."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from operation_progress import ProgressStream, parse_progress_line, progress_line
from asset_library_preview import export_archive_layout


class ProgressStreamTests(unittest.TestCase):
    def test_split_events_and_unrelated_logs(self):
        stream = ProgressStream()
        events, remaining = stream.feed('Extracting now\nPROGRESS|models.')
        self.assertEqual(events, [])
        self.assertEqual(remaining, 'Extracting now\n')
        events, remaining = stream.feed('pak|23|100\nnot a progress record\n')
        self.assertEqual(events, [('models.pak', 23, 100)])
        self.assertEqual(remaining, 'not a progress record\n')

    def test_invalid_progress_not_interpreted_as_complete(self):
        self.assertEqual(progress_line('A|B', 99, 10), 'PROGRESS|A_B|10|10')
        self.assertIsNone(parse_progress_line('PROGRESS|archive|500|100'))
        self.assertIsNone(parse_progress_line('PROGRESS|bad|nan|100'))
        self.assertIsNone(parse_progress_line('PROGRESS|bad|1|-1'))


class LibraryExportProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / 'AssetLibrary'
        self.identifier = 'a' * 20
        self.archive = self.library / self.identifier
        directory = self.archive / 'files'
        directory.mkdir(parents=True)
        self.entries = {}
        for i in range(53):
            path = 'characters/hunters/merc_tech/textures/tech_%03d.dds' % i
            data = bytes([i]) * (i + 2)
            file = directory / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            self.entries[path] = data
        (self.archive / 'asset-library.json').write_text(json.dumps({
            'version': 1,'name':'test.pak',
            'entries':[{'path':path,'bytes':len(data)}
                       for path,data in self.entries.items()]
        }), encoding='utf-8')
        self.dest = self.root / 'exports'
        self.dest.mkdir()

    def test_exports_report_real_file_counts(self):
        events = []
        output, count = export_archive_layout(
            self.identifier, self.dest, root=self.library,
            progress=lambda name, done, total: events.append((name, done, total)))
        self.assertEqual(count, 53)
        self.assertEqual(events[0][1:], (0, 53))
        self.assertEqual(events[-1][1:], (53, 53))
        self.assertTrue(all(0 <= n <= total == 53 for _, n, total in events))
        for relative, data in self.entries.items():
            self.assertEqual((output / relative).read_bytes(), data)

    def test_cancel_removes_partial_staging_and_preserves_library(self):
        seen = []
        def callback(name, done, total):
            seen.append(done)
        def cancelled():
            return 20 in seen
        with self.assertRaisesRegex(InterruptedError, 'cancelled'):
            export_archive_layout(
                self.identifier, self.dest, root=self.library,
                progress=callback, cancelled=cancelled)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(len(list((self.archive/'files').rglob('*.dds'))), 53)


if __name__ == '__main__':
    unittest.main()
