"""Offscreen interaction regressions for debounced explorer and model requests."""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QTreeWidgetItemIterator

from qt_workspace_editor import QtWorkspaceEditor


class ModelLoadResponsivenessTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        workspace = root/'workspace'
        files = workspace/'files'
        files.joinpath('characters').mkdir(parents=True)
        for path in ('characters/a.skinm','characters/b.skinm'):
            (files/path).write_bytes(b'CrChF fixture')
        (workspace/'.evolve-pak-workspace.json').write_text(json.dumps({
            'entries':[{'path':x,'format':'model'} for x in
                       ('characters/a.skinm','characters/b.skinm')]}),
            encoding='utf-8')
        parent = QMainWindow()
        parent.settings = {}
        parent.busy = False
        parent.projects_path = root
        parent.stage_path = root
        parent.game_root = None
        parent._find_game_root = lambda: None
        parent.current_workspace = str(workspace)
        parent.persist = lambda: None
        parent.open_pak_diff = lambda: None
        editor = QtWorkspaceEditor(parent, workspace)
        self.editor = editor
        self.addCleanup(editor.close)
        self.addCleanup(parent.close)

    def test_typing_does_not_rebuild_explorer_until_paused_or_enter(self):
        editor = self.editor
        self.assertGreaterEqual(editor._search_timer.interval(), 500)
        with patch.object(editor, 'populate') as rebuilt:
            editor.search.setText('a')
            self.app.processEvents()
            self.assertTrue(editor._search_timer.isActive())
            editor.search.setText('a.skinm')
            self.app.processEvents()
            self.assertIn('Search pending', editor.explorer_count.text())
            rebuilt.assert_not_called()
            editor._search_now()
            rebuilt.assert_called_once()
            self.assertFalse(editor._search_timer.isActive())
        self.assertEqual(editor._search_candidates(), ['characters/a.skinm'])
        stamp = editor._search_records_cache_key
        self.assertIsNotNone(stamp)
        with patch('builtins.sorted', side_effect=AssertionError(
                'Do not re-sort all asset paths for every query')):
            self.assertEqual(editor._search_candidates(), ['characters/a.skinm'])
        editor.search.setText('b.skinm')
        self.assertEqual(editor._search_candidates(), ['characters/b.skinm'])

    def test_rapid_selection_commits_only_last_model_and_skips_repeat(self):
        editor = self.editor
        editor._virtual_active = False
        entries = {}
        iterator = QTreeWidgetItemIterator(editor.file_tree)
        while iterator.value():
            rel = iterator.value().data(0,Qt.UserRole)
            if rel in ('characters/a.skinm','characters/b.skinm'):
                entries[rel] = iterator.value()
            iterator += 1
        self.assertEqual(len(entries),2)
        with (patch.dict(os.environ, {'QT_QPA_PLATFORM':'windows'}),
              patch.object(editor, 'load') as load):
            editor.file_tree.setCurrentItem(entries['characters/a.skinm'])
            self.assertTrue(editor._model_selection_timer.isActive())
            editor.file_tree.setCurrentItem(entries['characters/b.skinm'])
            load.assert_not_called()
            self.assertEqual(editor._queued_model,
                             ('characters/b.skinm',False))
            editor._open_queued_model()
            load.assert_called_once_with('characters/b.skinm',
                                         full_character=False)
            # Selection of the already fully loaded model doesn't re-decode.
            editor._model_selection_timer.stop()
            editor._queued_model = None
            editor.current = 'characters/b.skinm'
            editor.mode = 'models'
            editor._model_prepared = True
            editor.load_selection()
            self.assertFalse(editor._model_selection_timer.isActive())
            self.assertEqual(load.call_count,1)

    def test_model_cancel_hides_progress_invalidates_previous_worker(self):
        editor = self.editor
        editor.mode='models'
        editor.current='characters/a.skinm'
        editor._model_started_at = __import__('time').perf_counter()-1.4
        editor._model_pool = Mock()
        editor._model_loading_started = None
        editor._set_model_loading_phase('Reading source mesh')
        self.app.processEvents()
        self.assertFalse(editor.model_loading_footer.isHidden())
        self.assertEqual((editor.model_loading_progress.minimum(),
                          editor.model_loading_progress.maximum()),(0,0))
        editor._update_model_elapsed()
        self.assertIn('s',editor.model_loading_elapsed.text())
        token=editor._model_generation
        editor.cancel_model_loading()
        self.assertGreater(editor._model_generation,token)
        self.assertTrue(editor._model_cancel.is_set())
        self.assertTrue(editor.model_loading_footer.isHidden())
        self.assertFalse(editor._model_elapsed_timer.isActive())
        editor._model_pool.clear.assert_called_once()
        editor._model_ready(token,None,'stale result')
        self.assertIn('cancelled',editor.status.text())

    def test_worker_queue_discards_obsolete_queued_decode(self):
        editor=self.editor
        editor._model_pool = Mock()
        editor._model_signals = Mock()
        editor.mode='models'
        editor.current='characters/a.skinm'
        # Bypass headless synchronous branch to ensure heavy work is queued.
        with patch.dict(os.environ,{'QT_QPA_PLATFORM':'windows'}):
            editor._load_model_background('characters/a.skinm')
        editor._model_pool.clear.assert_called_once()
        editor._model_pool.start.assert_called_once()
        self.assertTrue(editor._model_elapsed_timer.isActive())
