import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from client_profile_details import profile_sources, read_details, MAX_BYTES
from qt_account_profile import AccountProfileInspector


class ClientProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'ModdedEvolveLauncher' / 'profile.json'
        self.profile.parent.mkdir()
        self.profile.write_text(json.dumps({'displayName': 'Fixture player', 'level': 12,
                                           'wins': 0, 'token': 'must not display',
                                           'identity': {'secret': 'must not display'}}))

    def test_discovery_is_scoped_and_deduplicated(self):
        rows = profile_sources(self.profile.parent / 'launcher.exe', self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], self.profile)

    def test_fields_are_allowlisted_and_source_unchanged(self):
        before = self.profile.read_bytes()
        self.assertEqual(read_details(self.profile), [('Display name', 'Fixture player'),
                                                    ('Level', '12'), ('Wins', '0')])
        self.assertEqual(self.profile.read_bytes(), before)

    def test_invalid_encoded_and_oversized_profiles(self):
        for data in (b'[]', b'bad', b'TRSEVLVPC', b' ' * (MAX_BYTES + 1), b'{"token":"secret"}'):
            self.profile.write_bytes(data)
            with self.assertRaises(ValueError):
                read_details(self.profile)

    def test_dialog_autoload_refresh_and_clear_stale_values(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}):
            dialog = AccountProfileInspector()
        try:
            self.assertEqual(dialog.tabs.tabText(dialog.tabs.currentIndex()), 'Client details')
            self.assertEqual(dialog.client_table.item(0, 1).text(), 'Fixture player')
            self.assertFalse(dialog.export_btn.isEnabled())
            self.profile.write_text('{"level":15}')
            dialog.refresh_client_details()
            self.assertEqual(dialog.client_table.rowCount(), 1)
            self.assertEqual(dialog.client_table.item(0, 1).text(), '15')
            self.profile.unlink()
            dialog.refresh_client_details()
            self.assertEqual(dialog.client_table.rowCount(), 0)
            self.assertIn('No local profile', dialog.client_status.text())
        finally:
            dialog.close()
