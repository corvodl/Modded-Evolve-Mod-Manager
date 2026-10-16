import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from qt_media_preview import MediaPreview, is_media
import test_qt_asset_preview as library_tests
from PySide6.QtTest import QTest


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_classification(self):
        for name in ('sound.WAV', 'sound.ogg', 'movie.mp4', 'bank.fsb', 'bank.bnk', 'clip.wem'):
            self.assertTrue(is_media(name))
        self.assertFalse(is_media('file.xml'))

    def test_local_playback_lifecycle_and_bank_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'silence.wav'
            with wave.open(str(path), 'wb') as out:
                out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\0\0' * 8000)
            view = MediaPreview()
            try:
                view.open_file(path)
                self.assertIsNotNone(view.player)
                self.assertTrue(view.play.isEnabled())
                self.assertTrue(view.player.source().isLocalFile())
                self.assertEqual(view.player.playbackState(), view.player.PlaybackState.StoppedState)
                for _ in range(40):
                    if view.player.duration() > 0:
                        break
                    QTest.qWait(50)
                self.assertGreater(view.player.duration(), 0, view.message.text())
                view._toggle()
                self.assertEqual(view.player.playbackState(), view.player.PlaybackState.PlayingState)
                view.open_file(Path(temp) / 'audio.fsb')
                self.assertTrue(view.player.source().isEmpty())
                self.assertFalse(view.play.isEnabled())
                self.assertIn('decoder', view.message.text())
            finally:
                view.clear()
                view.close()


class MediaLibraryTests(unittest.TestCase):
    setUpClass = classmethod(library_tests.ReadOnlyAssetPreviewUITests.setUpClass.__func__)
    setUp = library_tests.ReadOnlyAssetPreviewUITests.setUp

    def test_bank_preview_routes_without_loading_entire_binary(self):
        rel = 'sounds/test.fsb'
        target = self.folder / 'files' / rel
        target.parent.mkdir()
        target.write_bytes(b'FSB5fixture')
        import json
        manifest = self.folder / 'asset-library.json'
        data = json.loads(manifest.read_text())
        data['entries'].append({'path': rel, 'bytes': target.stat().st_size})
        manifest.write_text(json.dumps(data))
        self.window.refresh()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Media must stream from disk')):
            self.window.load(rel)
        self.assertEqual(self.window.mode, 'media')
        self.assertFalse(self.window.media_view.play.isEnabled())
        self.assertIn('decoder', self.window.media_view.message.text())
        self.assertEqual(target.read_bytes(), b'FSB5fixture')
