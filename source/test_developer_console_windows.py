"""Developer polling and diagnostics must not flash CMD/PowerShell on Windows.

Simulated Windows subprocess flags; no real processes, game files or network.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import developer_memory
import developer_network_lab
import developer_event_live


NO_WINDOW = 0x08000000


class HiddenDeveloperConsoleTests(unittest.TestCase):
    def test_periodic_tasklist_poll_does_not_open_console(self):
        fake = SimpleNamespace(returncode=0, stdout='"Evolve.exe","1456"\n')
        with patch.object(developer_memory, '_require_windows'), \
             patch.object(developer_memory.subprocess, 'CREATE_NO_WINDOW', NO_WINDOW,
                          create=True), \
             patch.object(developer_memory.subprocess, 'run',
                          return_value=fake) as runner:
            self.assertEqual(developer_memory.list_evolve_processes(), [1456])
            command = runner.call_args.args[0]
            self.assertEqual(command[0], 'tasklist')
            self.assertEqual(runner.call_args.kwargs['creationflags'], NO_WINDOW)

    def test_network_pid_inspection_hides_powershell(self):
        handle = MagicMock()
        fake = SimpleNamespace(returncode=0, stdout='[]')
        with patch.object(developer_memory, '_game_handle',
                          return_value=(handle, 42)), \
             patch.object(developer_network_lab.subprocess, 'CREATE_NO_WINDOW',
                          NO_WINDOW, create=True), \
             patch.object(developer_network_lab.subprocess, 'run',
                          return_value=fake) as runner:
            self.assertEqual(developer_network_lab.tcp_connections_for_pid(1456), ())
            handle.CloseHandle.assert_called_once_with(42)
            self.assertEqual(runner.call_args.args[0][0], 'powershell.exe')
            self.assertEqual(runner.call_args.kwargs['creationflags'], NO_WINDOW)

    def test_event_bridge_listener_inspection_hides_powershell(self):
        fake = SimpleNamespace(returncode=0, stdout='OWNED\n')
        with patch.object(developer_event_live, 'os',
                          SimpleNamespace(name='nt')), \
             patch.object(developer_event_live.subprocess, 'CREATE_NO_WINDOW',
                          NO_WINDOW, create=True), \
             patch.object(developer_event_live.subprocess, 'run',
                          return_value=fake) as runner:
            developer_event_live.verify_local_listener(1456, 49900)
            self.assertEqual(runner.call_args.args[0][0], 'powershell.exe')
            self.assertEqual(runner.call_args.kwargs['creationflags'], NO_WINDOW)


if __name__ == '__main__':
    unittest.main()
