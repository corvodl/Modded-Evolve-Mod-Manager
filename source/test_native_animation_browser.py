"""Qt animation browser: searchable, bounded, and correctly library-aware."""
from __future__ import annotations
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QLabel

from qt_gpu_viewer import QtGPUModelPanel
from character_animation_catalog import AnimationReference


class NativeAnimationSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        clips = [
            AnimationReference("run_idle", "characters/hunters/tech/run_idle.caf",
                               "caf", True),
            AnimationReference("weapon_fire", "characters/hunters/tech/weapon_fire.caf",
                               "caf", False, in_archive=True),
            AnimationReference("aimposes_forward", "characters/hunters/tech/aimposes_forward.caf",
                               "caf", True),
            AnimationReference("special_missing", "characters/hunters/tech/special_missing.caf",
                               "caf", False),
        ]
        clips.extend(
            AnimationReference("clip_%04d" % i,
                               "characters/hunters/tech/clip_%04d.caf" % i,
                               "caf", False)
            for i in range(210))
        p = self.panel = SimpleNamespace(
            native_clip_select=QComboBox(), native_search=QLineEdit(),
            native_state_filter=QComboBox(), native_motion_filter=QComboBox(),
            native_result_count=QLabel(), _native_references=tuple(clips),
            _native_suggested=0, _native_selection=Mock(),
            _animation_motion=QtGPUModelPanel._animation_motion,
        )
        p.native_state_filter.addItems(
            ["All availability", "Ready in library", "In PAK (extract on play)",
             "Not found", "Layered / additive", "Graph / DBA"])
        p.native_motion_filter.addItems(
            ["All motions", "Idle / Stand", "Move / Run", "Combat / Aim",
             "Actions / Other"])

    def _apply(self):
        QtGPUModelPanel._filter_native_catalog(self.panel)

    def test_large_catalog_limits_popup_and_searches_all_names(self):
        self._apply()
        self.assertLessEqual(self.panel.native_clip_select.count(), 91)
        self.assertIn("214 total", self.panel.native_result_count.text())
        self.panel.native_search.setText("clip_0209")
        self._apply()
        self.assertEqual(self.panel.native_clip_select.count(), 2)
        self.assertEqual(self.panel.native_clip_select.itemData(1), 213)

    def test_filter_by_library_presence_and_animation_type(self):
        p = self.panel
        p.native_state_filter.setCurrentIndex(1)
        self._apply()
        self.assertEqual(p.native_clip_select.itemData(1), 0)
        self.assertEqual(p.native_clip_select.count(), 2)
        p.native_state_filter.setCurrentIndex(2)
        self._apply()
        self.assertEqual(p.native_clip_select.itemData(1), 1)
        p.native_state_filter.setCurrentIndex(4)
        self._apply()
        self.assertEqual(p.native_clip_select.itemData(1), 2)
        p.native_state_filter.setCurrentIndex(0)
        p.native_motion_filter.setCurrentIndex(3)
        self._apply()
        self.assertTrue(any("weapon_fire" in p.native_clip_select.itemText(i)
                            for i in range(p.native_clip_select.count())))

    def test_playback_selection_is_reference_id_not_filtered_row(self):
        p = self.panel
        p.native_search.setText("clip_0199")
        self._apply()
        self.assertEqual(p.native_clip_select.itemData(1), 203)
        p.native_clip_select.setCurrentIndex(1)
        p.native_search.setText("")
        self._apply()
        self.assertEqual(p.native_clip_select.currentData(), 203)
        self.assertEqual(QtGPUModelPanel._animation_motion("stand_idle"), 1)


if __name__ == "__main__":
    unittest.main()
