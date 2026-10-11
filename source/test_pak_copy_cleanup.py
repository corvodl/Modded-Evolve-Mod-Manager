"""Conservative PAK cleanup tests with synthetic files (never touches game)."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from pak_copy_cleanup import scan_duplicate_paks, validate_recycle


class DuplicateCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = self.root / "EvolveGame"
        (self.game / "bin64_SteamRetail").mkdir(parents=True)
        (self.game / "bin64_SteamRetail" / "Evolve.exe").write_bytes(b"fixture")
        self.pak = self.game / "Game" / "characters_hunters_shared_ts.pak"
        self.pak.parent.mkdir()
        self.pak.write_bytes(b"same" * 1024)
        self.copy = Path(str(self.pak) + ".customkey-recovery-mod-abc")
        self.copy.write_bytes(self.pak.read_bytes())
        self.oldready = Path(str(self.pak) + ".customkey-ready.saved-abcd")
        self.oldready.write_bytes(self.pak.read_bytes())
        self.other = Path(str(self.pak) + ".saved-different")
        self.other.write_bytes(b"other data")
        self.original = Path(str(self.pak) + ".customkey-original")
        self.original.write_bytes(b"required original")
        self.swap = self.root / "swap"
        self.swap.mkdir()

    def test_scan_only_exact_copies_not_protected_original(self):
        rows = scan_duplicate_paks(self.game)
        results = {r.path: r for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual({r.path for r in rows if r.reclaimable},
                         {self.copy, self.oldready})
        self.assertFalse(results[self.original].reclaimable)
        self.assertFalse(results[self.other].reclaimable)
        self.assertEqual(results[self.original].status, "protected")
        self.assertEqual(self.pak.read_bytes(), b"same" * 1024)

    def test_restore_backups_prevent_recycling(self):
        rows = scan_duplicate_paks(self.game)
        eligible = [r for r in rows if r.path == self.copy]
        with patch("orphaned_swap_recovery.ensure_game_closed"), patch(
                "launch_integration.state_status") as state:
            state.return_value = type("State", (), {"phase": "restored",
                                                       "game": str(self.game)})()
            with self.assertRaisesRegex(ValueError, "Original or incomplete"):
                validate_recycle(self.game, self.swap, eligible)

    def test_journal_reference_blocks_recycling(self):
        root = self.root / "journal"
        root.mkdir()
        (root / "SwapRecovery-fixture.json").write_text(
            json.dumps({"modified_copy": str(self.copy)}))
        rows = scan_duplicate_paks(self.game, journal_roots=[root])
        copy = next(r for r in rows if r.path == self.copy)
        self.assertFalse(copy.reclaimable)
        self.assertIn("journal", copy.detail)

    def test_only_verified_duplicates_after_recovery(self):
        self.original.unlink()
        rows = scan_duplicate_paks(self.game)
        selected = next(r for r in rows if r.path == self.copy)
        with patch("orphaned_swap_recovery.ensure_game_closed"), patch(
                "launch_integration.state_status") as state:
            state.return_value = type("State", (), {
                "phase": "restored", "game": str(self.game)})()
            self.assertEqual(
                validate_recycle(self.game, self.swap, [selected]), (self.copy,))
            self.copy.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "since scan|identical"):
                validate_recycle(self.game, self.swap, [selected])

    def test_swapped_journal_refuses_all_recycling(self):
        self.original.unlink()
        selected = next(r for r in scan_duplicate_paks(self.game)
                        if r.path == self.copy)
        with patch("orphaned_swap_recovery.ensure_game_closed"), patch(
                "launch_integration.state_status") as state:
            state.return_value = type("State", (), {
                "phase": "swapped", "game": str(self.game)})()
            with self.assertRaisesRegex(ValueError, "Restore Game Files"):
                validate_recycle(self.game, self.swap, [selected])

    def test_live_or_unknown_files_never_eligible(self):
        unknown = Path(str(self.pak) + ".customkey-unknown-abc")
        unknown.write_bytes(self.pak.read_bytes())
        found = scan_duplicate_paks(self.game)
        self.assertFalse(next(row for row in found if row.path == unknown).reclaimable)
        self.assertNotIn(self.pak, [r.path for r in found])


if __name__ == "__main__":
    unittest.main()
