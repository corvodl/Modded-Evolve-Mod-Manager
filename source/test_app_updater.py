"""Updater security, version gating, and immutable personal Data regression tests."""
from __future__ import annotations
import hashlib
import io
import os
import subprocess
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import app_updater as u


COMMIT = 'a' * 40
OLD = 'b' * 40


def zip_bytes(*, extra=None, commit=COMMIT):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        files = {
            'EvolveModManager/EvolveModManager.exe': b'new program',
            'EvolveModManager/EvolveModWorker.exe': b'new worker',
            'EvolveModManager/_internal/python311.dll': b'runtime bytes',
            'EvolveModManager/BUILD_COMMIT.txt': commit.encode(),
            'EvolveModManager/BUILD_CHANNEL.txt': b'main',
            'EvolveModManager/START-HERE.txt': b'help',
        }
        files.update(extra or {})
        for name, value in files.items(): z.writestr(name, value)
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class UpdateTests(unittest.TestCase):
    def test_release_requires_main_head_exactly(self):
        archive = zip_bytes()
        sha = hashlib.sha256(archive).hexdigest()
        manifest = {'schema': 1, 'commit': COMMIT, 'version': '2.10.0',
                    'sha256': sha, 'bytes': len(archive), 'asset': u.RELEASE_ASSET}
        release = {'assets': [
            {'name': u.RELEASE_ASSET, 'browser_download_url': f'https://github.com/{u.REPO}/releases/download/Main/{u.RELEASE_ASSET}',
             'size': len(archive), 'digest': 'sha256:' + sha},
            {'name': u.MANIFEST_ASSET, 'browser_download_url': f'https://github.com/{u.REPO}/releases/download/Main/{u.MANIFEST_ASSET}'},
        ]}
        def mocked(url):
            if url.endswith('/branches/main'): return json.dumps({'commit': {'sha': COMMIT}}).encode()
            if url.endswith('/releases/tags/Main'): return json.dumps(release).encode()
            return json.dumps(manifest).encode()
        with patch.object(u, '_get', side_effect=mocked):
            self.assertIsNone(u.discover(COMMIT))
            self.assertEqual(u.discover(OLD).commit, COMMIT)
            manifest['commit'] = OLD
            self.assertIsNone(u.discover(OLD))
            manifest['commit'] = COMMIT
            release['assets'][0]['digest'] = 'sha256:' + ('0' * 64)
            self.assertIsNone(u.discover(OLD))
            del release['assets'][1]
            self.assertIsNone(u.discover(OLD))

    def test_prepare_zip_hash_commit_and_reject_data(self):
        archive = zip_bytes()
        url = f'https://github.com/{u.REPO}/releases/download/Main/{u.RELEASE_ASSET}'
        update = u.Update(COMMIT, 'v2.10', hashlib.sha256(archive).hexdigest(), url, len(archive))
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            installed = base / 'app'
            (installed / 'Data' / 'Projects').mkdir(parents=True)
            (installed / 'Data' / 'Projects' / 'secret.pak').write_bytes(b'secret')
            with patch.object(u.urllib.request, 'urlopen', return_value=Response(archive, url)):
                prepared = u.download_and_prepare(update, base/'scratch')
            self.assertEqual(u.installed_commit(prepared), COMMIT)
            self.assertEqual((installed/'Data'/'Projects'/'secret.pak').read_bytes(), b'secret')
            self.assertFalse((prepared/'Data').exists())
            with patch.object(u.urllib.request, 'urlopen', return_value=Response(archive, url)):
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    u.download_and_prepare(u.Update(COMMIT, 'v2.10', '0'*64, url, len(archive)), base/'wrong')
            with patch.object(u.urllib.request, 'urlopen', return_value=Response(archive, url)):
                with self.assertRaisesRegex(ValueError, 'commit'):
                    u.download_and_prepare(u.Update(OLD, 'v2.10', update.sha256, url, len(archive)), base/'different')

    def test_download_and_extract_emit_progress_without_modifying_data(self):
        archive = zip_bytes(extra={'EvolveModManager/_internal/asset.dat': b'x' * 4000})
        url = f'https://github.com/{u.REPO}/releases/download/Main/{u.RELEASE_ASSET}'
        update = u.Update(COMMIT, 'v2.10.2', hashlib.sha256(archive).hexdigest(), url, len(archive))
        progress = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(u.urllib.request, 'urlopen', return_value=Response(archive, url)):
                staged = u.download_and_prepare(update, Path(temp) / 'scratch',
                                                progress=lambda name, n, total: progress.append((name, n, total)))
            self.assertEqual((staged / '_internal' / 'asset.dat').read_bytes(), b'x' * 4000)
        phases = [name for name, _, _ in progress]
        self.assertIn('Downloading', phases)
        self.assertIn('Verifying', phases)
        self.assertIn('Extracting', phases)
        self.assertEqual(progress[0], ('Downloading', 0, len(archive)))
        self.assertIn(('Downloading', len(archive), len(archive)), progress)
        self.assertEqual(progress[-1], ('Ready', 1, 1))
        for stage in ('Downloading', 'Extracting'):
            values = [n for phase, n, _ in progress if phase == stage]
            self.assertEqual(values, sorted(values))

    def test_detached_installer_has_visible_progress_and_restart(self):
        script = u.APPLY_PS1
        self.assertIn('System.Windows.Forms.ProgressBar', script)
        self.assertIn('Set-UpdateProgress', script)
        self.assertIn('WaitForExit(30000)', script)
        self.assertIn('System.Diagnostics.ProcessStartInfo', script)
        self.assertIn('EVOLVE_MANAGER_UPDATE_ACK', script)
        self.assertIn('ROLLBACK COMPLETE', script)
        self.assertIn('Progress UI warning', script)
        self.assertIn('restoring previous version', script)
        self.assertNotIn("'Data'", script)
        self.assertIn('CREATE_NO_WINDOW', Path(u.__file__).read_text(encoding='utf-8'))

    def test_zip_path_traversal_and_sensitive_files_refused(self):
        for bad in ('EvolveModManager/../../Data/key.pem',
                    'EvolveModManager/Data/Projects/file.pak',
                    'EvolveModManager/_internal/secret.pak',
                    'EvolveModManager/evil.dll',
                    'EvolveModManager/_internal/../Data/config'):
            with self.subTest(bad=bad):
                with zipfile.ZipFile(io.BytesIO(zip_bytes(extra={bad: b'attack'}))) as archive:
                    with self.assertRaises(ValueError):u._validated_zip_members(archive)
        with zipfile.ZipFile(io.BytesIO(zip_bytes(extra={'EvolveModManager/_internal/normal.dll': b'dll'}))) as archive:
            self.assertGreaterEqual(len(u._validated_zip_members(archive)), 5)

    def test_no_install_without_windows_and_commit_stamp(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.assertIsNone(u.installed_commit(base))
            with patch.object(u.os, 'name', 'posix'):
                with self.assertRaisesRegex(OSError, 'Windows'):
                    u.launch_apply(u.Update(COMMIT, 'v2.10', '0'*64, 'https://github.com/', 1), base, base, 10)
        self.assertIn('$Backup', u.APPLY_PS1)
        self.assertIn('Move-Item', u.APPLY_PS1)
        self.assertNotIn("'Data'", u.APPLY_PS1)

    def test_main_release_workflow_includes_manifest_last(self):
        workflow = Path(__file__).resolve().parent.parent/'.github'/'workflows'/'windows-release.yml'
        if workflow.is_file():
            text = workflow.read_text(encoding='utf-8')
            self.assertLess(text.index('gh release upload Main "dist/EvolveModManager-Windows.zip"'),
                            text.index('gh release upload Main "dist/update-manifest.json"'))
            self.assertIn('Get-FileHash', text)
            self.assertIn('github.sha', text)


    def test_acknowledgement_restricted_to_updater_scratch(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            app = base / 'app'
            app.mkdir()
            (app / 'BUILD_COMMIT.txt').write_text(COMMIT)
            scratch = base / 'EvolveModManagerUpdates' / 'unique'
            scratch.mkdir(parents=True)
            ack = scratch / 'startup-ok-123.txt'
            with patch.dict(os.environ, {'LOCALAPPDATA': str(base),
                                         'EVOLVE_MANAGER_UPDATE_ACK': str(ack)}):
                u.report_startup_ready(app)
            self.assertEqual(ack.read_text().strip(), COMMIT)
            outside = base / 'Data' / 'nope.txt'
            with patch.dict(os.environ, {'LOCALAPPDATA': str(base),
                                         'EVOLVE_MANAGER_UPDATE_ACK': str(outside)}):
                u.report_startup_ready(app)
            self.assertFalse(outside.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell installer required')
    def test_windows_apply_rolls_back_after_invalid_new_executable(self):
        # Actual PowerShell execution on the Windows CI runner, rather than
        # merely searching installer source for rollback commands.
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target, staged = base / 'installed', base / 'scratch' / 'extracted' / 'EvolveModManager'
            target.mkdir(parents=True)
            staged.mkdir(parents=True)
            (target / 'EvolveModManager.exe').write_bytes(b'previous executable')
            (target / 'EvolveModWorker.exe').write_bytes(b'previous worker')
            (target / '_internal').mkdir()
            (target / '_internal' / 'old.bin').write_bytes(b'prior runtime')
            (target / 'Data' / 'Projects').mkdir(parents=True)
            (target / 'Data' / 'Projects' / 'save.dat').write_bytes(b'personal data')
            (staged / 'EvolveModManager.exe').write_bytes(b'invalid replacement')
            (staged / 'EvolveModWorker.exe').write_bytes(b'new worker')
            (staged / '_internal').mkdir()
            (staged / '_internal' / 'new.bin').write_bytes(b'new runtime')
            (staged / 'BUILD_COMMIT.txt').write_text(COMMIT)
            (staged / 'BUILD_CHANNEL.txt').write_text('main')
            script = base / 'scratch' / 'apply.ps1'
            script.write_text(u.APPLY_PS1, encoding='utf-8-sig')
            done = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA',
                 '-ExecutionPolicy', 'Bypass', '-File', str(script),
                 '-TargetDir', str(target), '-SourceDir', str(staged),
                 '-ManagerPid', '2147483647', '-ExpectedCommit', COMMIT],
                capture_output=True, text=True, timeout=90)
            self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn('ROLLBACK COMPLETE', done.stdout + done.stderr)
            self.assertEqual((target / 'EvolveModManager.exe').read_bytes(), b'previous executable')
            self.assertEqual((target / 'EvolveModWorker.exe').read_bytes(), b'previous worker')
            self.assertEqual((target / '_internal' / 'old.bin').read_bytes(), b'prior runtime')
            self.assertFalse((target / '_internal' / 'new.bin').exists())
            self.assertEqual((target / 'Data' / 'Projects' / 'save.dat').read_bytes(), b'personal data')


if __name__ == '__main__': unittest.main()
