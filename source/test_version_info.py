"""Release version is read from the packaged manifest and displayed in the GUI."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from version_info import app_version


class VersionManifestTests(unittest.TestCase):
    def test_release_manifest_controls_the_ui(self):
        from release_notes import RELEASE_NOTES
        self.assertEqual(app_version(), 'v' + RELEASE_NOTES[0][0])
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'VERSION.txt'
            path.write_text('1.0.1 - Maintenance Release\n', encoding='utf-8')
            self.assertEqual(app_version(path), 'v1.0.1')
            path.write_text('bad', encoding='utf-8')
            self.assertEqual(app_version(path), 'Development build')
        self.assertEqual(app_version(Path(folder) / 'missing'), 'Development build')
        (Path(folder)/'VERSION.txt').write_text('1.1.18-beta.1 - Preview')
        self.assertEqual(app_version(Path(folder)/'VERSION.txt'), 'v1.1.18-beta.1')

    def test_public_notes_are_brief_current_and_shared_with_readme(self):
        from release_notes import RELEASE_NOTES, markdown
        self.assertEqual('v' + RELEASE_NOTES[0][0], app_version())
        for version, notes in RELEASE_NOTES:
            self.assertTrue(1 <= len(notes) <= 3, version)
            self.assertTrue(all(len(note) <= 120 for note in notes), version)
        root = Path(__file__).resolve().parent.parent
        self.assertIn(markdown().strip(), (root/'README.md').read_text('utf-8'))
        public = markdown() + (root/'source'/'VERSION.txt').read_text('utf-8')
        self.assertNotIn('developer', public.lower())
        self.assertNotIn('access code', public.lower())

    def test_access_verifier_rejects_invalid_and_oversized_input(self):
        from developer_access import verify_code
        for code in (None, '', 'incorrect', 'x' * 257):
            self.assertFalse(verify_code(code))


if __name__ == '__main__':
    unittest.main()
