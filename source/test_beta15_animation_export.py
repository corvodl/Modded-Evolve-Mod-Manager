"""Beta 15 regressions: bounded animation picker, converter diagnostics,
and asynchronous long model exports that leave the UI responsive.
"""
from __future__ import annotations
import os
import time
from pathlib import Path
from threading import Event
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEventLoop, QTimer, Qt, QProcess
from PySide6.QtWidgets import QApplication, QListWidget, QPlainTextEdit

from test_material_preview import uv_mesh
from model_preview import read_preview_mesh
from qt_gpu_viewer import QtGPUModelPanel
from qt_background_exports import begin_file_operation, exports_busy
from character_animation_catalog import AnimationReference


class CompactAnimationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        mesh = read_preview_mesh(uv_mesh(), 'synthetic.skinm')
        self.panel = QtGPUModelPanel(None, mesh, compact=True)
        self.addCleanup(self.panel.close)

    def test_many_animations_remain_inside_six_row_scrollable_widget(self):
        p = self.panel
        entries = [
            AnimationReference(
                'idle_%04d' % i, 'characters/hunters/merc_val/idle_%04d.caf' % i,
                'caf', False)
            for i in range(385)]
        p.set_native_catalog(entries)
        p.resize(465, 385)
        p.show()
        self.app.processEvents()
        p.toggle_overlay('Animation')
        self.app.processEvents()
        self.assertIsInstance(p.native_clip_select, QListWidget)
        self.assertLessEqual(p.native_clip_select.height(), 152)
        self.assertLessEqual(p.native_clip_select.count(), 91)
        self.assertGreater(p.native_clip_select.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(p._overlay.height(), p.canvas.height())
        self.assertLessEqual(p._overlay.width(), p.canvas.width())
        p.native_search.setText('idle_0384')
        self.app.processEvents()
        self.assertEqual(p.native_clip_select.count(), 2)
        self.assertEqual(p.native_clip_select.itemData(1), 384)

    def test_long_paths_wrap_in_bounded_scrollable_details(self):
        p = self.panel
        item = AnimationReference(
            'combat_idle', 'characters/hunters/merc_val/' +
            'very_long_subdirectory/' * 12 + 'combat_idle.caf', 'caf', False)
        p.set_native_catalog([item])
        p.toggle_overlay('Animation')
        p.native_clip_select.setCurrentIndex(1)
        self.app.processEvents()
        self.assertIsInstance(p.native_info, QPlainTextEdit)
        self.assertLessEqual(p.native_info.height(), 90)
        self.assertEqual(
            p.native_info.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertIn(item.virtual_path, p.native_info.toPlainText())
        self.assertTrue(p.conversion_status.isVisible())


class AsyncProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_keeps_qt_event_loop_responsive(self):
        mesh = read_preview_mesh(uv_mesh(), 'synthetic.skinm')
        panel = QtGPUModelPanel(None, mesh, compact=True)
        self.addCleanup(panel.close)
        done = []
        loop = QEventLoop()

        def work(progress, cancelled):
            progress('Converting 1 of 2 models', 0, 2)
            time.sleep(.12)  # Simulate a CPU-heavy model decode.
            progress('Converting 2 of 2 models', 1, 2)
            time.sleep(.12)
            return 2

        def completed(result):
            done.append(result)
            loop.quit()

        thread = begin_file_operation(
            panel, 'Export models', 'Preparing model batch', work,
            on_success=completed,
            on_error=lambda reason: (done.append('ERROR ' + reason), loop.quit()))
        self.assertTrue(exports_busy(panel))
        ticks = []
        ticker = QTimer()
        ticker.setInterval(25)
        ticker.timeout.connect(lambda: ticks.append(1))
        ticker.start()
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        watchdog.start(2500)
        loop.exec()
        ticker.stop()
        watchdog.stop()
        self.assertEqual(done, [2])
        self.assertGreaterEqual(len(ticks), 3)
        # Process thread.finished cleanup queued after completed.
        self.app.processEvents()
        self.assertTrue(thread.isFinished() or not thread.isRunning())

    def test_cancellation_is_seen_by_worker_between_models(self):
        mesh = read_preview_mesh(uv_mesh(), 'synthetic.skinm')
        panel = QtGPUModelPanel(None, mesh, compact=True)
        self.addCleanup(panel.close)
        errors = []
        loop = QEventLoop()
        started = Event()

        def work(progress, cancelled):
            progress('Converting model', 0, 2)
            started.set()
            time.sleep(.15)
            if cancelled():
                raise InterruptedError('Cancelled between model exports')
            return 2

        task = begin_file_operation(
            panel, 'Export models', 'Working', work,
            on_success=lambda result: (errors.append('unexpected success'), loop.quit()),
            on_error=lambda reason: (errors.append(reason), loop.quit()))
        QTimer.singleShot(25, task.cancel)
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        watchdog.start(2500)
        loop.exec()
        watchdog.stop()
        self.assertTrue(started.is_set())
        self.assertEqual(errors, ['Cancelled between model exports'])


if __name__ == '__main__':
    unittest.main()
