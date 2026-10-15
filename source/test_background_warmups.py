"""Optional PAK warmups must not traverse a game on Qt's event thread."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from qt_manager import QtManager


class BackgroundWarmupTests(unittest.TestCase):
    def test_large_default_scan_is_submitted_to_a_worker(self):
        started = threading.Event()
        release = threading.Event()
        done = threading.Event()
        callers = []
        def inventory(_game):
            callers.append(threading.current_thread())
            started.set()
            release.wait(3)
            return ()
        manager = SimpleNamespace(
            game_root='/fixture', _find_game_root=lambda: '/fixture',
            _auto_library_scanned_game=None,
            _default_scan_running=False, _default_scan_key=None,
            asset_library_controller=SimpleNamespace(busy=False),
            signals=SimpleNamespace(library_defaults_ready=SimpleNamespace(
                emit=lambda *_: done.set())))
        with patch('asset_library.installed_defaults', side_effect=inventory):
            QtManager._warm_asset_library(manager)
            try:
                self.assertTrue(started.wait(3))
                self.assertTrue(manager._default_scan_running)
                self.assertNotEqual(callers[0], threading.current_thread())
            finally:
                release.set()
            self.assertTrue(done.wait(3))

    def test_interactive_model_and_map_tasks_preempt_warmup(self):
        manager = SimpleNamespace(busy=False,
            asset_library_controller=SimpleNamespace(busy=False),
            editors=[], library_previews=[], level_editors=[])
        self.assertFalse(QtManager._foreground_assets_active(manager))
        manager.library_previews.append(SimpleNamespace(
            mode='models', _model_prepared=False, _model_pool=None))
        self.assertTrue(QtManager._foreground_assets_active(manager))
        manager.library_previews.clear()
        manager.level_editors.append(SimpleNamespace(
            viewport=SimpleNamespace(_preparing=True, _streaming=False)))
        self.assertTrue(QtManager._foreground_assets_active(manager))


if __name__ == '__main__':
    unittest.main()
