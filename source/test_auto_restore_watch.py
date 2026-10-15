"""Regression checks for the detached post-game restoration watchdog."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "launcher_support"))
import auto_restore_watch as watch


class AutoRestoreWatchTests(unittest.TestCase):
    def test_only_restores_when_both_game_and_launcher_are_closed(self):
        self.assertFalse(watch.should_restore({"evolve.exe"}, True, 20, True, 999))
        self.assertFalse(watch.should_restore({"moddedevolvelauncher.exe"}, True, 20, True, 999))
        self.assertFalse(watch.should_restore(set(), True, 3, True, 999))
        self.assertTrue(watch.should_restore(set(), True, 4, True, 3))

    def test_fresh_launch_requires_game_observed_or_timeout(self):
        self.assertFalse(watch.should_restore(set(), False, 20, True, 119))
        self.assertTrue(watch.should_restore(set(), False, 4, True, 120))
        self.assertTrue(watch.should_restore(set(), False, 4, False, 2))

    def test_watch_waits_for_game_exit_and_calls_existing_restorer_once(self):
        processes = iter([
            {"moddedevolvelauncher.exe"},
            {"evolve.exe", "moddedevolvelauncher.exe"},
            {"evolve.exe"}, set(), set(), set(), set(),
        ])
        now = iter(range(100))
        with patch.object(watch.swap, "require_state",
                          return_value={"phase": "swapped"}), \
             patch.object(watch.swap, "task_names", side_effect=lambda: next(processes)), \
             patch("reusable_restore.main", return_value=0) as restore:
            result = watch.watch(fresh=True, interval=0,
                                 monotonic=lambda: next(now),
                                 sleeper=lambda _interval: None)
        self.assertEqual(result, 0)
        restore.assert_called_once()

    def test_watch_refuses_unknown_journal_phase(self):
        with patch.object(watch.swap, "require_state",
                          return_value={"phase": "unsafe"}), \
             patch("reusable_restore.main") as restore:
            with self.assertRaisesRegex(RuntimeError, "unsafe phase"):
                watch.watch(interval=0)
        restore.assert_not_called()

    def test_already_restored_does_not_change_files(self):
        for phase in ("prepared", "restored"):
            with self.subTest(phase=phase), \
                 patch.object(watch.swap, "require_state",
                              return_value={"phase": phase}), \
                 patch("reusable_restore.main") as restore:
                self.assertEqual(watch.watch(interval=0), 0)
                restore.assert_not_called()


if __name__ == "__main__":
    unittest.main()
