"""GFX dependency lookup is scoped to a single build and real movie types."""
from __future__ import annotations
import unittest

from gfx_preview_backend import GfxError, resolve_imports
from gfx_dependency_resolver import choose_movie_provider
from gfx_synthetic_fixtures import synthetic_gfx


class GfxDependencyFallbackTests(unittest.TestCase):
    def test_flashassets_subfolder_finds_requested_movie(self):
        hits = [
            ("Libs/UI/FlashAssets/effectsModeSelect.swf", "correct Stage 2 SWF"),
            ("Libs/UI/FlashAssets/images/effectsModeSelect.swf.dds.0", "not a movie"),
            ("Game/Other/effectsModeSelect.swf", "other game folder"),
        ]
        picked, origin = choose_movie_provider(
            "Libs/UI/effectsModeSelect.swf", "Libs/UI/main_menu.gfx", hits)
        self.assertEqual(picked, "correct Stage 2 SWF")
        self.assertEqual(origin, "same-UI-tree:Libs/UI/FlashAssets/effectsModeSelect.swf")

    def test_exact_movie_wins_before_nested_and_casefolds(self):
        hits = [
            ("Libs/UI/FlashAssets/effectsModeSelect.swf", "nested"),
            ("LIBS/UI/EFFECTSMODESELECT.SWF", "exact"),
        ]
        self.assertEqual(
            choose_movie_provider(
                "Libs/UI/effectsModeSelect.swf", "Libs/UI/main_menu.gfx", hits)[0],
            "exact")

    def test_ambiguous_references_do_not_select_arbitrary_fonts(self):
        hits = [
            ("Libs/UI/FlashAssets/font.gfx", "A"),
            ("Libs/UI/Fonts/font.gfx", "B"),
        ]
        picked, reason = choose_movie_provider(
            "Libs/UI/font.gfx", "Libs/UI/main_menu.gfx", hits)
        self.assertIsNone(picked)
        self.assertIn("ambiguous", reason)
        hits += [("Libs/UI/font.gfx", "one"), ("libs/ui/FONT.GFX", "two")]
        picked, reason = choose_movie_provider(
            "Libs/UI/font.gfx", "Libs/UI/main_menu.gfx", hits)
        self.assertIsNone(picked)
        self.assertIn("ambiguous exact", reason)

    def test_no_cross_folder_substitution_or_dds_as_swf(self):
        hits = [
            ("characters/monsters/FlashAssets/focus.swf", "different asset"),
            ("Libs/UI/FlashAssets/focus.swf.dds.0", "split DDS not a SWF"),
        ]
        picked, reason = choose_movie_provider(
            "Libs/UI/focus.swf", "Libs/UI/main_menu.gfx", hits)
        self.assertIsNone(picked)
        self.assertIn("missing", reason)
        with self.assertRaises(GfxError):
            choose_movie_provider(
                "Game/Levels/focus.swf", "Libs/UI/main_menu.gfx", hits)

    def test_resolve_imports_preserves_requested_virtual_filename(self):
        import types
        source = types.SimpleNamespace(imports=("effectsModeSelect.swf",))
        converted_swf = b"FWS" + synthetic_gfx()[3:]
        candidates = [("Libs/UI/FlashAssets/effectsModeSelect.swf", converted_swf)]
        def lookup(wanted):
            item, _ = choose_movie_provider(wanted, "Libs/UI/main_menu.gfx",
                                            candidates)
            return item
        resolved, errors = resolve_imports(source, "Libs/UI/main_menu.gfx", lookup)
        self.assertEqual(resolved,
                         {"Libs/UI/effectsModeSelect.swf": converted_swf})
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
