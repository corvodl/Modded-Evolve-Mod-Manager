"""Non-destructive model viewer normal-Y comparison and packed-normal tests."""
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtWidgets import QApplication
from material_preview import PreviewMaterials
from model_preview import read_preview_mesh
from qt_gpu_viewer import QtGPUModelPanel
from normal_map_utils import decode_xy_normal_rgba
from renderer_3 import FRAGMENT, Renderer3
from test_material_preview import uv_mesh


class NormalYDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        mesh = read_preview_mesh(uv_mesh(),'Emet test.skinm')
        materials = PreviewMaterials(
            (Image.new('RGB',(4,4),(150,150,150)),),('Emet body',),'emet.mtl',
            normal_maps=(Image.new('RGB',(4,4),(128,200,220)),))
        self.panel = QtGPUModelPanel(None, mesh, materials, compact=True)
        self.addCleanup(self.panel.close)

    def test_control_changes_gpu_state_without_writing_material_pixels(self):
        combo = self.panel.normal_convention_combo
        self.assertEqual(combo.objectName(),'modelNormalYDiagnostic')
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],[
            'Source (unchanged)','Flip green (DX to GL)','Flat (no normal maps)'])
        original = self.panel.canvas.materials.normal_maps[0].tobytes()
        self.assertEqual(self.panel.canvas.normal_diagnostic,'Source (unchanged)')
        self.assertIn('1 base + 0 detail',self.panel.normal_diagnostic_info.text())
        combo.setCurrentText('Flip green (DX to GL)')
        self.assertEqual(self.panel.canvas.normal_diagnostic,'Flip green (DX to GL)')
        combo.setCurrentText('Flat (no normal maps)')
        self.assertEqual(self.panel.canvas.normal_diagnostic,'Flat (no normal maps)')
        self.assertEqual(self.panel.canvas.materials.normal_maps[0].tobytes(),original)
        with self.assertRaises(ValueError):
            self.panel.canvas.set_normal_diagnostic('Some other mode')

    def test_shader_inverts_both_primary_and_detail_exactly_once(self):
        self.assertIn('uniform int normal_y_flip;', FRAGMENT)
        self.assertIn('if (normal_y_flip == 1) mapped.y = -mapped.y;', FRAGMENT)
        self.assertIn('if (normal_y_flip == 1) fine.y = -fine.y;', FRAGMENT)
        self.assertIn("('normal_y_flip',invert_normal_y)", Renderer3.bind.__code__.co_consts)
        self.assertIn('normal_y_flip', Renderer3.__init__.__code__.co_consts.__str__())

    def test_nonflat_y_fixture_detects_polarity(self):
        # Neutral Y=128 cannot detect an inversion. Use non-neutral Y.
        raw = bytes((128, 195, 0, 255))
        unchanged = decode_xy_normal_rgba(1,1,raw,invert_green=False).rgba
        flipped = decode_xy_normal_rgba(1,1,raw,invert_green=True).rgba
        self.assertGreater(unchanged[1], 185)
        self.assertLess(flipped[1], 70)
        self.assertLessEqual(abs(unchanged[0]-flipped[0]),1)
        self.assertLessEqual(abs(unchanged[2]-flipped[2]),1)

    def test_zero_normal_map_selection_reports_not_comparable(self):
        materials=PreviewMaterials(
            (Image.new('RGB',(4,4),(150,150,150)),),('Emet body',),'emet.mtl')
        self.panel.canvas.materials=materials
        self.panel._update_normal_source_status()
        self.assertIn('No normal mapping to compare',self.panel.normal_diagnostic_info.text())


if __name__=='__main__':
    unittest.main()
