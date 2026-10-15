"""Qt regression for the isolated legacy archive editing route."""
from __future__ import annotations
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QTreeWidgetItemIterator
from legacy_pak_workspace import create_project, changed_members, read_member
from qt_legacy_pak_editor import LegacyPAKEditor


class LegacyPAKQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.root / "Scripts.pak"
        with zipfile.ZipFile(source, "w") as z:
            z.writestr("Entities/Spawn.ent", '<Entity name="old"/>')
            z.writestr("Sounds/Monster.fsb", b"FSB5data")
        self.folder = create_project(source, self.root / "legacy-project")
        self.host = QWidget()
        self.editor = LegacyPAKEditor(self.host, self.folder)
        self.addCleanup(self.editor.close)
        self.addCleanup(self.host.close)

    def _select(self, name):
        iterator = QTreeWidgetItemIterator(self.editor.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.UserRole) == name:
                self.editor.tree.setCurrentItem(item)
                self.app.processEvents()
                return
            iterator += 1
        self.fail("Legacy tree missing " + name)

    def test_legacy_text_can_be_edited_without_stage2_keys(self):
        self._select("Entities/Spawn.ent")
        self.assertIn("old", self.editor.text.toPlainText())
        self.editor.text.setPlainText('<Entity name="new"/>')
        self.assertTrue(self.editor.save_button.isEnabled())
        self.editor.save_button.click()
        self.app.processEvents()
        self.assertEqual(changed_members(self.editor.project), ["Entities/Spawn.ent"])
        self.assertIn(b"new", read_member(self.editor.project, "Entities/Spawn.ent"))
        self.assertFalse(self.editor.save_button.isEnabled())

    def test_legacy_editor_uses_contextual_menus_not_six_button_row(self):
        self.assertEqual(list(self.editor.editor_menus),
                         ["File", "Edit", "View", "Tools"])
        self._select("Entities/Spawn.ent")
        self.assertTrue(self.editor.png_button.isHidden())
        self.assertTrue(self.editor.build_button.isHidden())
        self.assertTrue(self.editor.tool_buttons["Replace File…"].isHidden())
        self.editor.text.setPlainText("<Entity modified='yes'/>")
        self.app.processEvents()
        self.assertFalse(self.editor.save_button.isHidden())
        self.assertIn("Build Legacy PAK…",
                      [a.text() for a in self.editor.editor_menus["File"].actions()])
        # Leave no unsaved buffer: closeEvent prompts the real user.
        self.editor.text.setPlainText(self.editor.loaded_text)
        self.app.processEvents()

    def test_binary_selection_offers_safe_raw_replacement(self):
        self._select("Sounds/Monster.fsb")
        self.assertIn("Binary file", self.editor.info.text())
        self.assertFalse(self.editor.save_button.isEnabled())
        self.assertFalse(self.editor.png_button.isEnabled())
        self.assertEqual(self.editor.project["source_path"].suffix, ".pak")


if __name__ == "__main__":
    unittest.main()
