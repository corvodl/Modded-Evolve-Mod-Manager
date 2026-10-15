"""Injector restoration regressions: never launch past a stale custom-key backup."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from injector_guard import inspect_injector, require_clean_injector, KEY_OFFSET, KEY_SIZE


class InjectorGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        folder = self.root / "bin64_SteamRetail"
        folder.mkdir()
        self.active = folder / "inject.dll"
        self.backup = folder / "inject.dll.customkey-original"
        self.original = bytes((i % 251 for i in range(4096)))
        self.active.write_bytes(self.original)

    def test_clean_without_backup(self):
        self.assertEqual(inspect_injector(self.root).state, "no-backup")
        require_clean_injector(self.root)

    def test_leftover_custom_key_is_rejected_without_mutation(self):
        self.backup.write_bytes(self.original)
        changed = bytearray(self.original)
        changed[KEY_OFFSET:KEY_OFFSET + KEY_SIZE] = b"z" * KEY_SIZE
        self.active.write_bytes(changed)
        self.assertEqual(inspect_injector(self.root).state, "key-patched")
        with self.assertRaises(RuntimeError):
            require_clean_injector(self.root)
        self.assertEqual(self.active.read_bytes(), bytes(changed))
        self.assertEqual(self.backup.read_bytes(), self.original)

    def test_unexpected_changes_are_not_trusted(self):
        self.backup.write_bytes(self.original)
        changed = bytearray(self.original)
        changed[3] ^= 0xff
        self.active.write_bytes(changed)
        self.assertEqual(inspect_injector(self.root).state, "different")
        with self.assertRaises(RuntimeError):
            require_clean_injector(self.root)

    def test_identical_files_with_active_backup_still_block(self):
        self.backup.write_bytes(self.original)
        self.assertEqual(inspect_injector(self.root).state, "backup-present")
        with self.assertRaises(RuntimeError):
            require_clean_injector(self.root)

    def test_missing_active_is_unsafe(self):
        self.backup.write_bytes(self.original)
        self.active.unlink()
        self.assertEqual(inspect_injector(self.root).state, "unsafe")

    def test_invalid_backup_size_is_unsafe(self):
        self.backup.write_bytes(b"invalid")
        self.assertEqual(inspect_injector(self.root).state, "unsafe")

    def test_launch_commands_reject_orphan_with_prepared_journal(self):
        import json
        from launch_integration import launcher_commands, STATE_FILE
        swap = self.root / "swap"
        swap.mkdir()
        stage = self.root / "stage"
        stage.mkdir()
        (stage / "stage_status.json").write_text("{}")
        for name in ("controlled_swap.py", "reusable_restore.py", "auto_launch_frida.py"):
            (swap / name).write_text("")
        (swap / STATE_FILE).write_text(json.dumps({"game": str(self.root)}))
        self.backup.write_bytes(self.original)
        with self.assertRaises(RuntimeError):
            launcher_commands(swap, stage, "prepared")


if __name__ == "__main__":
    unittest.main()
