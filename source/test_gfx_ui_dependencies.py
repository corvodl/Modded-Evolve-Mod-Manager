"""Synthetic same-build UI_Data references and GFX texture companions."""
import unittest

from gfx_ui_dependencies import related_ui_textures


class GfxUiDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.paths = (
            "Libs/UI/main_menu_I108.dds.0",
            "Libs/UI/effectsModeSelect_I100.dds.0",
            "Libs/UI/effectsMainMenuFocus_I115.dds.0",
            "Libs/UI/FlashAssets/images/News/news_img1.dds",
            "Libs/UI/FlashAssets/images/LoadingScreen/default_loading.dds",
            "Libs/UI/other_I10.dds.0",
            "UI/UIElements/MainMenu.xml",
            "UI/UIElements/Other.xml",
            "UI/TexturePreload.xml",
            "UI/MainMenuNews.xml",
        )
        self.xml = {
            "UI/UIElements/MainMenu.xml": (
                b'<UIElements><UIElement><GFx file="main_menu.gfx" />'
                b'<Texture image="Libs/UI/main_menu_I108.dds.0" />'
                b'</UIElement></UIElements>'
            ),
            "UI/UIElements/Other.xml": (
                b'<UIElements><GFx file="other.gfx" />'
                b'<Image file="Libs/UI/other_I10.dds.0" /></UIElements>'
            ),
            "UI/TexturePreload.xml": (
                b'<Preload><NoLevel>'
                b'<Preload file="Libs/UI/effects*" />'
                b'<Preload file="Libs/UI/FlashAssets/images/LoadingScreen/default_loading.dds" />'
                b'</NoLevel><InGame><Common>'
                b'<Preload file="Libs/UI/other_I10.dds.0" />'
                b'</Common></InGame></Preload>'
            ),
            "UI/MainMenuNews.xml": (
                b'<MenuNews><News img="Libs/UI/FlashAssets/images/News/news_img1.dds" />'
                b'</MenuNews>'
            ),
        }

    def lookup(self, name):
        return {k.casefold(): v for k, v in self.xml.items()}.get(name.casefold())

    def test_compiled_import_images_and_verified_ui_data_preloads(self):
        result = dict(related_ui_textures(
            "Libs/UI/main_menu.gfx", self.paths, self.lookup,
            imports=("effectsModeSelect.swf",)))
        self.assertEqual(result["Libs/UI/main_menu_I108.dds.0"], "Compiled movie image")
        self.assertEqual(result["Libs/UI/effectsModeSelect_I100.dds.0"],
                         "Imported movie image")
        self.assertEqual(result["Libs/UI/effectsMainMenuFocus_I115.dds.0"],
                         "UI_Data preload")
        self.assertEqual(result["Libs/UI/FlashAssets/images/News/news_img1.dds"],
                         "UI_Data news")
        self.assertIn("Libs/UI/FlashAssets/images/LoadingScreen/default_loading.dds",
                      result)
        self.assertNotIn("Libs/UI/other_I10.dds.0", result)

    def test_unrelated_or_foreign_build_xml_does_not_link_assets(self):
        # The resolver sees only matching-build documents; an unrelated
        # UIElement isn't enough to attach another movie's images.
        files = self.paths + ("Libs/UI/not_for_this_movie.dds",)
        result = dict(related_ui_textures("Libs/UI/blank.gfx", files, self.lookup))
        self.assertNotIn("Libs/UI/not_for_this_movie.dds", result)
        # In-game global preloads are documented context, not a movie image.
        self.assertEqual(result.get("Libs/UI/other_I10.dds.0"), "UI_Data preload")

    def test_movie_embedded_tif_and_dds_paths_resolve_cooked_assets(self):
        from gfx_synthetic_fixtures import synthetic_gfx
        paths = self.paths + ("Libs/UI/main_menu_I9.dds.0",
                              "Libs/UI/FlashAssets/images/News/news_img2.dds")
        movie = synthetic_gfx(action_message="main_menu_I9.tif")
        result = dict(related_ui_textures(
            "Libs/UI/main_menu.gfx", paths, self.lookup, movie_bytes=movie))
        self.assertEqual(result["Libs/UI/main_menu_I9.dds.0"],
                         "GFX resource reference")
        self.assertNotIn("Libs/UI/FlashAssets/images/News/news_img2.dds", result)
        direct = synthetic_gfx(
            action_message="Libs/UI/FlashAssets/images/News/news_img2.dds")
        result2 = dict(related_ui_textures(
            "Libs/UI/main_menu.gfx", paths, self.lookup, movie_bytes=direct))
        self.assertEqual(
            result2["Libs/UI/FlashAssets/images/News/news_img2.dds"],
            "GFX resource reference")

    def test_malformed_ui_data_is_ignored_and_images_never_loaded_eagerly(self):
        calls = []
        def resolver(path):
            calls.append(path)
            return b'<UIElements><broken' if path.endswith("MainMenu.xml") else None
        out = related_ui_textures(
            "Libs/UI/main_menu.gfx", self.paths, resolver,
            imports=("../outside.swf",))
        self.assertIn(("Libs/UI/main_menu_I108.dds.0", "Compiled movie image"), out)
        self.assertFalse(any(path.casefold().endswith(".dds.0") for path in calls))


if __name__ == "__main__":
    unittest.main()
