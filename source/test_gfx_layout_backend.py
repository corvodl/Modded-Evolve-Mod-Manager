"""Synthetic static placement editing; no original Evolve game assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from gfx_preview_backend import GfxError
from gfx_synthetic_fixtures import synthetic_gfx
from gfx_layout_backend import static_elements, edit_first_frame, stage_static_layout
from gfx_edit_backend import create_gfx_session, undo_gfx_session
from gfx_featured_promo import patch_featured_actionscript


class GfxLayoutBackendTests(unittest.TestCase):
    def test_placement_bounds_translation_scale_rotation_and_hide(self):
        original = synthetic_gfx(sprites=False)
        items = static_elements(original)
        self.assertEqual(len(items), 1)
        item, = items
        self.assertEqual(item.character, 1)
        self.assertEqual(item.depth, 1)
        self.assertEqual(item.rect, (0, 64, 0, 48))
        self.assertTrue(item.editable)
        self.assertEqual((item.x, item.y), (0, 0))
        changed = edit_first_frame(
            original, depth=1, character=1, x=24, y=19,
            scale_x=1.5, scale_y=.75, rotation=30)
        output, = static_elements(changed)
        self.assertAlmostEqual(output.x, 24, delta=.08)
        self.assertAlmostEqual(output.y, 19, delta=.08)
        self.assertAlmostEqual(output.scale_x, 1.5, delta=.005)
        self.assertAlmostEqual(output.scale_y, .75, delta=.005)
        self.assertAlmostEqual(output.rotation, 30, delta=.1)
        self.assertEqual(len(static_elements(
            edit_first_frame(original, depth=1, character=1,
                             x=0, y=0, visible=False))), 0)
        self.assertEqual(original, synthetic_gfx(sprites=False))

    def test_nested_sprite_is_numeric_only_and_does_not_guess_bounds(self):
        source = synthetic_gfx(sprites=True)
        items = static_elements(source)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].rect)
        self.assertTrue(items[0].editable)
        self.assertIn("numeric", items[0].reason.lower())
        changed = edit_first_frame(
            source, depth=1, character=2, x=120, y=300)
        self.assertEqual(round(static_elements(changed)[0].x), 120)

    def test_compressed_movie_and_safety_guards(self):
        source = synthetic_gfx(sprites=False, compressed=True)
        changed = edit_first_frame(
            source, depth=1, character=1, x=50, y=70)
        self.assertAlmostEqual(static_elements(changed)[0].x, 50)
        with self.assertRaisesRegex(GfxError, "missing|unsafe"):
            edit_first_frame(source, depth=1, character=999, x=0, y=0)
        with self.assertRaisesRegex(GfxError, "safe limits"):
            edit_first_frame(source, depth=1, character=1,
                             x=250000, y=0)
        with self.assertRaisesRegex(GfxError, "finite"):
            edit_first_frame(source, depth=1, character=1,
                             x=float("nan"), y=0)

    def test_stage_and_undo_leave_workspace_game_source_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Project"
            file = project / "files/Libs/UI/main_menu.gfx"
            file.parent.mkdir(parents=True)
            source = synthetic_gfx(sprites=False)
            file.write_bytes(source)
            (project / ".evolve-pak-workspace.json").write_text(
                json.dumps({"entries": [{"path": "Libs/UI/main_menu.gfx",
                                        "sha256": hashlib.sha256(source).hexdigest()}]}))
            session = create_gfx_session(project, "Libs/UI/main_menu.gfx")
            edited = stage_static_layout(
                project, session.session, depth=1, character=1,
                x=60, y=80, scale_x=1.25, scale_y=1.,
                rotation=0, visible=True)
            self.assertTrue(edited.changed)
            self.assertEqual(file.read_bytes(), source)
            self.assertEqual(static_elements(edited.working_file.read_bytes())[0].x, 60)
            undone = undo_gfx_session(project, session.session)
            self.assertFalse(undone.changed)
            self.assertEqual(undone.working_file.read_bytes(), source)

    def test_runtime_promo_override_targets_supported_script_only(self):
        source = "function setFeatured(info) { _root.mcFeatureTitle.text = info.name; }"
        changed = patch_featured_actionscript(
            source, "layout", "try",
            layout=(120, 240, 1.5, .75, 35, False))
        self.assertIn("mcFeatureTry._x = 120.0000", changed)
        self.assertIn("mcFeatureButtonTry._yscale = 75.0000", changed)
        self.assertIn("mcFeatureTry._visible = false", changed)
        self.assertNotIn("mcFeatureBuy._x", changed)
        with self.assertRaisesRegex(GfxError, "Layout"):
            patch_featured_actionscript(source, "layout", "all",
                                       layout=(1, 2, 1, 1, 0, True))
        with self.assertRaisesRegex(GfxError, "supported bounds"):
            patch_featured_actionscript(source, "layout", "title",
                                       layout=(float("inf"), 0, 1, 1, 0, True))
        with self.assertRaisesRegex(GfxError, "already"):
            patch_featured_actionscript(changed, "layout", "try",
                                       layout=(0, 0, 1, 1, 0, True))


if __name__ == "__main__":
    unittest.main()
