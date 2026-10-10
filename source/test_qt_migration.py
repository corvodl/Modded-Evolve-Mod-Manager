"""Qt migration smoke/parity tests. Never modify an installed game.

Use offscreen Qt rendering in Windows CI; packaged EXE has a separate
on-screen screenshot capture step.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QTableWidget

from launch_integration import SwapStatus
from qt_manager import QtManager, SECTIONS, qt_style
from qt_font_health import ensure_readable_ui_font, glyphs_readable, glyph_signature, ui_family
from qt_workspace_editor import QtWorkspaceEditor, QtLooseFileEditor
from qt_preview_package import package


class QtMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyle('Fusion')
        ensure_readable_ui_font(cls.application, strict=True)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings_path = root/'empty-settings.json'
        self.base = root
        self.patchers = [
            patch('qt_manager.settings_source', return_value=self.settings_path),
            patch('qt_manager.allowed_paks', return_value={}),
            patch('qt_manager.state_status',
                  return_value=SwapStatus('missing','Not prepared yet.')),
            patch('qt_manager.save_settings', return_value=None),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)
        self.win = QtManager()
        self.addCleanup(self.win.close)
        self.win.show()
        self.application.processEvents()

    def test_ascii_glyphs_render_as_distinct_letters_not_squares(self):
        font = self.application.font()
        self.assertTrue(glyphs_readable(font), ui_family())
        rasters = [glyph_signature(font, char) for char in 'ABM129']
        self.assertEqual(len({shape for shape, ink in rasters}), 6)
        self.assertTrue(all(ink > 6 for _, ink in rasters))
        self.assertIn('font-family:"'+ui_family()+'";', qt_style())

    def test_all_pages_and_instruction_information_survive(self):
        self.assertEqual(SECTIONS, (
            'Instructions','Modding','Play & Restore','Settings','Credits'))
        self.assertEqual(self.win.stack.count(), 5)
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.assertTrue(self.win.menu_button.text().startswith('☰'))
        self.assertIn('modded-evolve.com',
            ' '.join(widget.text() for widget in
                     self.win.pages['instructions'].findChildren(QLabel)))
        self.assertIn('Restore original game files',
            ' '.join(widget.text() for widget in
                     self.win.pages['instructions'].findChildren(QLabel)))
        self.assertEqual(self.win.table.columnCount(),5)
        self.assertEqual([self.win.table.horizontalHeaderItem(i).text()
                          for i in range(5)],
                         ['Folder','Description','File','Type / Project','Size'])
        self.assertEqual(self.win.file_filter.count(),3)
        self.assertTrue(self.win.version_label.text().startswith('v'))

    def test_complete_manager_controls_present_not_hidden_by_redesign(self):
        names = [button.text() for button in self.win.findChildren(QPushButton)]
        required = [
            'Help','Activity','Set Up Manager','Get Evolve Stage 2  ↗',
            'Go to Modding  →','Refresh','External PAK…',
            'Unpack PAK','Unpack Selected PAKs','Edit Files','Review Changes',
            'Build Mod','Add Mod','Import Modified PAK','Undo Staged Mod',
            'Play With Mods','Restore Game Files','Refresh Status',
            'Status Details','Set Up Launcher','Refresh Original PAKs',
            'Check Setup','Open Projects','Save Settings','Check for Updates',
            'Create Desktop Shortcut','Export Complete Manager ZIP',
            'Configure Bundled Setup',
        ]
        for name in required:
            with self.subTest(control=name):
                self.assertIn(name,names)

    def test_qpropertyanimation_is_an_overlay_and_does_not_resize(self):
        width = self.win.stack.width()
        self.assertFalse(self.win._drawer_open)
        self.win.menu_button.click()
        self.assertTrue(self.win._drawer_open)
        self.assertEqual(self.win.menu_animation.duration(),205)
        self.assertTrue(self.win.drawer.isVisible())
        self.win.menu_animation.setCurrentTime(205)
        self.application.processEvents()
        self.assertEqual(self.win.drawer.x(),0)
        self.assertEqual(self.win.stack.width(),width)
        self.win.open_page(1)
        self.win.menu_animation.setCurrentTime(205)
        self.application.processEvents()
        self.assertEqual(self.win.stack.currentIndex(),1)
        self.assertFalse(self.win._drawer_open)
        self.assertEqual(self.win.stack.width(),width)

    def test_table_search_description_and_original_names(self):
        stage = self.base/'stage'/'paks'/'Game'
        stage.mkdir(parents=True)
        (stage/'characters_monsters_goliath_data.pak').write_bytes(b'data')
        (stage/'characters_monsters_goliath_ts.pak').write_bytes(b'texture')
        self.win.stage_path = str(self.base/'stage')
        self.win.fields['stage'].setText(self.win.stage_path)
        with patch('qt_manager.allowed_paks',return_value={
            'Game/characters_monsters_goliath_data.pak':'Game/characters_monsters_goliath_data.pak',
            'Game/characters_monsters_goliath_ts.pak':'Game/characters_monsters_goliath_ts.pak',
        }):
            self.win.refresh_archives()
        self.assertEqual(self.win.table.rowCount(),2)
        self.win.search.setText('Goliath Models')
        self.application.processEvents()
        self.assertEqual(self.win.table.rowCount(),1)
        descriptions = [self.win.table.item(i,1).text()
                        for i in range(self.win.table.rowCount())]
        self.assertEqual(descriptions,['Goliath Models'])
        self.assertEqual(self.win.table.item(0,2).text(),
                         'characters_monsters_goliath_data.pak')

    def test_qt_editor_preserves_text_and_shows_every_editor_action(self):
        ws = self.base/'Workspace'
        path = ws/'files'/'Game'/'settings.txt'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'hello original\n')
        (ws/'.evolve-pak-workspace.json').write_text(json.dumps(
            {'entries':[{'path':'Game/settings.txt','format':'text',
                         'sha256':'digest-not-used-for-saving'}]}),
            encoding='utf-8')
        self.win.projects_path = str(self.base/'Projects')
        editor = QtWorkspaceEditor(self.win,ws)
        self.addCleanup(editor.close)
        self.assertEqual(editor.tabs.count(),3)
        button_labels = [b.text() for b in editor.findChildren(QPushButton)]
        for expected in (
            'Save','Find Text','Import / Replace File','Restore Original',
            'Export PNG','Import DDS','Import PNG','Export Native Model',
            'Import Same-Layout Model','Find Model Textures',
            'Choose Texture Folder','Open GPU Viewer',
        ):
            self.assertIn(expected,button_labels)
        editor.load('Game/settings.txt')
        self.assertFalse(editor.views['files'].isReadOnly())
        editor.views['files'].setPlainText('hello Qt\n')
        editor.save()
        self.assertEqual(path.read_bytes(),b'hello Qt\n')
        self.assertTrue(any((ws/'EditorBackups').rglob('settings.txt')))
        self.assertFalse(editor._dirty())
        self.assertIsNotNone(editor.save_shortcut)
        self.assertIsNotNone(editor.find_shortcut)

    def test_compiled_qt_stable_update_path_keeps_manual_previews_separate(self):
        from qt_manager import QtManager
        code = Path(__file__).with_name('qt_manager.py').read_text(encoding='utf-8')
        self.assertIn("if not silent:", code)
        self.assertIn("preview = discover_experimental()", code)
        self.assertIn("QTimer.singleShot(2500, lambda: self.check_updates(silent=True))", code)
        self.assertIn("report_startup_ready(APP_HOME)", code)
        self.assertIn("launch_apply(candidate,staged,APP_HOME,os.getpid())", code)

    def test_gpu_model_renderer_is_native_qt_without_tk_mainloop(self):
        code = Path(__file__).with_name('qt_gpu_viewer.py').read_text(encoding='utf-8')
        self.assertIn("class QtGLModelCanvas(QOpenGLWidget)", code)
        self.assertIn("glDrawElements", code)
        self.assertIn("glTexImage2D", code)
        self.assertNotIn("tkinter", code)

    def test_native_qt_editor_does_not_launch_tk_compatibility_ui(self):
        manager = Path(__file__).with_name('qt_manager.py').read_text('utf-8')
        editor = Path(__file__).with_name('qt_workspace_editor.py').read_text('utf-8')
        self.assertNotIn('EvolveModLegacyEditor.exe', manager)
        self.assertNotIn('qt_legacy_editor_bridge', manager)
        self.assertNotIn('Advanced compatibility editor', editor)
        self.assertIn('Open GPU Viewer', editor)

    def test_preview_never_packages_keys_or_claims_stable_main(self):
        src = self.base/'EvolveModManagerQt'
        src.mkdir()
        for name in ('EvolveModManagerQt.exe','EvolveModWorker.exe',
                     'EvolveModLegacyEditor.exe','BUILD_COMMIT.txt',
                     'BUILD_CHANNEL.txt'):
            (src/name).write_bytes(b'preview')
        (src/'BUILD_CHANNEL.txt').write_text('qt-preview')
        outcome = package(src,self.base/'dist')
        self.assertTrue(outcome.is_file())
        (src/'Data').mkdir()
        with self.assertRaisesRegex(RuntimeError,'Data folder'):
            package(src,self.base/'dist')

    def test_no_setup_dialogs_or_mutation_on_first_boot(self):
        self.assertFalse(self.win.busy)
        self.assertIsNone(self.win.process)
        self.assertFalse(self.win._drawer_open)
        self.assertEqual(self.win.stack.currentIndex(),0)
        self.assertFalse(self.win._log_dialog)
        self.assertEqual(self.win.archive_entries,[])
        self.assertFalse(self.settings_path.exists())


if __name__ == '__main__':
    unittest.main()
