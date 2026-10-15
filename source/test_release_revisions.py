"""Patch-revision release and single-folder updater regression tests."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_updater as u
from release_revision import next_revision
from test_app_updater import Response, zip_bytes

SHA = 'a' * 40
DIGEST = 'c' * 64


class PatchRevisionTests(unittest.TestCase):
    def test_revision_selection_is_monotonic_and_retains_immutable_milestones(self):
        self.assertEqual(next_revision('1.1.18-beta.51'), (0, 'milestone'))
        old = {'schema': 1, 'version': '1.1.18-beta.50'}
        self.assertEqual(next_revision('1.1.18-beta.50', old), (1, 'patch'))
        second = {'schema': 2, 'version': '1.1.18-beta.50', 'revision': 3}
        self.assertEqual(next_revision('1.1.18-beta.50', second), (4, 'patch'))
        self.assertEqual(next_revision('1.1.18-beta.51', second), (0, 'milestone'))
        with self.assertRaisesRegex(ValueError, 'requires an existing'):
            next_revision('1.1.18-beta.51', second, mode='patch')
        with self.assertRaisesRegex(ValueError, 'exists'):
            next_revision('1.1.18-beta.50', second, mode='milestone')
        with self.assertRaisesRegex(ValueError, 'older'):
            next_revision('1.1.18-beta.49', second)
        with self.assertRaises(ValueError):
            next_revision('1.1.18-beta.50', {'schema': 2,
                          'version': '1.1.18-beta.50', 'revision': True})

    def test_schema1_migration_and_revision2_filenames(self):
        legacy = {'schema': 1, 'version': '1.1.17', 'asset': u.RELEASE_ASSET}
        self.assertEqual(u._validated_asset_name(legacy),
                         (u.RELEASE_ASSET, 0, 'milestone'))
        patch_version = '1.1.18-beta.50'
        manifest = {'schema': 2, 'version': patch_version, 'revision': 2,
                    'release_type': 'patch',
                    'asset': f'EvolveModManager-Windows-{patch_version}-p2.zip',
                    'installer': f'EvolveModManager-Setup-{patch_version}-p2.exe'}
        self.assertEqual(u._validated_asset_name(manifest)[1:], (2, 'patch'))
        for bad in (False, -1, 100000, '2'):
            with self.subTest(bad=bad):
                manifest['revision'] = bad
                with self.assertRaises(ValueError):
                    u._validated_asset_name(manifest)
        manifest['revision'] = 2
        manifest['asset'] = u.RELEASE_ASSET
        with self.assertRaisesRegex(ValueError, 'asset'):
            u._validated_asset_name(manifest)

    def test_choose_latest_rejects_same_milestone_downgrade(self):
        patch1 = u.Update(SHA, '1.1.18-beta.50', DIGEST, 'url', 10, 'beta', 1, 'patch')
        patch2 = u.Update('b' * 40, '1.1.18-beta.50', DIGEST, 'url', 10,
                          'beta', 2, 'patch')
        self.assertIs(u.choose_latest([patch1, patch2], '1.1.18-beta.50',
                                     installed_revision=1), patch2)
        self.assertIsNone(u.choose_latest([patch1], '1.1.18-beta.50',
                                          installed_revision=2))
        self.assertIsNone(u.choose_latest([patch2], '1.1.18-beta.51',
                                          installed_revision=0))

    def test_channel_manifest_patch_is_verified_and_missing_rejected(self):
        version = '1.1.18-beta.50'
        filename = f'EvolveModManager-Windows-{version}-p1.zip'
        installer = f'EvolveModManager-Setup-{version}-p1.exe'
        base = f'https://github.com/{u.REPO}/releases/download/Beta/'
        manifest = {'schema': 2, 'channel': 'beta', 'commit': SHA,
                    'version': version, 'revision': 1, 'release_type': 'patch',
                    'asset': filename, 'installer': installer,
                    'sha256': DIGEST, 'bytes': 320}
        release = {'prerelease': True, 'assets': [
            {'name': filename, 'browser_download_url': base + filename,
             'size': 320, 'digest': 'sha256:' + DIGEST},
            {'name': u.MANIFEST_ASSET,
             'browser_download_url': base + u.MANIFEST_ASSET}]}
        def get(url):
            if url.endswith('/branches/beta'):
                return json.dumps({'commit': {'sha': SHA}}).encode()
            if url.endswith('/releases/tags/Beta'):
                return json.dumps(release).encode()
            if url.endswith('update-manifest.json'):
                return json.dumps(manifest).encode()
            raise AssertionError(url)
        with patch.object(u, '_get', side_effect=get):
            update = u.discover_channel('beta')
            self.assertEqual((update.revision, update.release_type), (1, 'patch'))
            manifest['revision'] = 2
            self.assertIsNone(u.discover_channel('beta'))
            manifest['revision'] = 1
            release['assets'][0]['digest'] = 'sha256:' + '0' * 64
            self.assertIsNone(u.discover_channel('beta'))

    def test_installed_revision_and_app_folder_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'EvolveModManager'
            root.mkdir()
            self.assertEqual(u.installed_revision(root), 0)
            stamp = root / u.REVISION_STAMP
            stamp.write_text('9')
            self.assertEqual(u.installed_revision(root), 9)
            stamp.write_text('-1')
            self.assertEqual(u.installed_revision(root), 0)
            self.assertEqual(u.update_workspace(root), root / 'UpdateWork')
            self.assertNotIn('EvolveModManagerUpdates', str(u.update_workspace(root)))

    def test_workflows_preserve_tagged_versions_and_stage_revision(self):
        root = Path(__file__).resolve().parent.parent
        for file, channel in [('windows-beta-release.yml', 'Beta'),
                              ('windows-release.yml', 'Main')]:
            text = (root / '.github/workflows' / file).read_text('utf-8')
            self.assertIn('source/release_revision.py', text)
            self.assertIn('EVOLVE_RELEASE_REVISION', text)
            self.assertIn('release create $versionTag', text)
            self.assertIn('already exists; refusing to overwrite', text)
            self.assertIn('gh release upload ' + channel + " 'dist/update-manifest.json'", text)
            self.assertLess(text.index('gh release upload ' + channel + ' "dist/$file"'),
                            text.index('gh release upload ' + channel + " 'dist/update-manifest.json'"))
            self.assertIn('schema=2', text)
        build = (root / 'Build-Windows-EXE.cmd').read_text('utf-8')
        self.assertIn('BUILD_REVISION.txt', build)
        installer = (root / 'installer/EvolveModManager.iss').read_text('utf-8')
        self.assertIn('BUILD_REVISION.txt', installer)
        updater = (root / 'source/app_updater.py').read_text('utf-8')
        self.assertNotIn("EvolveModManagerUpdates'", updater)
        self.assertNotIn("'.update-backup-'", updater)


if __name__ == '__main__':
    unittest.main()
