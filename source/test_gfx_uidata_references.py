"""UIData XML/GFX reference tests with synthetic Stage 2 setup, no game assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from asset_library import _invalidate
from build_collections import register_build, assign_archive
from asset_library_preview import library_gfx_ui_data
from gfx_uidata_references import (
    parse_ui_data_references, is_ui_data_archive, UIDataError,
)

MAIN_XML = b"""<?xml version="1.0"?>
<UIElements name="MainMenu"><UIElement name="MainMenuNew">
<GFx file="main_menu.gfx" layer="3"><Constraints>
<Align mode="dynamic" halign="center" valign="center"/>
</Constraints></GFx>
<functions>
  <function name="OpenScreen" funcname="openScreen">
  <param name="showNews" desc="show news feed"/></function>
  <function name="UpdateNews" funcname="updateNews"/>
</functions>
<arrays><array name="menuButtons_names" varname="menuButtons_names"/></arrays>
</UIElement></UIElements>"""
TEXTURE_XML = b"""<Preload><InGame><Merc>
<Preload file="Libs/UI/merchud2_*"/>
</Merc></InGame><NoLevel>
<Preload file="Libs/UI/effects*"/>
<Preload file="Libs/UI/FlashAssets/images/LoadingScreen/default_loading.dds"/>
</NoLevel></Preload>"""
FONT_XML = b"""<root><FontMappings>
<language id="JPN-JA" fontSourceFile="gfxfontlib_locfont.gfx">
<font originalName="Times New Roman" mappedName="EvolveJP"/></language>
</FontMappings></root>"""
NEWS_XML = b"""<MenuNews><News text="Sample news" img="Libs/UI/FlashAssets/images/News/news_img1.dds"/>
</MenuNews>"""
CINEMATICS = b"""<ScreenCinematics><MainMenu><Main>
<Button command="OpenChallenges" display_name="@ui_ChallengesMainMenuButton" cinematic_event="open_challenges"/>
</Main></MainMenu></ScreenCinematics>"""
DOCS = {
    "UI/UIElements/MainMenu.xml": MAIN_XML,
    "UI/TexturePreload.xml": TEXTURE_XML,
    "UI/FontMappings.xml": FONT_XML,
    "UI/MainMenuNews.xml": NEWS_XML,
    "UI/MainMenuScreen.xml": CINEMATICS,
}


class UIDataPureTests(unittest.TestCase):
    def test_main_menu_bindings_callbacks_preload_texture_and_news(self):
        result = parse_ui_data_references(
            "Libs/UI/main_menu.gfx", DOCS, archive="Game/UI_Data.pak")
        self.assertEqual(result["status"], "ui-data-bindings-found")
        self.assertEqual(result["source_archive"], "Game/UI_Data.pak")
        self.assertEqual(len(result["screens"]), 1)
        menu = result["screens"][0]
        self.assertEqual(menu["ui_element"], "MainMenuNew")
        self.assertEqual(menu["layer"], "3")
        self.assertEqual(menu["align"]["halign"], "center")
        self.assertEqual(menu["callbacks"][0],
                         {"name": "openScreen", "params": ["showNews"]})
        self.assertEqual(result["arrays"][0]["name"], "menuButtons_names")
        self.assertEqual(len(result["texture_preloads"]), 3)
        self.assertTrue(any(
            a["path"] == "Libs/UI/FlashAssets/images/LoadingScreen/default_loading.dds"
            for a in result["texture_preloads"]))
        self.assertEqual(result["named_texture_references"][0]["path"],
                         "Libs/UI/FlashAssets/images/News/news_img1.dds")
        self.assertEqual(result["font_sources"][0]["movie"],
                         "gfxfontlib_locfont.gfx")
        self.assertEqual(result["menu_commands"][0]["command"], "OpenChallenges")
        self.assertFalse(result["atlas_uv_bindings"])
        self.assertFalse(result["runtime_callbacks_executed"])

    def test_ui_data_does_not_guess_sprites_or_match_other_movie(self):
        result = parse_ui_data_references("Libs/UI/another_menu.gfx", DOCS)
        self.assertEqual(result["status"], "no-ui-data-screen-match")
        self.assertEqual(result["screens"], [])
        self.assertEqual(result["named_texture_references"], [])
        other = parse_ui_data_references(
            "Characters/Monsters/main_menu.gfx", DOCS)
        self.assertEqual(other["screens"], [])

    def test_safe_xml_bound_and_archive_alias(self):
        for value in ("Game/UIData.pak", "Game/UI_Data.pak",
                      "Game/UI-Data.pak"):
            self.assertTrue(is_ui_data_archive(value))
        self.assertFalse(is_ui_data_archive("Game/UI_Assets.pak"))
        with self.assertRaisesRegex(UIDataError, "Unsafe"):
            parse_ui_data_references("Libs/UI/main_menu.gfx", {
                "UI/UIElements/MainMenu.xml": b'<!DOCTYPE x [ <!ENTITY leak SYSTEM "file:///etc/hosts"> ]><x>&leak;</x>'
            })
        with self.assertRaisesRegex(UIDataError, "Too many"):
            parse_ui_data_references("Libs/UI/main_menu.gfx", {
                f"UI/UIElements/Extra{i}.xml": b"<UIElements/>"
                for i in range(255)
            })


class UIDataLibraryScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "AssetLibrary"
        self.root.mkdir()
        self.stage2 = register_build("Stage 2", "stage2", root=self.root)
        self.legacy = register_build("Legacy", "legacy", root=self.root)
        self.movie_id = "a" * 20
        self._archive(self.stage2, self.movie_id,
                      "Game/UI_Assets.pak",
                      {"Libs/UI/main_menu.gfx": b"synthetic movie"})
        self.addCleanup(_invalidate)

    def _archive(self, build, aid, relative, files):
        folder = self.root / aid
        for name, data in files.items():
            file = folder / "files" / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        manifest = {
            "version": 1, "name": Path(relative).name,
            "source": "/sample/" + relative,
            "source_sha256": hashlib.sha256(aid.encode()).hexdigest(),
            "entries": [
                {"path": k, "bytes": len(v),
                 "sha256": hashlib.sha256(v).hexdigest()}
                for k, v in files.items()],
        }
        (folder / "asset-library.json").write_text(json.dumps(manifest))
        assign_archive(build, aid, relative_path=relative, root=self.root)
        _invalidate()

    def test_no_cross_build_uidata_fallback(self):
        self._archive(self.legacy, "b" * 20, "Game/UIData.pak", DOCS)
        result = library_gfx_ui_data(
            self.movie_id, "Libs/UI/main_menu.gfx", root=self.root,
            build_id=self.stage2)
        self.assertEqual(result["status"], "ui-data-not-imported")
        self.assertFalse(result["screens"])

    def test_matching_build_uses_only_registered_ui_data_archive(self):
        self._archive(self.stage2, "c" * 20, "Game/UI_Data.pak", DOCS)
        result = library_gfx_ui_data(
            self.movie_id, "Libs/UI/main_menu.gfx",
            root=self.root, build_id=self.stage2)
        self.assertEqual(result["status"], "ui-data-bindings-found")
        self.assertEqual(result["source_archive_id"], "c" * 20)
        self.assertEqual(result["source_build_id"], self.stage2)
        self.assertEqual(result["screens"][0]["ui_element"], "MainMenuNew")
        self.assertEqual(result["named_texture_references"][0]["relation"],
                         "menu-news-image-not-direct-GFX-sprite-UV")

    def test_ambiguous_uidata_snapshot_is_reported(self):
        self._archive(self.stage2, "c" * 20, "Game/UIData.pak", DOCS)
        self._archive(self.stage2, "d" * 20, "Game/UI_Data.pak", DOCS)
        result = library_gfx_ui_data(
            self.movie_id, "Libs/UI/main_menu.gfx",
            root=self.root, build_id=self.stage2)
        self.assertEqual(result["status"], "ambiguous-ui-data-archives")
        self.assertFalse(result["screens"])


if __name__ == "__main__":
    unittest.main()
