"""Compact SSAO controls and nonblocking character-loading status checks."""
from __future__ import annotations
import os
import time
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtWidgets import QApplication, QLabel, QFrame, QProgressBar
from model_preview import read_preview_mesh
from test_material_preview import uv_mesh
from qt_gpu_viewer import QtGPUModelPanel
from qt_workspace_editor import QtWorkspaceEditor


class CompactGpuModelUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        mesh=read_preview_mesh(uv_mesh(),'synthetic.skinm')
        self.panel=QtGPUModelPanel(None,mesh,compact=True)
        self.addCleanup(self.panel.close)

    def test_ssao_is_opt_in_and_uses_bounded_presets(self):
        selector=self.panel.ssao_combo
        self.assertEqual(selector.objectName(),'modelSsaoQuality')
        self.assertEqual([selector.itemText(i) for i in range(selector.count())],
                         ['Off','Low','Medium','High'])
        self.assertEqual(self.panel.canvas.ssao_quality,'Off')
        selector.setCurrentText('Medium')
        self.assertEqual(self.panel.canvas.ssao_quality,'Medium')
        selector.setCurrentText('Off')
        self.assertEqual(self.panel.canvas.ssao_quality,'Off')

    def test_view_materials_and_animation_inspectors_stay_compact(self):
        self.panel.resize(530,470)
        self.panel.show()
        self.app.processEvents()
        for section in ('View','Materials','Animation'):
            self.panel.toggle_overlay(section,True)
            self.app.processEvents()
            overlay=self.panel._overlay
            self.assertLessEqual(overlay.height(),min(490,self.panel.canvas.height()))
            self.assertLessEqual(overlay.width(),415)
            self.assertLessEqual(overlay.width()+overlay.x(),
                                 self.panel.canvas.width())
        self.panel.toggle_overlay('Materials',True)
        self.app.processEvents()
        self.assertFalse(self.panel.solid_geometry.isVisible())
        self.assertFalse(self.panel.diffuse_source_info.isVisible())
        self.panel.toggle_overlay('Animation',True)
        self.app.processEvents()
        self.assertFalse(self.panel.native_info.isVisible())
        self.assertTrue(self.panel.native_clip_select.isVisible())

    def test_texture_progress_footer_tracks_actual_uploads_and_hides(self):
        footer = self.panel.texture_loading_footer
        bar = self.panel.texture_loading_progress
        self.assertEqual(footer.objectName(), 'modelTextureLoadingFooter')
        self.assertEqual(bar.objectName(), 'modelTextureLoadingProgress')
        self.assertTrue(footer.isHidden())
        self.panel._update_texture_load_progress(0, 6)
        self.assertFalse(footer.isHidden())
        self.assertEqual((bar.minimum(), bar.maximum(), bar.value()), (0, 6, 0))
        self.assertIn('6 remaining', self.panel.texture_loading_label.text())
        self.panel._update_texture_load_progress(4, 6)
        self.assertEqual(bar.value(), 4)
        self.assertIn('2 remaining', self.panel.texture_loading_label.text())
        self.panel._update_texture_load_progress(6, 6)
        self.assertTrue(footer.isHidden())
        self.panel._update_texture_load_progress(0, 2)
        self.assertFalse(footer.isHidden())
        self.assertEqual(bar.maximum(), 2)
        self.panel._update_texture_load_progress(0, 0)
        self.assertTrue(footer.isHidden())
        self.panel.set_texture_resolution_pending(True)
        self.assertFalse(footer.isHidden())
        self.assertEqual((bar.minimum(), bar.maximum()), (0,0))
        self.assertIn('Resolving textures', self.panel.texture_loading_label.text())
        self.panel._update_texture_load_progress(1, 3)
        self.assertEqual((bar.minimum(),bar.maximum()), (0,0))
        self.panel.set_texture_resolution_pending(False)
        self.assertTrue(footer.isHidden())

    def test_model_loading_footer_reports_work_before_geometry_ready(self):
        footer = QFrame()
        footer.setObjectName('modelLoadingFooter')
        label = QLabel()
        bar = QProgressBar()
        footer.hide()
        fake = SimpleNamespace(model_loading_footer=footer,
                               model_loading_label=label,
                               model_loading_progress=bar)
        QtWorkspaceEditor._set_model_loading_phase(
            fake, 'Loading model · reading geometry…')
        self.assertFalse(footer.isHidden())
        self.assertIn('reading geometry', label.text())
        self.assertEqual((bar.minimum(), bar.maximum()), (0, 0))
        QtWorkspaceEditor._set_model_loading_phase(
            fake, 'Loading model · indexing animation references…')
        self.assertIn('animation', label.text())
        QtWorkspaceEditor._set_model_loading_phase(fake, '', False)
        self.assertTrue(footer.isHidden())
        QtWorkspaceEditor._set_model_loading_phase(
            fake, 'Loading model · reading geometry…')
        self.assertFalse(footer.isHidden())
        self.assertEqual((bar.minimum(),bar.maximum()),(0,0))

    def test_stage_status_explains_progress_without_blocking(self):
        status=QLabel()
        fake=SimpleNamespace(
            _model_generation=6,mode='models',_model_prepared=False,
            _model_started_at=time.perf_counter()-3.,
            status=status,current='characters/creatures/armadonbull.skinm')
        QtWorkspaceEditor._model_stage_ready(fake,6,'materials')
        self.assertIn('material',status.text())
        QtWorkspaceEditor._model_stage_ready(fake,6,'animations')
        self.assertIn('animation',status.text())
        QtWorkspaceEditor._slow_model_notice(fake,6)
        self.assertIn('longer than usual',status.text())
        previous=status.text()
        fake._model_prepared=True
        QtWorkspaceEditor._model_stage_ready(fake,6,'materials')
        self.assertEqual(status.text(),previous)
        fake._model_prepared=False
        QtWorkspaceEditor._model_stage_ready(fake,5,'materials')
        self.assertEqual(status.text(),previous)


if __name__=='__main__':
    unittest.main()
