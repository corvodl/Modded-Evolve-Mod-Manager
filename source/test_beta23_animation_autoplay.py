"""3P DBA default, CAF rig coverage, and modern animation popover regressions.

Uses synthetic references and meshes. No Evolve game assets are committed.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch, Mock
import unittest
import zlib

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from character_animation_catalog import (
    AnimationReference, preferred_third_person_dba)
from material_preview import PreviewMaterials
from model_preview import read_preview_mesh
from native_caf import append_caf_animations, minimum_matching_controllers
from model_blender_export import glb_bytes
from model_rigging import skin_glb
from qt_gpu_viewer import QtGPUModelPanel
from test_material_preview import uv_mesh
from test_model_rigging import tiny_mesh, tiny_skeleton


class AutoDbTests(unittest.TestCase):
    def test_exact_character_third_person_database_over_unrelated(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/'goliath_3p.dba'
            file.write_bytes(b'fixture')
            refs=(
                AnimationReference('DBA: wraith_3p',
                    'characters/monsters/wraith/animations/wraith_3p.dba',
                    'dba',True,file),
                AnimationReference('DBA: goliath_3p',
                    'characters/monsters/goliath/animations/goliath_3p.dba',
                    'dba',True,file),
            )
            chosen=preferred_third_person_dba(
                refs,'characters/monsters/goliath/goliath_skeleton.chr')
            self.assertEqual(chosen,refs[1])
            self.assertIsNone(preferred_third_person_dba(
                refs,'characters/monsters/unknown/unknown_skeleton.chr'))

    def test_unavailable_database_never_guessed(self):
        refs=(AnimationReference('DBA: goliath_3p',
            'characters/monsters/goliath/animations/goliath_3p.dba',
            'dba',False),)
        self.assertIsNone(preferred_third_person_dba(
            refs,'characters/monsters/goliath/goliath.chr'))

    def test_complex_rig_rejects_animation_with_three_matching_bones(self):
        from native_caf import append_caf_animations
        raw=tiny_mesh()
        rigged,_=skin_glb(glb_bytes(read_preview_mesh(raw)),raw,tiny_skeleton())
        root=zlib.crc32(b'root')
        motion=SimpleNamespace(
            tracks=tuple(SimpleNamespace(controller_id=x) for x in
                         (root,zlib.crc32(b'child'))+tuple(range(20,30))),
            additive=False)
        self.assertEqual(minimum_matching_controllers(len(motion.tracks)),10)
        with patch('native_caf.decode_caf',return_value=motion):
            with self.assertRaisesRegex(ValueError,'skeleton mismatch'):
                append_caf_animations(rigged,[('goliath_wrong_stage',b'fixture')])

    def test_small_verified_caf_remains_supported(self):
        self.assertEqual(minimum_matching_controllers(1),1)
        self.assertEqual(minimum_matching_controllers(2),2)
        self.assertEqual(minimum_matching_controllers(10),8)


class PanelDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel=QtGPUModelPanel(None,read_preview_mesh(uv_mesh()),compact=True)
        self.addCleanup(self.panel.close)

    def test_renamed_play_and_compact_sections(self):
        self.assertEqual(set(self.panel.animation_sections),
                         {'Animations','Playback','Playlists & markers'})
        self.assertEqual(self.panel.play_native_button.text(),'▶  Play')
        self.assertLessEqual(self.panel.native_clip_select.maximumHeight(),160)
        self.assertIsNotNone(self.panel.studio_fill_slider)

    def test_verified_dba_auto_play_on_model_open(self):
        with tempfile.TemporaryDirectory() as folder:
            asset=Path(folder)/'goliath_3p.dba'
            asset.write_bytes(b'fixture')
            ref=AnimationReference(
                'DBA: goliath_3p',
                'characters/monsters/goliath/animations/goliath_3p.dba',
                'dba',True,asset)
            with patch.object(self.panel,'load_native',autospec=True) as play:
                self.panel.set_native_catalog((ref,),loader=Mock(),
                    character='characters/monsters/goliath/goliath_skeleton.chr')
                self.assertEqual(self.panel.native_clip_select.currentData(),0)
                deadline=time.monotonic()+1.0
                while not play.called and time.monotonic()<deadline:
                    self.qt.processEvents()
                    time.sleep(.015)
                self.assertTrue(play.called)
                self.assertTrue(self.panel._auto_dba_loading)

    def test_wrong_reference_cannot_start_old_default(self):
        with tempfile.TemporaryDirectory() as folder:
            asset=Path(folder)/'goliath_3p.dba'
            asset.write_bytes(b'fixture')
            ref=AnimationReference('DBA: goliath_3p',
                'characters/monsters/goliath/animations/goliath_3p.dba',
                'dba',True,asset)
            with patch.object(self.panel,'load_native') as play:
                self.panel.set_native_catalog((ref,),loader=Mock(),
                    character='characters/monsters/goliath/goliath.chr')
                self.panel._native_selection(0)  # user changed selection
                deadline=time.monotonic()+0.2
                while time.monotonic()<deadline:
                    self.qt.processEvents()
                    time.sleep(.015)
                play.assert_not_called()


if __name__=='__main__':
    unittest.main()
