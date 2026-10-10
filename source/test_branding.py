"""Branding/developer-only copy regression checks; no Windows needed."""
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
from publish_release import publish

ROOT = Path(__file__).resolve().parent
KIT = ROOT.parent

class BrandingTests(unittest.TestCase):
    def test_icon_files_and_windows_multisize_resource(self):
        png = (ROOT / 'assets' / 'hunt.png').read_bytes()
        ico = (ROOT / 'assets' / 'hunt.ico').read_bytes()
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(ico[:4], b'\x00\x00\x01\x00')
        count = struct.unpack_from('<H', ico, 4)[0]
        sizes = set()
        for i in range(count):
            width, height, *_ = struct.unpack_from('<BBBBHHII', ico, 6 + 16*i)
            self.assertEqual(width, height)
            sizes.add(width or 256)
        self.assertTrue({16, 32, 48, 256}.issubset(sizes), sizes)

    def test_branded_gui_and_worker(self):
        spec = (ROOT / 'EvolveModManager.spec').read_text(encoding='utf-8')
        self.assertEqual(spec.count("icon=str(root/'assets'/'hunt.ico')"), 2)
        gui = (ROOT / 'pak_manager_gui.py').read_text(encoding='utf-8')
        self.assertIn("ROOT/'assets'/'hunt.png'", gui)
        self.assertIn('SetCurrentProcessExplicitAppUserModelID', gui)

    def test_no_player_text_editor(self):
        gui = (ROOT / 'pak_manager_gui.py').read_text(encoding='utf-8')
        self.assertNotIn('edit_ui_text', gui)
        self.assertNotIn('edit_wording_button', gui)
        self.assertNotIn('ui_text_note', gui)
        self.assertNotIn('editable_text_path', (ROOT / 'ui_copy.py').read_text())
        self.assertTrue((KIT/'DEVELOPER-UI-TEXT.txt').is_file())
        for p in (KIT/'Docs').glob('*.txt'):
            self.assertNotIn('Edit Button Text', p.read_text(encoding='utf-8'), p.name)

    def test_release_omits_editable_player_json_and_dev_docs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            built = base/'app'
            internal = built/'_internal'
            docs = built/'Docs'
            internal.mkdir(parents=True)
            docs.mkdir()
            for exe in ('EvolveModManager.exe', 'EvolveModWorker.exe'):
                (built/exe).write_bytes(b'fake exe')
            (internal/'ui_text.json').write_bytes((ROOT/'ui_text.json').read_bytes())
            (built/'BUILD_COMMIT.txt').write_text('a'*40)
            (built/'BUILD_CHANNEL.txt').write_text('main')
            (built/'START-HERE.txt').write_text('Run EvolveModManager.exe')
            (docs/'README-PORTABLE-APP.txt').write_text('Run Set Up Manager')
            release, archive = publish(built, base/'dist')
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                self.assertIn('EvolveModManager/_internal/ui_text.json', names)
                self.assertNotIn('EvolveModManager/ui_text.json', names)
                self.assertFalse(any('DEVELOPER' in name for name in names))
            (built/'ui_text.json').write_text('{"example":"override"}')
            with self.assertRaisesRegex(ValueError, 'Unexpected file'):
                publish(built, base/'otherdist')

if __name__ == '__main__':
    unittest.main()
