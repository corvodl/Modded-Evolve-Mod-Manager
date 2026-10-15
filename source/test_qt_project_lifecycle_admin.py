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
        from PySide6.QtWidgets import QApplication
        from qt_manager import QtManager
        from qt_workspace_editor import QtWorkspaceEditor
        from launch_integration import SwapStatus
        from shiboken6 import isValid
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / 'Project'
            (workspace/'files').mkdir(parents=True)
            with patch('qt_manager.settings_source', return_value=root/'settings.json'), \
                    patch('qt_manager.allowed_paks', return_value={}), \
                    patch('qt_manager.state_status', return_value=SwapStatus('missing', 'Not prepared')), \
                    patch('qt_manager.save_settings', return_value=None):
                manager = QtManager()
                manager.projects_path = str(root/'Projects')
                editor = None
                try:
                    # Use a minimal mocked editor to exercise manager reuse
                    # separately from PAK-manifest-specific editor initialization.
                    with patch.object(QtWorkspaceEditor, '_read_records', return_value={}):
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
                        self.assertEqual(len([x for x in manager.editors if x is editor]), 1)
                finally:
                    if editor is not None:
                        editor.close()
                    manager.close()
