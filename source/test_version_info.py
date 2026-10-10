"""Release version is read from the packaged manifest and displayed in the GUI."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from version_info import app_version


class VersionManifestTests(unittest.TestCase):
    def test_release_manifest_controls_the_ui(self):
        self.assertEqual(app_version(), 'v1.0.1')
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'VERSION.txt'
            path.write_text('1.0.1 - Maintenance Release\n', encoding='utf-8')
            self.assertEqual(app_version(path), 'v1.0.1')
            path.write_text('bad', encoding='utf-8')
            self.assertEqual(app_version(path), 'Development build')
        self.assertEqual(app_version(Path(folder) / 'missing'), 'Development build')


if __name__ == '__main__':
    unittest.main()
