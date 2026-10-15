"""Read-only preflight, Event Viewer and support-redaction regression tests."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from launch_integration import SwapStatus
import system_health_extras as extra


class PrelaunchSafetyTests(unittest.TestCase):
    def test_unsafe_journal_blocks_unmodded_and_modded(self):
        with TemporaryDirectory() as tmp:
            game = Path(tmp)
            exe = game / "bin64_SteamRetail" / "Evolve.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"test")
            for mode in (False, True):
                errors, _ = extra.prelaunch_check(
                    swap_root=tmp, game_root=game, modded=mode,
                    status=SwapStatus("swapped", "Needs restoration"),
                    process_running=False, require_admin=True)
                self.assertTrue(any("Restore originals" in e for e in errors))

    def test_clean_setup_keeps_launch_without_optional_runtime_probes(self):
        with TemporaryDirectory() as tmp:
            game = Path(tmp)
            exe = game / "bin64_SteamRetail" / "Evolve.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"test")
            errors, notes = extra.prelaunch_check(
                swap_root=tmp, game_root=game, modded=True,
                status=SwapStatus("prepared", "Ready"),
                process_running=False, require_admin=True)
            self.assertEqual(errors, ())
            self.assertIn("offline protection", " ".join(notes))

    def test_running_game_refuses_new_launch(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "bin64_SteamRetail" / "Evolve.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"x")
            errors, _ = extra.prelaunch_check(
                swap_root=tmp, game_root=tmp, modded=False,
                status=SwapStatus("restored", "Clean"),
                process_running=True, require_admin=True)
            self.assertTrue(any("Close Evolve" in e for e in errors))

    def test_missing_exe_reports_error_without_writing(self):
        with TemporaryDirectory() as tmp:
            errors, _ = extra.prelaunch_check(
                swap_root=tmp, game_root=tmp, modded=True,
                status=SwapStatus("missing", "Fresh"), process_running=False,
                require_admin=True)
            self.assertTrue(any("executable" in item for item in errors))


class ReportPrivacyTests(unittest.TestCase):
    def test_private_paths_tokens_and_emails_redacted(self):
        raw = ("C:\\Users\\secretuser\\Downloads\\Evolve.exe "
               "admin@example.org password=abcd1234 "
               "access_token:ABC")
        result = extra.redact_support_text(raw)
        for secret in ("secretuser", "example.org", "abcd1234", "ABC"):
            self.assertNotIn(secret, result)
        self.assertIn("[LOCAL PATH]", result)

    def test_discord_report_is_bounded_and_does_not_embed_diagnostic_details(self):
        report = extra.format_discord_report(
            os_version="Windows 11", manager_version="1.2.1",
            client_version="0.1.54", game_version="3.6",
            checks={"firewall": ("Offline", "C:\\Users\\private\\key.txt secret")},
            crashes=("2026-10-15 Fault: Evolve.exe",))
        self.assertIn("Support Report", report)
        self.assertIn("Firewall: Offline", report)
        self.assertNotIn("C:\\Users\\private", report)
        self.assertNotIn("key.txt", report)
        self.assertIn("Recent crashes", report)
        self.assertIn("Review private details", report)


class WindowsEventHistoryTests(unittest.TestCase):
    XML = ('<?xml version="1.0"?>'
           '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
           '<System><EventID>1000</EventID>'
           '<TimeCreated SystemTime="2026-10-15T21:30:00.000Z"/></System>'
           '<EventData><Data Name="AppName">Evolve.exe</Data>'
           '<Data Name="ModuleName">KERNELBASE.dll</Data></EventData>'
           '</Event>'
           '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
           '<System><EventID>1000</EventID></System>'
           '<EventData><Data Name="AppName">notepad.exe</Data></EventData>'
           '</Event>')

    def test_crash_history_filters_to_evolve_and_uses_read_only_command(self):
        fake_os = SimpleNamespace(name="nt", environ={})
        runner = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=self.XML, stderr=""))
        with patch.object(extra, "os", fake_os):
            result = extra.windows_crash_history(runner=runner, limit=4)
        self.assertEqual(len(result), 1)
        self.assertIn("KERNELBASE.dll", result[0])
        command = runner.call_args.args[0]
        self.assertEqual(command[1:3], ["qe", "Application"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 15)
        self.assertNotIn("shell", runner.call_args.kwargs)

    def test_untrusted_xml_is_rejected(self):
        fake_os = SimpleNamespace(name="nt", environ={})
        runner = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="<broken>", stderr=""))
        with patch.object(extra, "os", fake_os):
            with self.assertRaisesRegex(RuntimeError, "invalid event XML"):
                extra.windows_crash_history(runner=runner)


if __name__ == "__main__":
    unittest.main()
