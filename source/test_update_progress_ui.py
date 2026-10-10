"""Update startup prompting and visible UI progress regressions."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from app_updater import Update
from dark_theme import apply_theme
from pak_manager_gui import Manager


class UpdateProgressUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        apply_theme(self.root)
        self.addCleanup(self.root.destroy)
        self.manager = Manager.__new__(Manager)
        self.manager.window = self.root
        self.manager.update_status = tk.StringVar(master=self.root, value='Not checked')
        self.manager._update_prompt_pending = False
        self.manager.update_in_progress = False
        self.manager.available_update = None
        self.manager.busy = False
        self.manager._update_progress_window = None
        self.manager.update_button = ttk.Button(self.root)
        self.version = Update('f'*40, '2.10.2', '0'*64, 'https://example.org', 1)

    def test_startup_discovery_prompts_when_ready(self):
        with patch.object(self.manager, 'offer_update') as offer:
            self.manager.on_update_check(self.version, None, True)
            self.assertTrue(self.manager._update_prompt_pending)
            self.manager._offer_pending_update()
            offer.assert_called_once_with()
            self.assertFalse(self.manager._update_prompt_pending)
        self.assertIn('2.10.2', self.manager.update_status.get())

    def test_pending_prompt_waits_until_operation_ends(self):
        self.manager.on_update_check(self.version, None, True)
        self.manager.busy = True
        with patch.object(self.manager, 'offer_update') as offer:
            self.manager._offer_pending_update()
            offer.assert_not_called()
            self.assertTrue(self.manager._update_prompt_pending)
            self.manager.busy = False
            self.manager._offer_pending_update()
            offer.assert_called_once()

    def test_progress_bar_tracks_download_unpack_and_restart(self):
        self.manager._open_update_progress('2.10.2')
        self.root.update()
        try:
            self.manager._set_update_progress('Downloading', 5, 10)
            self.assertEqual(int(float(self.manager._update_bar.cget('value'))), 32)
            self.manager._set_update_progress('Extracting', 10, 20)
            self.assertEqual(int(float(self.manager._update_bar.cget('value'))), 84)
            self.manager._set_update_progress('Ready', 1, 1)
            self.assertEqual(int(float(self.manager._update_bar.cget('value'))), 100)
            self.assertIn('Restarting', self.manager._update_stage.get())
        finally:
            self.manager._close_update_progress()
        self.assertIsNone(self.manager._update_progress_window)

    def test_quiet_no_update_is_not_an_invasive_prompt(self):
        with patch.object(self.manager, 'offer_update') as offer:
            self.manager.on_update_check(None, None, True)
            offer.assert_not_called()
            self.assertFalse(self.manager._update_prompt_pending)


if __name__ == '__main__':
    unittest.main()
