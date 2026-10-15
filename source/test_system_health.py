"""Regression tests for nonintrusive System Health scans and confirmed mutations."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import MagicMock, patch

import system_health as h


class SystemHealthCoreTests(unittest.TestCase):
    def test_only_stale_owned_partials_are_cleanup_candidates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp = root / "Temp"
            temp.mkdir()
            download = root / "Downloads"
            download.mkdir()
            project = root / "Projects"
            project.mkdir()
            old = temp / "unfinished.part"
            fresh = download / "still-downloading.partial"
            original = temp / "never-delete.pak"
            unrelated = project / "project.tmp"
            for path in (old, fresh, original, unrelated):
                path.write_bytes(b"test")
            old_time = time.time() - 3 * 86400
            os.utime(old, (old_time, old_time))
            os.utime(original, (old_time, old_time))
            os.utime(unrelated, (old_time, old_time))
            self.assertEqual(h.storage_candidates(root), [old])
            removed, freed = h.cleanup_stale_temps(root)
            self.assertEqual((removed, freed), (1, 4))
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(original.exists())
            self.assertTrue(unrelated.exists())

    def test_no_firewall_write_while_game_or_launcher_running(self):
        with patch.object(h, "game_running", return_value=True), \
                patch.object(h, "set_offline") as change:
            with self.assertRaisesRegex(RuntimeError, "Close Evolve"):
                h.change_offline_rule("C:/FakeGame", True)
            change.assert_not_called()

    def test_only_manager_owned_firewall_api_is_used(self):
        with patch.object(h, "game_running", return_value=False), \
                patch.object(h, "set_offline", return_value=True) as change:
            self.assertTrue(h.change_offline_rule("game", True))
            change.assert_called_once_with("game", True)

    def test_all_checks_continue_after_partial_failure(self):
        with patch.object(h, "firewall_snapshot", side_effect=OSError("not found")), \
                patch.object(h, "process_snapshot", return_value=("Not running", "-")), \
                patch.object(h, "dump_snapshot", return_value=("Not configured", "-")), \
                patch.object(h, "storage_snapshot", return_value=("Ready", "-")), \
                patch.object(h, "environment_snapshot", return_value=("Ready", "-")):
            snapshot = h.collect_snapshot("", "/data", "/projects", "/stage", "/swap")
        self.assertEqual(set(snapshot), {"firewall", "process", "crash", "storage", "environment"})
        self.assertEqual(snapshot["firewall"][0], "Unavailable")
        self.assertIn("not found", snapshot["firewall"][1])
        self.assertEqual(snapshot["storage"][0], "Ready")

    def test_missing_psutil_refuses_firewall_mutation(self):
        with patch.object(h, "psutil", None):
            self.assertTrue(h.game_running())  # fail closed
            self.assertEqual(h.process_snapshot()[0], "Unavailable")

    def test_export_excludes_sensitive_or_large_files(self):
        with TemporaryDirectory() as tmp:
            data = Path(tmp) / "Data"
            log = data / "Logs"
            log.mkdir(parents=True)
            (log / "startup.log").write_text("diagnostic")
            (log / "password.txt").write_text("secret")
            (log / "large.log").write_bytes(b"x" * (16 * 1024 * 1024 + 1))
            (data / "CrashDumps").mkdir()
            (data / "CrashDumps" / "sensitive.dmp").write_bytes(b"dump")
            output = Path(tmp) / "support.zip"
            with patch.object(h, "process_snapshot", return_value=("Not running", "-")), \
                    patch.object(h, "firewall_snapshot", return_value=("Offline", "-")), \
                    patch.object(h, "storage_snapshot", return_value=("Ready", "-")), \
                    patch.object(h, "environment_snapshot", return_value=("Ready", "-")), \
                    patch.object(h, "dump_snapshot", return_value=("Off", "-")):
                h.support_zip(output, data, "", tmp, tmp, tmp)
            from zipfile import ZipFile
            with ZipFile(output) as archive:
                names = archive.namelist()
                self.assertIn("system-health.json", names)
                self.assertIn("logs/01-startup.log", names)
                self.assertFalse(any("dmp" in name or "password" in name for name in names))
            with self.assertRaises(FileExistsError):
                h.support_zip(output, data, "", tmp, tmp, tmp)


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM", "offscreen") == "offscreen",
                     "Qt offscreen UI test")
class SystemHealthQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_home_panel_collapsed_no_scans_before_expand(self):
        from qt_home_dashboard import HomeDashboard
        manager = MagicMock()
        manager.game_root = None
        manager.settings = {}
        manager.projects_path = "/projects"
        manager.stage_path = "/stage"
        manager.swap_path = "/swap"
        manager._find_game_root.return_value = ""
        with patch("qt_system_health.collect_snapshot") as scan:
            home = HomeDashboard(manager)
            panel = home.system_health
            self.assertEqual(panel.objectName(), "systemHealthPanel")
            self.assertFalse(panel.toggle.isChecked())
            self.assertFalse(panel.disclosure.body.isVisible())
            self.assertFalse(panel._scanned)
            scan.assert_not_called()
            # Don't start a background worker while testing widget geometry.
            with patch.object(panel._thread_pool, "start") as start:
                panel.toggle.click()
                self.assertTrue(panel.toggle.isChecked())
                self.assertEqual(len(panel.labels), 5)
                start.assert_called_once()
            home.close()

    def test_result_rows_show_five_independent_checks(self):
        from qt_system_health import SystemHealthPanel
        manager = MagicMock()
        panel = SystemHealthPanel(manager)
        values = {key: ("Ready", key + " details") for key, *_ in panel.LABELS}
        with patch.object(panel, "refresh") as refresh:
            panel._finished("snapshot", values, "")
            self.assertTrue(panel._scanned)
            self.assertEqual(len(panel.labels), 5)
            self.assertIn("firewall details", panel.labels["firewall"].text())
            refresh.assert_not_called()
        panel.close()


if __name__ == "__main__":
    unittest.main()
