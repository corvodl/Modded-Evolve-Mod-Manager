"""User-visible UI wording must come only from packaged developer resources."""
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import ui_copy

class WordingTests(unittest.TestCase):
    def test_bundled_defaults_not_overridden_by_player_sidecar(self):
        with TemporaryDirectory() as tmp:
            user = Path(tmp) / 'ui_text.json'
            user.write_text('{"play_button":"Launch My Mods"}', encoding='utf-8')
            with patch('os.getcwd', return_value=tmp):
                labels, warning = ui_copy.load_text()
            self.assertFalse(warning)
            self.assertEqual(labels['play_button'], 'Play With Mods')

    def test_invalid_developer_text_fails_gracefully(self):
        with TemporaryDirectory() as tmp:
            user = Path(tmp) / 'ui_text.json'
            user.write_text('{broken', encoding='utf-8')
            with patch.object(ui_copy, 'BUNDLED_TEXT', user):
                labels, warning = ui_copy.load_text()
            self.assertEqual(labels, {})
            self.assertIn('Bundled UI labels', warning)

    def test_main_ui_references_existing_keys(self):
        labels = json.loads(ui_copy.BUNDLED_TEXT.read_text(encoding='utf-8'))
        root = ui_copy.BUNDLED_TEXT.parent
        for filename in ('pak_manager_gui.py','workspace_editor.py'):
            tree = ast.parse((root / filename).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 't' and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                    self.assertIn(node.args[0].value, labels, (filename, node.lineno))

if __name__ == '__main__':
    unittest.main()
