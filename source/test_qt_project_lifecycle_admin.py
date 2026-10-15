"""Regression coverage for Windows elevation and Qt project reopening."""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import windows_admin_launch


class AdminLaunchTests(unittest.TestCase):
    def test_windows_manifest_applies_only_to_gui(self):
        for name in ('EvolveModManager.spec', 'EvolveModManagerQt.spec'):
            spec = Path(__file__).with_name(name).read_text('utf-8')
            self.assertIn("uac_admin=(entry == 'qt_manager')", spec)

    def test_source_windows_launch_requests_uac(self):
        shell = MagicMock()
        shell.IsUserAnAdmin.return_value = False
        shell.ShellExecuteW.return_value = 42
        with patch.object(windows_admin_launch.os, 'name', 'nt'), \
                patch.object(windows_admin_launch.sys, 'frozen', False, create=True), \
                patch.object(windows_admin_launch.sys, 'argv', ['qt_manager.py', '--example']), \
                patch.object(windows_admin_launch.ctypes, 'windll', create=True) as dll:
            dll.shell32 = shell
            self.assertFalse(windows_admin_launch.ensure_manager_administrator())
            args = shell.ShellExecuteW.call_args.args
            self.assertEqual(args[1], 'runas')
            self.assertIn('qt_manager.py', args[3])

    def test_admin_does_not_relaunch(self):
        with patch.object(windows_admin_launch.os, 'name', 'nt'), \
                patch.object(windows_admin_launch.sys, 'frozen', False, create=True), \
                patch.object(windows_admin_launch.ctypes, 'windll', create=True) as dll:
            dll.shell32.IsUserAnAdmin.return_value = True
            self.assertTrue(windows_admin_launch.ensure_manager_administrator())
            dll.shell32.ShellExecuteW.assert_not_called()


@unittest.skipUnless(os.environ.get('QT_QPA_PLATFORM') == 'offscreen', 'Offscreen Qt CI test')
class QtProjectResumeTests(unittest.TestCase):
    def test_reopen_editor_reuses_valid_timers_and_keeps_editable_copy(self):
        """Test manager reuse independently of GPU/editor resource setup."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QWidget
        from qt_manager import QtManager
        from launch_integration import SwapStatus
        from shiboken6 import isValid
        app = QApplication.instance() or QApplication([])

        class TestEditor(QWidget):
            def __init__(self, parent, workspace, archive_map=None):
                super().__init__(parent)
                self.workspace = Path(workspace)
                self.records = {}
                self.read_only_library = False
                self.recovery_timer = QTimer(self)
                self.recovery_timer.setInterval(30000)
                self.recovery_timer.start()

            def resume_project_editor(self):
                self.recovery_timer.start()

            def closeEvent(self, event):
                self.recovery_timer.stop()
                super().closeEvent(event)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "Project"
            workspace.mkdir()
            with patch("qt_manager.settings_source", return_value=root / "settings.json"), \
                    patch("qt_manager.allowed_paks", return_value={}), \
                    patch("qt_manager.state_status",
                          return_value=SwapStatus("missing", "Not prepared")), \
                    patch("qt_manager.save_settings", return_value=None), \
                    patch("qt_workspace_editor.QtWorkspaceEditor", TestEditor):
                manager = QtManager()
                editor = None
                try:
                    editor = manager._open_project_workspace(workspace)
                    first_timer = editor.recovery_timer
                    self.assertTrue(isValid(first_timer))
                    editor.close()
                    app.processEvents()
                    self.assertFalse(first_timer.isActive())
                    reopened = manager._open_project_workspace(workspace)
                    self.assertIs(reopened, editor)
                    self.assertTrue(isValid(reopened.recovery_timer))
                    self.assertTrue(reopened.recovery_timer.isActive())
                    self.assertEqual(sum(x is editor for x in manager.editors), 1)
                finally:
                    if editor is not None:
                        editor.close()
                    manager.close()
