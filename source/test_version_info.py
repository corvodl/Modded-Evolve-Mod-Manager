"""Release version is read from the packaged manifest and displayed in the GUI."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from version_info import app_version, footer_version


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

    def test_footer_displays_milestone_without_patch_revision(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'VERSION.txt'
            revision = Path(folder) / 'BUILD_REVISION.txt'
            path.write_text('1.2.0 - Stable Release', encoding='utf-8')
            revision.write_text('2', encoding='ascii')
            self.assertEqual(app_version(path), 'v1.2.0 · Patch 2')
            self.assertEqual(footer_version(path), 'v1.2.0')
            path.write_text('1.1.18-beta.50 - Beta', encoding='utf-8')
            self.assertEqual(app_version(path), 'v1.1.18-beta.50 · Patch 2')
            self.assertEqual(footer_version(path), 'v1.1.18-beta.50')
            revision.write_text('0', encoding='ascii')
            self.assertEqual(footer_version(path), 'v1.1.18-beta.50')

    def test_stable_notes_match_current_release_and_hide_beta_history(self):
        from release_notes import RELEASE_NOTES, markdown
        self.assertEqual(('1.2.6',), tuple(v for v, _ in RELEASE_NOTES[:1]))
        self.assertEqual('v' + RELEASE_NOTES[0][0], app_version())
        for version, notes in RELEASE_NOTES:
            self.assertTrue(1 <= len(notes) <= (12 if version.startswith(('1.2.', '1.3.')) else 3), version)
            self.assertTrue(all(len(note) <= 185 for note in notes), version)
            self.assertNotIn('-beta.', version)
        root = Path(__file__).resolve().parent.parent
        readme = (root/'README.md').read_text('utf-8')
        version, notes = RELEASE_NOTES[0]
        self.assertIn('## Latest Stable — v' + version, readme)
        section = readme.split('## Latest Stable — v' + version, 1)[1].split(
            '## Download and install', 1)[0]
        for note in notes:
            self.assertIn('- ' + note, section)
        self.assertNotIn('## Latest Beta', readme)
        self.assertNotIn('1.1.18-beta.', readme)
        self.assertNotIn('1.1.18-beta.', markdown())
        public = markdown() + (root/'source'/'VERSION.txt').read_text('utf-8')
        self.assertNotIn('developer', public.lower())
        self.assertNotIn('access code', public.lower())

    def test_access_verifier_rejects_invalid_and_oversized_input(self):
        from developer_access import verify_code
        for code in (None, '', 'incorrect', 'x' * 257):
            self.assertFalse(verify_code(code))


if __name__ == '__main__':
    unittest.main()
