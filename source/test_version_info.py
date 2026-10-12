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
        with TemporaryDirectory() as beta_folder:
            path = Path(beta_folder) / 'VERSION.txt'
            path.write_text('1.1.18-beta.1 - Preview')
            self.assertEqual(app_version(path), 'v1.1.18-beta.1')

    def test_public_notes_are_brief_current_and_shared_with_readme(self):
        from release_notes import RELEASE_NOTES, markdown
        self.assertEqual('v' + RELEASE_NOTES[0][0], app_version())
        for version, notes in RELEASE_NOTES:
            self.assertTrue(1 <= len(notes) <= 3, version)
            self.assertTrue(all(len(note) <= 120 for note in notes), version)
        root = Path(__file__).resolve().parent.parent
        readme = (root/'README.md').read_text('utf-8')
        version, notes = RELEASE_NOTES[0]
        self.assertIn('## Latest Beta — v' + version, readme)
        section = readme.split('## Latest Beta — v' + version, 1)[1].split('## Download and install', 1)[0]
        for note in notes:
            self.assertIn('- ' + note, section)
        self.assertNotIn('## Latest Beta — v1.1.18-beta.18', readme)
        self.assertNotIn('## Latest Beta — v1.1.18-beta.19', readme)
        self.assertNotIn('## Latest Beta — v1.1.18-beta.21', readme)
        self.assertNotIn('## Latest Beta — v1.1.18-beta.22', readme)
        self.assertNotIn('## Latest Beta — v1.1.18-beta.23', readme)
        self.assertNotIn('## Latest Beta — v1.1.18-beta.24', readme)
        public = markdown() + (root/'source'/'VERSION.txt').read_text('utf-8')
        self.assertNotIn('developer', public.lower())
        self.assertNotIn('access code', public.lower())

    def test_access_verifier_rejects_invalid_and_oversized_input(self):
        from developer_access import verify_code
        for code in (None, '', 'incorrect', 'x' * 257):
            self.assertFalse(verify_code(code))


if __name__ == '__main__':
    unittest.main()
