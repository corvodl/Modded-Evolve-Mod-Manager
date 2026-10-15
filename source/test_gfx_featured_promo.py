"""Guarded featured-menu presets: mock FFDec, no commercial game data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gfx_featured_promo import patch_featured_actionscript, stage_featured_promo
from gfx_edit_backend import create_gfx_session, undo_gfx_session, GfxError
from gfx_synthetic_fixtures import synthetic_gfx


class FeaturedPromoTests(unittest.TestCase):
    SCRIPT = '''function setFeatured(info) {
    var message = "{not a code brace}";
    // Some AS2 comments contain } too
    /* Another brace { inside comments */
    _root.mcFeatureTitle.text = info.name;
}
'''
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = self.root / "Project"
        self.file = self.ws / "files/Libs/UI/main_menu.gfx"
        self.file.parent.mkdir(parents=True)
        self.movie = synthetic_gfx(
            action_message="setFeatured mcFeatureTitle mcFeatureBuy mcFeatureTry")
        self.file.write_bytes(self.movie)
        (self.ws / ".evolve-pak-workspace.json").write_text(json.dumps({
            "entries": [{"path": "Libs/UI/main_menu.gfx", "format": "raw",
                         "sha256": hashlib.sha256(self.movie).hexdigest()}]
        }))
        self.snapshot = create_gfx_session(self.ws, "Libs/UI/main_menu.gfx")

    def test_all_modes_change_only_supported_symbols(self):
        hidden = patch_featured_actionscript(self.SCRIPT, "hide", "all")
        self.assertIn("mcFeatureTitle._visible = false", hidden)
        self.assertIn("mcFeatureButtonBuy._visible = false", hidden)
        self.assertNotIn("removeMovieClip", hidden)
        title = patch_featured_actionscript(self.SCRIPT, "hide", "title")
        self.assertNotIn("mcFeatureButtonTry._visible", title)
        removed = patch_featured_actionscript(self.SCRIPT, "remove", "all")
        self.assertIn("mcFeatureButtonTry.enabled = false", removed)
        self.assertIn("mcFeatureButtonTry.removeMovieClip()", removed)
        self.assertIn('mcFeatureTitle.text = ""', removed)
        replaced = patch_featured_actionscript(
            self.SCRIPT, "custom", "title", 'GOLIATH "MODDED"')
        self.assertIn(r'GOLIATH \"MODDED\"', replaced)
        self.assertNotIn("mcFeatureBuy._visible", replaced)
        self.assertIn('var message = "{not a code brace}"', replaced)

    def test_input_guard_and_duplicate_injection(self):
        with self.assertRaisesRegex(GfxError, "72 printable"):
            patch_featured_actionscript(self.SCRIPT, "custom", "title", "\n")
        with self.assertRaisesRegex(GfxError, "featured title"):
            patch_featured_actionscript(self.SCRIPT, "custom", "all", "GOLIATH")
        with self.assertRaisesRegex(GfxError, "valid for"):
            patch_featured_actionscript(self.SCRIPT, "hide", "title", "text")
        with self.assertRaisesRegex(GfxError, "already"):
            patch_featured_actionscript(
                patch_featured_actionscript(self.SCRIPT, "hide"), "hide")
        with self.assertRaisesRegex(GfxError, "one setFeatured"):
            patch_featured_actionscript("function other() {}", "hide")
        with self.assertRaisesRegex(GfxError, "one setFeatured"):
            patch_featured_actionscript(self.SCRIPT * 2, "hide")
        with self.assertRaisesRegex(GfxError, "early return"):
            patch_featured_actionscript(
                "function setFeatured() { if (x) return; }", "hide")
        with self.assertRaisesRegex(GfxError, "end of setFeatured"):
            patch_featured_actionscript(
                "function setFeatured() {", "hide")

    def test_stages_private_movie_with_ffdec_and_undo(self):
        records = []
        modified = synthetic_gfx(
            width=80,
            action_message="setFeatured mcFeatureTitle mcFeatureBuy mcFeatureTry")
        def mock_ffdec(args, *, configured, folder, cancelled=None):
            records.append(args[0])
            if args[0] == "-export":
                scripts = Path(args[2]) / "scripts"
                scripts.mkdir()
                (scripts / "frame_1.as").write_text(self.SCRIPT)
            elif args[0] == "-importScript":
                path = Path(args[-1]) / "scripts/frame_1.as"
                updated = path.read_text()
                self.assertIn("mcFeatureTitle._visible = false", updated)
                Path(args[2]).write_bytes(modified)
            else:
                self.fail("Unexpected FFDec invocation")
        with patch("gfx_featured_promo._ffdec", side_effect=mock_ffdec):
            changed = stage_featured_promo(
                self.ws, self.snapshot.session, "hide",
                target="title", configured="mock")
        self.assertEqual(records, ["-export", "-importScript"])
        self.assertTrue(changed.changed)
        self.assertEqual(changed.edits, 1)
        self.assertEqual(self.file.read_bytes(), self.movie)
        reverted = undo_gfx_session(self.ws, self.snapshot.session)
        self.assertFalse(reverted.changed)
        self.assertEqual(reverted.edits, 0)

    def test_missing_or_ambiguous_scripts_fail_without_project_or_session_write(self):
        def fake_ffdec(args, *, configured, folder, cancelled=None):
            if args[0] == "-export":
                scripts = Path(args[2])
                (scripts / "a.as").write_text("function someOther() {}")
        with patch("gfx_featured_promo._ffdec", side_effect=fake_ffdec):
            with self.assertRaisesRegex(GfxError, "identify"):
                stage_featured_promo(self.ws, self.snapshot.session, "remove")
        self.assertEqual(self.snapshot.working_file.read_bytes(), self.movie)
        self.assertEqual(self.file.read_bytes(), self.movie)

    def test_rejects_other_movie_and_missing_compiled_symbols(self):
        bad = synthetic_gfx(action_message="something_else")
        self.snapshot.working_file.write_bytes(bad)
        with self.assertRaisesRegex(GfxError, "lacks"):
            stage_featured_promo(self.ws, self.snapshot.session, "hide")
        self.assertEqual(self.file.read_bytes(), self.movie)


if __name__ == "__main__":
    unittest.main()
