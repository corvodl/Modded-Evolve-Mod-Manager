import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import app_runtime

class PackagingTests(unittest.TestCase):
    def test_source_command_unchanged(self):
        cmd = [sys.executable, '-u', 'tool.py', 'build']
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(app_runtime.worker_command(cmd), cmd)

    def test_frozen_command_preserves_paths_and_arguments(self):
        with tempfile.TemporaryDirectory(prefix='mod manager ') as tmp:
            root = Path(tmp)
            (root/'EvolveModWorker.exe').touch()
            with patch.object(sys, 'frozen', True, create=True), patch.object(app_runtime, 'APP_HOME', root):
                self.assertEqual(app_runtime.worker_command([sys.executable, '-u', 'C:/My Mods/helper.py', '--stage-dir', 'C:/My Mods/staged']),
                                 [str(root/'EvolveModWorker.exe'), 'C:/My Mods/helper.py', '--stage-dir', 'C:/My Mods/staged'])

    def test_worker_keeps_helper_location_and_sibling_import(self):
        with tempfile.TemporaryDirectory(prefix='swap folder ') as tmp:
            root=Path(tmp)
            (root/'sibling.py').write_text('VALUE=42')
            (root/'check.py').write_text('from pathlib import Path\nimport sibling,sys\nassert sibling.VALUE==42\nassert Path(__file__).parent==Path(sys.argv[1])\nprint("ok",flush=True)\n')
            result=subprocess.run([sys.executable, str(Path(__file__).with_name('worker_entry.py')), str(root/'check.py'), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ok',result.stdout)


class SettingsLayoutTests(unittest.TestCase):
    def test_legacy_settings_preserved_and_new_settings_take_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root/'manager_settings.json'
            legacy.write_text('{"projects":"C:/existing projects"}')
            settings = root/'Data'/'Settings'/'manager_settings.json'
            with patch.object(app_runtime, 'APP_HOME', root), patch.object(app_runtime, 'SETTINGS_FILE', settings):
                self.assertEqual(app_runtime.settings_source(), legacy)
                app_runtime.save_settings({'projects': 'C:/existing projects'})
                self.assertEqual(app_runtime.settings_source(), settings)
                self.assertTrue(legacy.exists())
                import json
                self.assertEqual(json.loads(settings.read_text())['projects'], 'C:/existing projects')
