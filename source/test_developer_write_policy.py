"""Session-only Developer write permission regressions.

The authorization gate is independent of UI control appearance: every memory
write and patch operation must ask it at execution time.
"""
from unittest import TestCase
from developer_write_policy import require_developer_write


class WritePolicyTests(TestCase):
    def test_read_only_is_default_even_in_offline_game(self):
        with self.assertRaises(PermissionError):
            require_developer_write(False,False,False,lambda:'game',lambda _:True)

    def test_offline_requires_verified_network_isolation(self):
        with self.assertRaises(PermissionError):
            require_developer_write(True,False,False,lambda:'game',lambda _:False)
        self.assertEqual(require_developer_write(
            True,False,False,lambda:'game',lambda _:True),'offline-isolated')

    def test_online_requires_both_explicit_session_optin_and_network_switch(self):
        self.assertEqual(require_developer_write(
            True,True,True,lambda:'game',lambda _:False),'online-consented')
        with self.assertRaises(PermissionError):
            require_developer_write(True,True,False,lambda:'game',lambda _:False)
        with self.assertRaises(PermissionError):
            require_developer_write(True,False,True,lambda:'game',lambda _:False)

    def test_read_only_stays_available_without_a_write_provider(self):
        with self.assertRaises(PermissionError):
            require_developer_write(True,False,False,None,lambda _:True)
