"""Offscreen tests for Blender-inspired Qt model viewport and loading feedback.

Exercises widget state without OpenGL hardware or proprietary Evolve assets.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QToolButton
from character_animation_catalog import AnimationReference
from model_preview import read_preview_mesh
from qt_gpu_viewer import QtGPUModelPanel
from test_material_preview import uv_mesh


class Pointer:
    def __init__(self, x, y, *, button=Qt.LeftButton, buttons=Qt.LeftButton):
        self.point = QPointF(x, y)
        self._button = button
        self._buttons = buttons
    def position(self):
        return self.point
    def button(self):
        return self._button
    def buttons(self):
        return self._buttons
    def accept(self):
        pass


class BlenderViewportUiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        mesh = read_preview_mesh(uv_mesh(), 'tech_3p.skinm')
        self.panel = QtGPUModelPanel(None, mesh, compact=True)
        self.addCleanup(self.panel.close)

    def test_viewport_uses_pictograms_and_replaces_camera_buttons_with_gizmo(self):
        panel = self.panel
        self.assertEqual(panel.canvas.shading, 'smooth')
        for mode in ('Orbit', 'Pan', 'Zoom', 'Frame'):
            button = panel._tool_rail.findChild(QToolButton, 'modelTool'+mode)
            self.assertIsNotNone(button)
            self.assertFalse(button.icon().isNull(), mode)
            self.assertTrue(button.toolTip())
            self.assertTrue(button.accessibleName())
        gizmo = panel._axis_widget
        self.assertEqual(gizmo.objectName(), 'modelOrientationGizmo')
        self.assertEqual(gizmo.parent(), panel.canvas)
        self.assertEqual(len(gizmo._axis_points()), 6)

    def test_xyz_drag_orbits_and_axis_click_snaps_camera(self):
        panel = self.panel
        gizmo = panel._axis_widget
        canvas = panel.canvas
        old = tuple(canvas.rotation)
        gizmo.mousePressEvent(Pointer(55, 55))
        gizmo.mouseMoveEvent(Pointer(84, 45))
        gizmo.mouseReleaseEvent(Pointer(84, 45))
        self.assertNotEqual(tuple(canvas.rotation), old)
        # Two signed endpoints per axis. Choose the positive Z dot and snap.
        z = next(row for row in gizmo._axis_points() if row[4] == 'Top')
        point = z[3]
        gizmo.mousePressEvent(Pointer(point.x(), point.y()))
        gizmo.mouseReleaseEvent(Pointer(point.x(), point.y()))
        self.assertEqual(canvas.rotation, [0., 90.])

    def test_slider_percentages_track_values_without_modifying_source_images(self):
        panel = self.panel
        for slider, number, updated in (
            (panel.lighting_slider, panel.lighting_percent, 140),
            (panel.glow_slider, panel.glow_percent, 215),
            (panel.gloss_slider, panel.gloss_percent, 72),
            (panel.studio_fill_slider, panel.studio_fill_percent, 65),
        ):
            slider.setValue(updated)
            self.assertEqual(number.text(), str(updated)+'%')
            self.assertTrue(number.toolTip())
        panel.layer_weight.setValue(48)
        self.assertEqual(panel.layer_weight_label.text(), '48%')

    def test_narrow_inspectors_scroll_not_clip_and_do_not_hide_entire_model(self):
        panel = self.panel
        panel.resize(475, 410)
        panel.show()
        self.app.processEvents()
        for section in ('View', 'Materials', 'Animation'):
            panel.toggle_overlay(section, True)
            self.app.processEvents()
            overlay = panel._overlay
            self.assertLessEqual(overlay.width()+overlay.x(), panel.canvas.width())
            self.assertLessEqual(overlay.height()+overlay.y(), panel.canvas.height())
            self.assertLessEqual(overlay.width(), 415)
            self.assertLessEqual(overlay.height(), 490)
        panel.toggle_overlay('Animation', True)
        for heading, _ in panel.animation_sections.values():
            heading.setChecked(True)
        panel._update_animation_scrolling()
        self.app.processEvents()
        self.assertGreater(panel._overlay.verticalScrollBar().maximum(), 0)

    def test_animation_loader_shows_truthful_busy_indicator_until_done_or_cancelled(self):
        panel = self.panel
        self.assertTrue(panel.native_loading_footer.isHidden())
        panel._native_pool = SimpleNamespace(start=Mock())
        ref = AnimationReference('tech_idle', 'tech_3p_idle.caf',
                                 'caf', True, Path('tech_3p_idle.caf'))
        panel._native_references = (ref,)
        panel._native_loader = Mock()
        panel.native_clip_select.addItem('tech_idle', 0)
        panel.native_clip_select.setCurrentIndex(0)
        panel.load_native()
        self.assertFalse(panel.native_loading_footer.isHidden())
        self.assertEqual((panel.native_loading_progress.minimum(),
                          panel.native_loading_progress.maximum()), (0, 0))
        self.assertIn('Decoding CAF', panel.native_loading_label.text())
        token = panel._native_generation
        panel._native_ready(token, None, 'Synthetic rig mismatch')
        self.assertTrue(panel.native_loading_footer.isHidden())
        self.assertIn('Synthetic rig mismatch', panel.native_info.text())
        panel.load_native()
        old = panel._native_generation
        panel.cancel_native_conversion()
        self.assertGreater(panel._native_generation, old)
        self.assertTrue(panel.native_loading_footer.isHidden())
        panel._native_ready(old, None, 'stale result')
        self.assertNotIn('stale result', panel.native_info.text())

    def test_glb_loading_is_async_and_cancellable(self):
        panel = self.panel
        panel._native_pool = SimpleNamespace(start=Mock())
        panel._load_animation_file('example.glb')
        self.assertFalse(panel.native_loading_footer.isHidden())
        self.assertIn('GLB', panel.native_loading_label.text())
        panel._native_pool.start.assert_called_once()
        token = panel._file_generation
        panel.cancel_native_conversion()
        panel._file_animation_ready(token, None, 'stale failure')
        self.assertTrue(panel.native_loading_footer.isHidden())
        panel._load_animation_file('example.glb')
        panel._file_animation_ready(panel._file_generation, None, 'Unsupported rig')
        self.assertIn('Unsupported rig', panel.anim_status.text())
