"""Regression: animation popover remains scrollable at small GPU viewport sizes.

No real game files or OpenGL GPU context are required for the layout check.
"""
from __future__ import annotations
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QScrollArea
from material_preview import PreviewMaterials
from model_preview import read_preview_mesh
from test_material_preview import uv_mesh
from qt_gpu_viewer import QtGPUModelPanel


class AnimationPopoverLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        mesh = read_preview_mesh(uv_mesh(), 'synthetic.skinm')
        self.panel = QtGPUModelPanel(None, mesh, compact=True)
        self.addCleanup(self.panel.close)

    def test_all_expanded_sections_scroll_within_small_window(self):
        self.panel.resize(420, 375)
        self.panel.show()
        self.app.processEvents()
        self.panel.toggle_overlay('Animation')
        for heading, _ in self.panel.animation_sections.values():
            heading.setChecked(True)
        self.panel.layer_row_widget.setVisible(True)
        self.panel._update_animation_scrolling()
        self.app.processEvents()
        overlay = self.panel._overlay
        self.assertIsInstance(overlay, QScrollArea)
        self.assertEqual(overlay.verticalScrollBarPolicy(), Qt.ScrollBarAsNeeded)
        self.assertEqual(overlay.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertLessEqual(overlay.height(), self.panel.canvas.height())
        self.assertLessEqual(overlay.width(), self.panel.canvas.width())
        bar = overlay.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0, 'expanded sections must scroll instead of clipping')
        bar.setValue(bar.maximum())
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())
        self.assertTrue(self.panel.animation_sections['Playlists & markers'][1].isVisible())

    def test_collapsible_panels_update_scroll_range(self):
        self.panel.resize(450, 440)
        self.panel.show()
        self.app.processEvents()
        self.panel.toggle_overlay('Animation')
        self.app.processEvents()
        for heading, _ in self.panel.animation_sections.values():
            heading.setChecked(True)
        self.panel._update_animation_scrolling()
        self.app.processEvents()
        fully_open = self.panel._overlay.verticalScrollBar().maximum()
        self.assertGreater(fully_open, 0)
        for title in ('Playback', 'Playlists & markers'):
            self.panel.animation_sections[title][0].setChecked(False)
        self.panel._update_animation_scrolling()
        self.app.processEvents()
        collapsed = self.panel._overlay.verticalScrollBar().maximum()
        self.assertLess(collapsed, fully_open)
        self.assertTrue(self.panel.animation_sections['Animations'][1].isVisible())


if __name__ == '__main__':
    unittest.main()
