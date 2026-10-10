"""Windows desktop shortcut tests; no user's Desktop is modified by tests."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import base64
import os
import unittest
from unittest.mock import patch

import desktop_shortcut


class DesktopShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root / 'EvolveModManager.exe'
        self.executable.write_bytes(b'exe')
        self.icon = self.root / 'hunt.ico'
        self.icon.write_bytes(b'ico')
        self.link = self.root / desktop_shortcut.SHORTCUT_NAME

    def test_creates_named_shortcut_with_same_bundled_icon_and_working_directory(self):
        self.link.write_bytes(b'link')
        def check_call(argv, **kwargs):
            self.assertEqual(argv[:3], ['powershell.exe', '-NoProfile', '-NonInteractive'])
            self.assertEqual(kwargs['env']['EVOLVE_SHORTCUT_TARGET'], str(self.executable))
            self.assertEqual(kwargs['env']['EVOLVE_SHORTCUT_ICON'], str(self.icon))
            encoded = argv[argv.index('-EncodedCommand') + 1]
            script = base64.b64decode(encoded).decode('utf-16le')
            self.assertIn("$link.IconLocation = \"$icon,0\"", script)
            self.assertIn("$link.WorkingDirectory = Split-Path", script)
            self.assertIn("'DesktopDirectory'", script)
            self.assertIn("Evolve Mod Manager.lnk", script)
            self.assertNotIn(str(self.executable), script)
            return SimpleNamespace(returncode=0, stdout=str(self.link) + '\n', stderr='')
        with patch.object(desktop_shortcut.os, 'name', 'nt'), \
             patch.object(desktop_shortcut.subprocess, 'run', side_effect=check_call):
            outcome = desktop_shortcut.create_desktop_shortcut(
                self.executable, self.icon)
        self.assertEqual(outcome, self.link)

    def test_non_windows_platform_is_not_silently_modified(self):
        with patch.object(desktop_shortcut.os, 'name', 'posix'), \
             patch.object(desktop_shortcut.subprocess, 'run') as run:
            with self.assertRaisesRegex(OSError, 'Windows only'):
                desktop_shortcut.create_desktop_shortcut(self.executable, self.icon)
            run.assert_not_called()

    def test_does_not_create_shortcut_for_game_or_missing_icon(self):
        invalid = self.root / 'Evolve.exe'
        invalid.write_bytes(b'game')
        with patch.object(desktop_shortcut.os, 'name', 'nt'), \
             patch.object(desktop_shortcut.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'EvolveModManager.exe'):
                desktop_shortcut.create_desktop_shortcut(invalid, self.icon)
            self.icon.unlink()
            with self.assertRaises(FileNotFoundError):
                desktop_shortcut.create_desktop_shortcut(self.executable, self.icon)
            run.assert_not_called()

    def test_failed_windows_script_does_not_claim_success(self):
        with patch.object(desktop_shortcut.os, 'name', 'nt'), \
             patch.object(desktop_shortcut.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=1, stdout='', stderr='Desktop permission denied')):
            with self.assertRaisesRegex(RuntimeError, 'Desktop permission denied'):
                desktop_shortcut.create_desktop_shortcut(self.executable, self.icon)

    def test_windows_must_report_real_lnk_not_just_zero_exit(self):
        with patch.object(desktop_shortcut.os, 'name', 'nt'), \
             patch.object(desktop_shortcut.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=0, stdout=str(self.link) + '\n', stderr='')):
            with self.assertRaisesRegex(RuntimeError, 'did not create'):
                desktop_shortcut.create_desktop_shortcut(self.executable, self.icon)


if __name__ == '__main__':
    unittest.main()
