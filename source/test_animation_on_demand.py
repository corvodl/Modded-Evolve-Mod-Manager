"""Verify exact-path CAF extraction happens at Play, never at discovery."""
from __future__ import annotations
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import animation_on_demand as demand
from character_animation_catalog import (
    AnimationReference, caf_header, parse_animation_references,
    native_clip_playability, discover_animations,
)
from shared_pak_cache import SharedCatalog
from test_shared_pak_cache import SharedPAKCacheTests

HEADER = b"CrChF\x07\x00\x00" + struct.pack("<II", 1, 16) + b"\0" * 16
VIRTUAL = "characters/hunters/merc_tech/animations/3p/tech_nw_alert_stand_idle.caf"
ANIM_PAK = "AnimationsCAF.pak"


class OnDemandCAFs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.game = self.root / "EvolveGame"
        (self.game / "bin64_SteamRetail").mkdir(parents=True)
        (self.game / "bin64_SteamRetail" / "Evolve.exe").write_bytes(b"test")
        self.source = self.game / "Game" / ANIM_PAK
        self.source.parent.mkdir()
        self.source.write_bytes(b"mock signed CryPAK")
        self.key = self.root / "RSAKeyData.bin"
        self.key.write_bytes(b"x" * 140)
        self.info = {
            "entries": {VIRTUAL: {
                "method": 13, "extra_length": 0,
                "original_size": len(HEADER),
                "compressed_size": len(HEADER)}}}
        demand._CATALOGS.clear()

    def test_discovery_does_not_decrypt_unextracted_caf(self):
        ref = AnimationReference("stand_idle", VIRTUAL, "caf", False,
                                 in_archive=True)
        with patch("evolve_gameplay_editor.decrypt_entry") as decrypt:
            xml = ('<Params><AnimationList>'
                   '<Animation name="#filepath" '
                   'path="characters/hunters/merc_tech/animations/3p"/>'
                   '<Animation name="stand_idle" path="tech_nw_alert_stand_idle.caf"/>'
                   '</AnimationList></Params>').encode()
            refs = parse_animation_references(
                xml, lambda path: None,
                can_extract=lambda path: path.casefold() == VIRTUAL.casefold())
            decrypt.assert_not_called()
        self.assertEqual(refs[0].in_archive, True)
        self.assertEqual(native_clip_playability(refs[0])[0], "extractable")
        self.assertEqual(native_clip_playability(ref)[0], "extractable")

    def test_exact_missing_caf_extracted_from_verified_original_game(self):
        ref = AnimationReference("stand_idle", VIRTUAL, "caf", False)
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", ANIM_PAK)) as verified, \
             patch("evolve_gameplay_editor.decrypt_entry",
                   return_value=HEADER) as decrypt:
            resolved, extracted, origin = demand.resolve_caf_for_play(
                ref, stage_root=self.stage, game_root=self.game)
        self.assertTrue(extracted)
        self.assertEqual(origin, "original game PAK")
        self.assertEqual(verified.call_args.args[1], self.key)
        self.assertEqual(decrypt.call_count, 1)
        self.assertTrue(resolved.available)
        self.assertTrue(caf_header(resolved.file))
        self.assertTrue(resolved.file.resolve().is_relative_to(self.root))
        self.assertFalse(resolved.file.is_relative_to(self.game))
        self.assertEqual(self.source.read_bytes(), b"mock signed CryPAK")

    def test_second_play_reuses_cached_caf_without_decryption(self):
        ref = AnimationReference("stand_idle", VIRTUAL, "caf", False)
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", ANIM_PAK)), \
             patch("evolve_gameplay_editor.decrypt_entry",
                   return_value=HEADER):
            resolved, *_ = demand.resolve_caf_for_play(
                ref, stage_root=self.stage, game_root=self.game)
        with patch("evolve_gameplay_editor.decrypt_entry") as decrypt, \
             patch("evolve_pak_rekey.extract_plain_cdr") as cdr:
            again, fresh, origin = demand.resolve_caf_for_play(
                resolved, stage_root=self.stage, game_root=self.game)
        self.assertFalse(fresh)
        self.assertEqual(again.file, resolved.file)
        decrypt.assert_not_called()
        cdr.assert_not_called()

    def test_missing_caf_is_not_silently_substituted(self):
        other = AnimationReference("not_present",
                                   VIRTUAL.replace("stand_idle", "not_real"),
                                   "caf", False)
        with patch("evolve_pak_rekey.extract_plain_cdr",
                   return_value=(self.info, b"", b"", ANIM_PAK)), \
             patch("evolve_gameplay_editor.decrypt_entry") as decrypt:
            with self.assertRaisesRegex(FileNotFoundError, "not found"):
                demand.resolve_caf_for_play(
                    other, stage_root=self.stage, game_root=self.game)
            decrypt.assert_not_called()

    def test_additive_aimposes_remain_layered_if_not_extracted(self):
        reference = AnimationReference("tech_nw_alert_stand_aimposes_forward",
                                       VIRTUAL, "caf", False, in_archive=True)
        state, _ = native_clip_playability(reference)
        self.assertEqual(state, "layered")


if __name__ == "__main__":
    unittest.main()
