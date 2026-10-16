"""Synthetic coverage for offline Blender extension install; Blender not required."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import zipfile

import blender_addon_installer as installer


def make_package(root, *, manifest_id="evolve_animation_browser"):
    archive = root / installer.PACKAGE_NAME
    manifest = (
        'schema_version = "1.0.0"\n'
        'id = "%s"\n'
        'version = "1.1.0"\n'
        'type = "add-on"\n' % manifest_id)
    with zipfile.ZipFile(archive, "w") as zip_file:
        for name in sorted(installer.PACKAGE_FILES):
            zip_file.writestr(name, manifest if name == "blender_manifest.toml"
                              else "test fixture")
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


class BlenderAddonInstallerTests(TestCase):
    def test_sha_pinned_manifest_and_exact_members_required(self):
        with TemporaryDirectory() as tmp:
            archive, digest = make_package(Path(tmp))
            with patch.object(installer, "PACKAGE_SHA256", digest):
                self.assertEqual(installer.validate_package(archive), archive.resolve())
                archive.write_bytes(archive.read_bytes() + b"tampered")
                with self.assertRaisesRegex(ValueError, "official"):
                    installer.validate_package(archive)

    def test_substituted_addon_cannot_be_installed_even_with_matching_hash(self):
        with TemporaryDirectory() as tmp:
            archive, digest = make_package(Path(tmp), manifest_id="evil_addon")
            with patch.object(installer, "PACKAGE_SHA256", digest):
                with self.assertRaisesRegex(ValueError, "not Evolve Animation"):
                    installer.validate_package(archive)

    def test_unknown_entries_are_rejected_even_if_archive_hash_matches(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _ = make_package(root)
            with zipfile.ZipFile(archive, "a") as z:
                z.writestr("../untrusted.py", "bad")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(installer, "PACKAGE_SHA256", digest):
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    installer.validate_package(archive)

    def test_local_app_or_downloads_offline_package_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app" / "source"
            app.mkdir(parents=True)
            home = root / "home"
            (home / "Downloads").mkdir(parents=True)
            self.assertIsNone(installer.find_addon_package(application=app, home=home))
            addon = home / "Downloads" / installer.PACKAGE_NAME
            addon.write_bytes(b"zip content")
            self.assertEqual(installer.find_addon_package(application=app, home=home), addon)
            (app / "third_party").mkdir()
            bundled = app / "third_party" / installer.PACKAGE_NAME
            bundled.write_bytes(b"zip content")
            self.assertEqual(installer.find_addon_package(application=app, home=home), bundled)

    def test_blender_version_check_and_exact_native_install_command(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "blender.exe"
            executable.write_text("test executable")
            archive, digest = make_package(root)
            commands = []
            def fake_run(args, **kwargs):
                commands.append(tuple(args))
                if args[1:] == ["--version"]:
                    return SimpleNamespace(returncode=0, stdout="Blender 4.3.2\n",
                                           stderr="")
                return SimpleNamespace(returncode=0, stdout="installed", stderr="")
            with patch.object(installer, "PACKAGE_SHA256", digest):
                result = installer.install_addon(executable, archive, runner=fake_run)
            self.assertIn("Installed Evolve Animation Browser", result)
            self.assertEqual(commands[-1], (
                str(executable.resolve()), "--command", "extension",
                "install-file", "-r", "user_default", "-e", str(archive.resolve())))

    def test_unsupported_blender_fails_before_installing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            blender = root / "blender.exe"
            blender.write_text("fixture")
            archive, digest = make_package(root)
            called = []
            def fake_run(args, **kwargs):
                called.append(args)
                return SimpleNamespace(returncode=0, stdout="Blender 4.1.9\n", stderr="")
            with patch.object(installer, "PACKAGE_SHA256", digest):
                with self.assertRaisesRegex(ValueError, "4.2"):
                    installer.install_addon(blender, archive, runner=fake_run)
            self.assertEqual(len(called), 1)

    def test_failed_install_is_reported_not_claimed_successful(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            blender = root / "blender.exe"
            blender.write_text("fixture")
            archive, digest = make_package(root)
            def fake_run(args, **kwargs):
                if args[1:] == ["--version"]:
                    return SimpleNamespace(returncode=0, stdout="Blender 4.5.0", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="repo missing")
            with patch.object(installer, "PACKAGE_SHA256", digest):
                with self.assertRaisesRegex(RuntimeError, "repo missing"):
                    installer.install_addon(blender, archive, runner=fake_run)

    def test_status_requires_exact_user_extension_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blender Foundation" / "Blender" / "4.3"
            manifest = root / "extensions" / "user_default" / installer.PACKAGE_ID / "blender_manifest.toml"
            self.assertEqual(installer.installed_versions(appdata=Path(tmp)), ())
            manifest.parent.mkdir(parents=True)
            manifest.write_text('id = "evolve_animation_browser"\nversion = "1.1.0"')
            self.assertEqual(installer.installed_versions(appdata=Path(tmp)),
                             (("4.3", "1.1.0"),))


if __name__ == "__main__":
    import unittest
    unittest.main()
