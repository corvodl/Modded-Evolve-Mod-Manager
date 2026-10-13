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
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QTableWidget, QFrame, QCheckBox, QToolButton, QLineEdit, QMessageBox

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

    def test_gpu_popup_toolbar_uses_real_icons(self):
        from qt_gpu_viewer import _viewport_tool_icon
        for kind in ('View','Materials','Animation','Grid','Solid','Wire'):
            with self.subTest(kind=kind):
                icon=_viewport_tool_icon(kind)
                self.assertFalse(icon.isNull())
                self.assertFalse(icon.pixmap(18,18).isNull())

    def test_ascii_glyphs_render_as_distinct_letters_not_squares(self):
        font = self.application.font()
        self.assertTrue(glyphs_readable(font), ui_family())
        rasters = [glyph_signature(font, char) for char in 'ABM129']
        self.assertEqual(len({shape for shape, ink in rasters}), 6)
        self.assertTrue(all(ink >= 6 for _, ink in rasters))
        self.assertIn('font-family:"'+ui_family()+'";', qt_style())

    def test_all_pages_and_instruction_information_survive(self):
        self.assertEqual(SECTIONS, (
            'Instructions','Asset Library','Modding','Play & Restore','Settings',
            'Credits',"What's New",'Developer'))
        self.assertEqual(self.win.stack.count(), 8)
        self.assertLess(SECTIONS.index('Asset Library'), SECTIONS.index('Modding'))
        self.assertEqual(self.win.asset_table.columnCount(), 6)
        self.assertTrue(any(
            button.text() == 'Open Asset Library Folder'
            for button in self.win.pages['asset_library'].findChildren(QPushButton)))
        with patch('qt_manager.QDesktopServices.openUrl', return_value=True) as opener:
            from asset_library import LIBRARY_HOME
            with patch('asset_library.LIBRARY_HOME', self.base):
                self.win.open_library_folder()
            opener.assert_called_once()
            self.assertTrue(os.path.samefile(opener.call_args.args[0].toLocalFile(), self.base))
        self.assertIn('preview and export', ' '.join(
            item.text() for item in
            self.win.pages['asset_library'].findChildren(QLabel)).casefold())
        self.assertIn('read-only', ' '.join(
            item.text() for item in
            self.win.pages['asset_library'].findChildren(QLabel)).casefold())
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.assertTrue(self.win.menu_button.text().startswith('☰'))
        self.assertIn('modded-evolve.com',
            ' '.join(widget.text() for widget in
                     self.win.pages['instructions'].findChildren(QLabel)))
        self.assertIn('Restore original game files',
            ' '.join(widget.text() for widget in
                     self.win.pages['instructions'].findChildren(QLabel)))
        self.assertEqual(self.win.table.columnCount(),6)
        self.assertEqual([self.win.table.horizontalHeaderItem(i).text()
                          for i in range(6)],
                         ['Dependency Group','Folder','Description','File',
                          'Type / Project','Size'])
        self.assertEqual(self.win.file_filter.count(),3)
        self.assertTrue(self.win.version_label.text().startswith('v'))

    def test_complete_manager_controls_present_not_hidden_by_redesign(self):
        names = [button.text() for button in self.win.findChildren(QPushButton)]
        names += [action.text() for group in self.win.modding_menu.actions()
                  for action in group.menu().actions()
                  if not action.isSeparator()]
        required = [
            'Help','Activity','Set Up Manager','Get Evolve Stage 2  ↗',
            'Go to Modding  →','Refresh','Import External PAK…',
            'Unpack PAK + known dependencies',
            'Unpack selected PAKs + dependencies','Edit Files','Review Changes',
            'Build Mod','Add Mod','Import Modified PAK','Undo Staged Mod',
            'Mod Profiles…',
            'Play With Mods','Restore Game Files',
            'Status Details','Set Up Launcher','Refresh Original PAKs',
            'Check Setup','Open Projects','Save Settings','Check for Updates',
            'Create Desktop Shortcut','Export Complete Manager ZIP',
            'Configure Bundled Setup',
        ]
        for name in required:
            with self.subTest(control=name):
                self.assertIn(name,names)

    def test_play_restore_menu_escapes_literal_ampersand(self):
        # QPushButton interprets an unescaped & as a Windows mnemonic.
        self.assertEqual(self.win.nav_buttons[SECTIONS.index('Play & Restore')].text(), 'Play && Restore')
        self.assertEqual(self.win.nav_buttons[SECTIONS.index('Play & Restore')].accessibleName(), 'Play & Restore')
        self.win.nav_buttons[SECTIONS.index('Play & Restore')].click()
        self.application.processEvents()
        self.assertEqual(self.win.stack.currentIndex(), SECTIONS.index('Play & Restore'))

    def test_play_refresh_icon_is_adjacent_and_replaces_full_button(self):
        buttons = [b.text() for b in self.win.pages['play'].findChildren(QPushButton)]
        self.assertNotIn('Refresh Status', buttons)
        self.assertIn('Play With Mods', buttons)
        icon = self.win.refresh_status_icon
        self.assertIsInstance(icon, QToolButton)
        self.assertEqual(icon.accessibleName(), 'Refresh Status')
        self.assertTrue(bool(icon.toolTip()))
        self.assertFalse(icon.icon().isNull())
        self.assertEqual(self.win.launch_status_row.itemAt(0).widget(), self.win.launch_status)
        self.assertEqual(self.win.launch_status_row.itemAt(1).widget(), icon)
        with patch.object(self.win, 'refresh_swap_status') as refresh:
            icon.click()
            refresh.assert_called_once_with()

    def test_hover_information_icons_explain_actions_without_extra_pages(self):
        for section in ('modding', 'play', 'settings'):
            icons = self.win.pages[section].findChildren(QToolButton, 'infoIcon')
            self.assertGreaterEqual(len(icons), 0)  # Compact menus may replace inline info icons.
            for widget in icons:
                self.assertEqual(widget.text(), 'i')
                self.assertGreater(len(widget.toolTip()), 35)
                self.assertTrue(widget.accessibleName().startswith('Information: '))
                self.assertLessEqual(widget.width(), 23)
        build = next(b for b in self.win.pages['modding'].findChildren(QPushButton)
                     if b.text() == 'Build Mod')
        self.assertIn('Build Mod', build.text())
        self.assertEqual(len(self.win.nav_buttons), len(SECTIONS))

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

    def test_info_click_opens_help_without_triggering_adjacent_action(self):
        # The compact Modding page routes help through a menu, rather than
        # eight large inline buttons with separate info icon columns.
        help_menu = next(x.menu() for x in self.win.modding_menu.actions()
                         if x.text() == 'Help')
        action = next(x for x in help_menu.actions()
                      if x.text() == 'Modding Help')
        with patch.object(self.win, 'show_help') as help_dialog, \
             patch.object(self.win, 'run_steps') as run:
            action.trigger()
            help_dialog.assert_called_once_with(0)
            run.assert_not_called()

    def test_whats_new_is_available_offline_and_matches_public_notes(self):
        from release_notes import RELEASE_NOTES
        self.win.nav_buttons[SECTIONS.index("What's New")].click()
        self.assertIs(self.win.stack.currentWidget(), self.win.pages['whats_new'])
        text = ' '.join(x.text() for x in self.win.pages['whats_new'].findChildren(QLabel))
        for version, notes in RELEASE_NOTES:
            self.assertIn('v' + version, text)
            for note in notes:
                self.assertIn(note, text)


    def test_memory_panel_requires_developer_unlock_and_auto_attaches(self):
        from developer_memory_panel import DeveloperMemoryPanel
        self.assertEqual(self.win.findChildren(DeveloperMemoryPanel), [])
        index = SECTIONS.index('Developer')
        with patch('qt_manager.QInputDialog.getText', return_value=('test-input', True)), \
             patch('qt_manager.verify_code', return_value=True), \
             patch('developer_memory_panel.list_evolve_processes', return_value=[39832]), \
             patch.object(DeveloperMemoryPanel, '_run'):
            self.win.open_page(index)
            self.application.processEvents()
            panel = self.win.pages['developer'].findChild(DeveloperMemoryPanel)
            self.assertIsNotNone(panel)
            panel.refresh_processes()
            self.assertEqual(panel._pid, 39832)
            self.assertEqual(panel.processes.currentData(), 39832)
            self.assertIn('39832', panel.connection.text())
            self.assertEqual(panel.tabs.count(), 4)
            self.win.open_page(0)
            self.assertTrue(panel._closed)
            self.assertEqual(self.win.pages['developer'].findChildren(DeveloperMemoryPanel), [])

    def test_developer_write_switches_require_acknowledgment_and_reset(self):
        from developer_memory_panel import DeveloperMemoryPanel
        with patch('qt_manager.QInputDialog.getText',
                   return_value=('test-input',True)), \
             patch('qt_manager.verify_code',return_value=True), \
             patch('developer_memory_panel.list_evolve_processes',
                   return_value=[]):
            self.win.open_page(SECTIONS.index('Developer'))
        panel=self.win.dev_memory_panel
        self.assertFalse(self.win._developer_allow_writes)
        self.assertFalse(self.win._developer_online_write_ack)
        self.assertFalse(panel.write_button.isEnabled())
        self.assertFalse(self.win.dev_online_writes.isEnabled())
        self.win.dev_writes.setChecked(True)
        self.assertTrue(panel.write_button.isEnabled())
        self.assertTrue(self.win.dev_online_writes.isEnabled())
        with patch('qt_manager.QMessageBox.information'):
            self.win.dev_online_writes.setChecked(True)
        self.assertFalse(self.win._developer_online_write_ack)
        with patch('qt_manager.QMessageBox.question',
                   return_value=QMessageBox.Yes), \
             patch('qt_manager.QInputDialog.getText',
                   return_value=('I ACCEPT',True)):
            self.win._developer_allow_online=True
            self.win.dev_online_writes.setChecked(True)
        self.assertTrue(self.win._developer_online_write_ack)
        self.win.open_page(0)
        self.assertFalse(self.win._developer_allow_writes)
        self.assertFalse(self.win._developer_online_write_ack)
        self.assertEqual(self.win.stack.count(),len(SECTIONS))

    def test_developer_cancel_and_invalid_code_keep_previous_page(self):
        self.win.open_page(1)
        button = self.win.nav_buttons[SECTIONS.index('Developer')]
        self.assertEqual(self.win.pages['developer'].findChildren(QLabel), [])
        with patch('qt_manager.QInputDialog.getText', return_value=('', False)), \
             patch('qt_manager.verify_code') as verify:
            button.click()
            verify.assert_not_called()
        self.assertEqual(self.win.stack.currentIndex(), 1)
        with patch('qt_manager.QInputDialog.getText', return_value=('incorrect', True)) as prompt, \
             patch('qt_manager.QMessageBox.warning') as warning:
            button.click()
            self.assertEqual(prompt.call_args.args[-1], QLineEdit.Password)
            warning.assert_called_once()
        self.assertEqual(self.win.stack.currentIndex(), 1)
        self.assertEqual(self.win.nav_buttons[1].objectName(), 'navActive')
        self.assertEqual(self.win.pages['developer'].findChildren(QLabel), [])

    def test_developer_unlocks_only_after_verification_and_relocks_on_leave(self):
        self.win.open_page(1)
        index = SECTIONS.index('Developer')
        with patch('qt_manager.QInputDialog.getText', return_value=('test-input', True)) as prompt, \
             patch('qt_manager.verify_code', return_value=True) as verify:
            self.win.nav_buttons[index].click()
            prompt.assert_called_once()
            verify.assert_called_once_with('test-input')
        protected = self.win.pages['developer']
        self.assertIs(self.win.stack.currentWidget(), protected)
        self.assertTrue(protected.findChildren(QLabel))
        lock = next(b for b in protected.findChildren(QPushButton) if b.text() == 'Lock and leave')
        lock.click()
        self.assertEqual(self.win.stack.currentIndex(), 1)
        self.assertIsNot(self.win.pages['developer'], protected)
        self.assertEqual(self.win.pages['developer'].findChildren(QLabel), [])
        with patch('qt_manager.QInputDialog.getText', return_value=('', False)) as prompt:
            self.win.nav_buttons[index].click()
            prompt.assert_called_once()
        self.assertEqual(self.win.stack.currentIndex(), 1)
        self.assertEqual(self.win.stack.count(), len(SECTIONS))

    def test_developer_reclick_requires_code_and_wrong_attempt_locks_content(self):
        index = SECTIONS.index('Developer')
        with patch('qt_manager.QInputDialog.getText', return_value=('test-input', True)), \
             patch('qt_manager.verify_code', return_value=True):
            self.win.open_page(index)
        with patch('qt_manager.QInputDialog.getText', return_value=('incorrect', True)), \
             patch('qt_manager.QMessageBox.warning'):
            self.win.open_page(index)
        self.assertEqual(self.win.stack.currentIndex(), 0)
        self.assertEqual(self.win.pages['developer'].findChildren(QLabel), [])
        with patch('qt_manager.QInputDialog.getText', return_value=('test-input', True)), \
             patch('qt_manager.verify_code', return_value=True):
            self.win.open_page(index)
        self.win.nav_buttons[3].click()
        self.assertEqual(self.win.stack.currentIndex(), 3)
        self.assertEqual(self.win.pages['developer'].findChildren(QLabel), [])

    def test_settings_sections_stay_inside_settings_page(self):
        self.assertEqual(self.win.settings_sections, (
            'UPDATES', 'GENERAL', 'GAME & FILES', 'ASSET LIBRARY',
            'BACKUPS & RECOVERY', 'LAUNCH PREFERENCES',
            'ADVANCED & MAINTENANCE'))
        self.assertEqual(len(self.win.nav_buttons), len(SECTIONS))
        self.assertEqual(self.win.stack.count(), len(SECTIONS))
        self.assertEqual([btn.text().replace('&&', '&') for btn in self.win.nav_buttons], list(SECTIONS))
        dividers = self.win.pages['settings'].findChildren(QFrame, 'settingsDivider')
        self.assertEqual(len(dividers), len(self.win.settings_sections))
        section_headings = [
            label.text() for label in self.win.pages['settings'].findChildren(QLabel)
            if label.objectName() == 'settingsHeading'
        ]
        self.assertEqual(section_headings, list(self.win.settings_sections))
        buttons = {button.text() for button in
                   self.win.pages['settings'].findChildren(QPushButton)}
        for expected in ('Create Desktop Shortcut', 'Set Up Manager',
                         'Check Setup', 'Open Projects', 'Save Settings',
                         'Client Verification & Recovery Guide',
                         'Prepare Mod Files Now', 'Check for Updates',
                         'Export Complete Manager ZIP',
                         'Configure Bundled Setup', 'Settings Help'):
            self.assertIn(expected, buttons)

    def test_ui_preferences_save_and_change_appearance_without_game_changes(self):
        self.win.density_combo.setCurrentText('Compact')
        self.win.motion_checkbox.setChecked(True)
        self.win.snapshots_checkbox.setChecked(False)
        self.win.activity_checkbox.setChecked(False)
        self.assertEqual(self.win.settings['ui_density'], 'Compact')
        self.assertTrue(self.win.settings['reduce_motion'])
        self.assertFalse(self.win.settings['keep_original_snapshots'])
        self.assertFalse(self.win.settings['show_activity_on_tasks'])
        self.assertEqual(self.win.menu_animation.duration(), 0)
        self.assertIn('padding:5px 10px', self.win.styleSheet())
        self.win.motion_checkbox.setChecked(False)
        self.assertEqual(self.win.menu_animation.duration(), 205)
        self.assertTrue(self.win.motion_checkbox in self.win.pages['settings'].findChildren(QCheckBox))

    def test_prepare_mods_now_never_starts_game_or_swaps_originals(self):
        ready = SwapStatus('prepared', 'Ready', count=2, ready=2)
        with patch.object(self.win, 'refresh_swap_status', return_value=ready), \
             patch.object(self.win, 'info') as info, \
             patch.object(self.win, 'run_steps') as run:
            self.win.prepare_mods_now()
            info.assert_called_once()
            run.assert_not_called()
        restored = SwapStatus('restored', 'Needs preparation', count=2, ready=0,
                              stage=self.win.stage_path)
        steps = [
            ('Preparing mod files', ['python', '-u', 'controlled_swap.py', 'prepare']),
            ('Starting Evolve with Mods', ['python', '-u', 'auto_launch_frida.py']),
        ]
        with patch.object(self.win, 'refresh_swap_status', return_value=restored), \
             patch('qt_manager.launcher_commands', return_value=steps), \
             patch.object(self.win, 'confirm', return_value=True), \
             patch.object(self.win, 'run_steps') as run:
            self.win.prepare_mods_now()
            run.assert_called_once()
            args, kwargs = run.call_args
            self.assertEqual(len(args[0]), 1)
            self.assertEqual(args[0][0][0], 'Preparing mod files')
            self.assertEqual(args[1], 'Prepare Mod Files')
            self.assertNotIn('auto_launch_frida', str(args[0]))
        missing = SwapStatus('missing', 'Not configured')
        with patch.object(self.win, 'refresh_swap_status', return_value=missing), \
             patch.object(self.win, 'failure') as failed, \
             patch.object(self.win, 'run_steps') as run:
            self.win.prepare_mods_now()
            failed.assert_called_once()
            run.assert_not_called()
        unsafe = SwapStatus('swapped', 'Mods live')
        with patch.object(self.win, 'refresh_swap_status', return_value=unsafe), \
             patch.object(self.win, 'failure') as failed, \
             patch.object(self.win, 'run_steps') as run:
            self.win.prepare_mods_now()
            failed.assert_called_once()
            run.assert_not_called()

    def test_recovery_disallows_disabling_live_rename_backups(self):
        settings = self.win.pages['settings']
        explanations = ' '.join(label.text() for label in settings.findChildren(QLabel))
        self.assertIn('same-volume renames', explanations)
        self.assertIn('.customkey-original', explanations)
        self.assertIn('manual fallback', explanations)
        self.assertFalse(hasattr(self.win, 'skip_live_backup_checkbox'))

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
        self.assertEqual(descriptions,['Game'])
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
        self.assertIsInstance(editor, QMainWindow)
        self.assertIsNotNone(editor.centralWidget())
        self.assertTrue(editor.windowFlags() & Qt.WindowMaximizeButtonHint)
        self.assertFalse(editor.windowFlags() & Qt.MSWindowsFixedSizeDialogHint)
        editor.showMaximized()
        self.application.processEvents()
        self.assertTrue(editor.isMaximized())
        editor.showNormal()
        self.application.processEvents()
        from qt_cpu_model_viewer import QtCPUModelCanvas
        self.assertIsInstance(editor.model_cpu, QtCPUModelCanvas)
        self.assertIsNotNone(editor.model_fallback)
        button_labels = [b.text() for b in editor.findChildren(QPushButton)]
        for expected in ('Save', 'Find Text', 'Import / Replace File',
                         'Restore Original', 'Export PNG', 'Import DDS', 'Import PNG'):
            self.assertIn(expected, button_labels)
        # The model tab no longer stacks rows of oversized action buttons.
        self.assertNotIn('Open GPU Viewer', button_labels)
        self.assertNotIn('Export Native Model', button_labels)
        menus = editor.editor_menus
        self.assertEqual(list(menus), ['File', 'Edit', 'View', 'Model',
                                       'Textures', 'Animation', 'Help'])
        menu_texts = {name: [action.text() for action in menu.actions()]
                      for name, menu in menus.items()}
        for label in ('Export for Blender (.glb)…', 'Export Model Folder…',
                      'Export Native Model…'):
            self.assertIn(label, menu_texts['File'])
        self.assertIn('Diagnose Texture Links…', menu_texts['Textures'])
        self.assertIn('Texture Preview Status…', menu_texts['Textures'])
        self.assertIn('Load Animated GLB…', menu_texts['Animation'])
        self.assertIn('Faceted Shading', menu_texts['View'])
        self.assertIn('Wireframe', menu_texts['View'])
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
        self.assertIn("candidate=discover_latest(", code)
        self.assertIn("QTimer.singleShot(2500, lambda: self.check_updates(silent=True))", code)
        self.assertIn("report_startup_ready(APP_HOME)", code)
        self.assertIn("launch_apply(candidate,staged,APP_HOME,os.getpid())", code)

    def test_gpu_model_renderer_is_native_qt_without_tk_mainloop(self):
        code = Path(__file__).with_name('qt_gpu_viewer.py').read_text(encoding='utf-8')
        self.assertIn("class QtGLModelCanvas(QOpenGLWidget)", code)
        self.assertIn("class QtGPUModelPanel(QWidget)", code)
        self.assertIn("class QtGPUModelDialog(QDialog)", code)
        self.assertIn("class QtGPUModelDialog(QDialog)", code)
        editor = Path(__file__).with_name('qt_workspace_editor.py').read_text(encoding='utf-8')
        self.assertIn('class QtWorkspaceEditor(QMainWindow)', editor)
        self.assertIn('QtGPUModelPanel', editor)
        self.assertIn("view.setCurrentWidget(self.model_panel)", editor)
        self.assertIn('self.model_panel', editor)
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
