"""Regression: compact Wraith material diagnosis never explodes into a tall popup."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from qt_model_texture_diagnostics import (
    ModelTextureDiagnosticsDialog, texture_diagnostic_summary,
    full_texture_diagnostic_report,
)


class CompactTextureDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        parts = [
            {'part': i, 'archive': 'characters_monsters_teleporter_ts.pak',
             'physical': 'C:/AssetLibrary/teleporter_diff.dds.%s' % i}
            for i in range(32)
        ]
        self.reports = [(
            'characters/monsters/teleporter/teleporter1.skinm', {
                'material': 'characters/monsters/teleporter/teleporter.mtl',
                'status': 'found',
                'basis': 'unique equipment-prefix material',
                'textures': [{
                    'map': 'Diffuse',
                    'reference': 'characters/monsters/teleporter/textures/teleporter_diff.tif',
                    'status': 'found',
                    'paths': ['characters/monsters/teleporter/textures/teleporter_diff.dds.0'],
                    'archives': ['characters_monsters_teleporter_ts.pak'],
                    'physical_paths': [parts[0]['physical']],
                    'stream_parts': parts,
                }],
            }
        )]
        self.dialog = ModelTextureDiagnosticsDialog(
            self.reports[0][0], self.reports[0][0],
            self.reports, ('Optional bumpmap decode warning',))
        self.addCleanup(self.dialog.close)

    def test_summary_counts_all_parts_without_expanding(self):
        summary = texture_diagnostic_summary(
            self.reports, ('Optional bumpmap decode warning',))
        self.assertEqual(summary['found'], 1)
        self.assertEqual(summary['fragments'], 32)
        self.assertEqual(summary['issues'], 1)
        self.dialog.show()
        self.app.processEvents()
        self.assertLessEqual(self.dialog.width(), 900)
        self.assertLessEqual(self.dialog.height(), 580)
        root = self.dialog.tree.topLevelItem(0)
        self.assertTrue(root.isExpanded())
        texture = root.child(1)
        self.assertEqual(texture.text(1), 'found')
        self.assertFalse(texture.isExpanded())
        mip_row = texture.child(1)
        self.assertIn('32', mip_row.text(0))
        self.assertFalse(mip_row.isExpanded())

    def test_filter_and_full_report_preserve_source_paths(self):
        self.dialog.search.setText('teleporter_diff')
        self.app.processEvents()
        self.assertFalse(self.dialog.tree.topLevelItem(0).isHidden())
        text = full_texture_diagnostic_report(
            self.reports[0][0], self.reports[0][0], self.reports,
            ('Optional bumpmap decode warning',))
        self.assertIn('teleporter_diff.dds.31', text)
        self.assertIn('Optional bumpmap decode warning', text)
        self.dialog._copy_report()
        self.assertEqual(QApplication.clipboard().text(), text)

    def test_expand_details_can_be_collapsed(self):
        self.dialog._toggle_details()
        self.assertEqual(self.dialog.detail_button.text(), 'Collapse details')
        self.dialog._toggle_details()
        self.assertEqual(self.dialog.detail_button.text(), 'Expand details')


if __name__ == '__main__':
    unittest.main()
