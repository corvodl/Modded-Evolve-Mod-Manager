"""Patch 2 native Qt design regression tests (offscreen, no Evolve access)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QSplitter

from qt_theme_116 import palette, qt_palette_style
from qt_ui_components import (
    WorkspaceHeader, SurfacePanel, DisclosurePanel,
    ContextActions, configure_splitter,
)


class DesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def test_shared_palette_dark_light_and_density(self):
        for mode in ("Dark", "Light"):
            values = palette(mode)
            style = qt_palette_style(
                "QWidget { color: #f5f3f6; }", mode, density="Compact")
            self.assertIn(values["text"], style)
            self.assertIn("QFrame#workspaceSurface", style)
            self.assertIn("QToolButton#contextOverflow", style)
            self.assertIn("QFrame#fileExplorerPane", style)
            self.assertIn("QScrollBar::handle:vertical", style)
            self.assertIn("padding: 5px 9px", style)
            self.assertNotIn("backdrop-filter", style.lower())
        special = qt_palette_style("", "Dark", {"accent": "#ab4055"})
        self.assertIn("#ab4055", special)

    def test_compact_header_and_unbordered_disclosures(self):
        header = WorkspaceHeader("Modding", "Manage projects")
        self.assertEqual(header.title.text(), "Modding")
        self.assertEqual(header.description.text(), "Manage projects")
        group = SurfacePanel("Materials")
        self.assertEqual(group.objectName(), "workspaceSurface")
        self.assertGreaterEqual(group.content.count(), 1)
        disclosure = DisclosurePanel("Advanced options", expanded=False)
        self.assertFalse(disclosure.body.isVisible())
        detail = QLabel("Advanced value")
        disclosure.addWidget(detail)
        disclosure.toggle.setChecked(True)
        self.assertFalse(disclosure.body.isHidden())
        disclosure.toggle.setChecked(False)
        self.assertFalse(disclosure.body.isVisible())
        header.deleteLater()
        group.deleteLater()
        disclosure.deleteLater()

    def test_context_actions_keep_rare_commands_in_overflow(self):
        actions = ContextActions()
        used = []
        primary = actions.add_action("Open", lambda: used.append("open"), primary=True)
        secondary = actions.add_action(
            "Rebuild all", lambda: used.append("rebuild"), overflow=True)
        primary.click()
        secondary.trigger()
        self.assertEqual(used, ["open", "rebuild"])
        self.assertEqual(primary.objectName(), "primary")
        self.assertEqual(actions.menu.actions(), [secondary])
        self.assertEqual(actions.more.toolTip(), "Additional actions")

    def test_splitters_keep_viewports_resizable(self):
        panel = configure_splitter(QSplitter(Qt.Horizontal))
        panel.addWidget(QWidget())
        panel.addWidget(QWidget())
        panel.setSizes([250, 700])
        self.assertFalse(panel.childrenCollapsible())
        self.assertGreaterEqual(panel.handleWidth(), 5)


if __name__ == "__main__":
    unittest.main()
