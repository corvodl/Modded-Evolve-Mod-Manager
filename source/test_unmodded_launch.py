"""Normal client launch preflight never swaps staged PAKs or changes game data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from launch_integration import SwapStatus, unmodded_launch_target


class NormalLaunchPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = self.root / 'EvolveGame'
        exe = self.game / 'bin64_SteamRetail' / 'Evolve.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'synthetic game executable')
        self.client = self.root / 'ModdedEvolveLauncher.exe'
        self.client.write_bytes(b'synthetic client executable')
        self.swap = self.root / 'swap'
        self.swap.mkdir()
        (self.swap / 'launcher_config.json').write_text(
            json.dumps({'launcher': str(self.client)}), encoding='utf-8')

    def test_uses_configured_client_with_originals_in_place(self):
        with patch('launch_integration.state_status',
                   return_value=SwapStatus('prepared', 'Ready', game=str(self.game))):
            game, client = unmodded_launch_target(self.swap)
            self.assertEqual(game, self.game.resolve())
            self.assertEqual(client.resolve(), self.client.resolve())
        self.assertTrue((self.game / 'bin64_SteamRetail' / 'Evolve.exe').is_file())

    def test_refuses_active_swap_even_with_configured_client(self):
        with patch('launch_integration.state_status',
                   return_value=SwapStatus('swapped', 'Mods installed',
                                           game=str(self.game))):
            with self.assertRaisesRegex(RuntimeError, 'Restore Game Files'):
                unmodded_launch_target(self.swap)

    def test_refuses_orphaned_originals_even_if_journal_claims_clean(self):
        pak = self.game / 'Game' / 'sample.pak'
        pak.parent.mkdir(parents=True)
        pak.write_bytes(b'modded')
        Path(str(pak) + '.customkey-original').write_bytes(b'original')
        with patch('launch_integration.state_status',
                   return_value=SwapStatus('restored', 'Originals restored',
                                           game=str(self.game))):
            with self.assertRaisesRegex(RuntimeError, 'backups'):
                unmodded_launch_target(self.swap)
        self.assertEqual(pak.read_bytes(), b'modded')

    def test_refuses_unconfigured_normal_client(self):
        (self.swap / 'launcher_config.json').unlink()
        with patch('launch_integration.state_status',
                   return_value=SwapStatus('missing', 'Not configured')):
            with self.assertRaisesRegex(FileNotFoundError, '[Nn]ormal'):
                unmodded_launch_target(self.swap, self.game)


if __name__ == '__main__':
    unittest.main()
