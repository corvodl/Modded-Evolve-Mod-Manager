"""Compact animation UI regression: GUI widgets work at reduced viewport sizes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from model_preview import read_preview_mesh
from test_material_preview import uv_mesh
from qt_gpu_viewer import QtGPUModelPanel
from character_animation_catalog import AnimationReference


class AnimationOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_animation_actions_consistently_named_play_and_popover_is_compact(self):
        panel = QtGPUModelPanel(None, read_preview_mesh(uv_mesh()))
        try:
            panel.resize(760, 560)
            panel.canvas.resize(690, 480)
            panel.toggle_overlay('Animation', True)
            self.assertEqual(panel.play_native_button.text(), 'Play')
            self.assertLessEqual(panel._overlay.height(), 395)
            self.assertEqual(panel.native_search.placeholderText(), 'Search animation names…')
            self.assertEqual(panel.native_refresh_button.text(), 'Refresh')
            self.assertEqual(panel.native_clip_select.maximumHeight(), 152)
            refs = (
                AnimationReference('Idle', 'characters/test/idle.caf', 'caf', True, Path('idle.caf')),
                AnimationReference('Packed', 'characters/test/packed.caf', 'caf', False, None,
                                   False, Path('clip.dba'), 'characters/test/clip.dba'),
                AnimationReference('Archive', 'characters/test/archive.caf', 'caf', False,
                                   None, True))
            panel._native_references = refs
            panel._native_loader = lambda reference: None
            for i, reference in enumerate(refs):
                panel.native_clip_select.clear()
                panel.native_clip_select.addItem(reference.name, i)
                panel._native_selection(0)
                self.assertEqual(panel.play_native_button.text(), 'Play')
        finally:
            panel.close()
            panel.deleteLater()


if __name__ == '__main__':
    unittest.main()
