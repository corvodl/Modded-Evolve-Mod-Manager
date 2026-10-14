"""Guard against accidentally shipping FFDec and Java with the Windows app."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from release_dependency_policy import (
    external_dependency_in_path, assert_external_dependencies_absent,
    check_app_folder,
)
from publish_release import publish


class ExternalRuntimePackageTests(unittest.TestCase):
    def test_forbidden_payloads_are_detected(self):
        for relative in (
            "EvolveModManager/_internal/ffdec.jar",
            "EvolveModManager/_internal/FFDEC-CLI.exe",
            "EvolveModManager/_internal/FFDec_26.3.0.zip",
            "EvolveModManager/_internal/java.exe",
            "EvolveModManager/_internal/jre/bin/server/jvm.dll",
            "EvolveModManager/_internal/temurin-21/bin/java.exe",
            "EvolveModManager/_internal/jdk-21/bin/javaw.exe",
        ):
            with self.subTest(path=relative):
                self.assertIsNotNone(external_dependency_in_path(relative))
                with self.assertRaisesRegex(ValueError, "FFDec or Java"):
                    assert_external_dependencies_absent((relative,))

    def test_manager_integration_code_is_not_forbidden(self):
        for relative in (
            "_internal/qt_ffdec_prompt.py",
            "_internal/gfx_preview_backend.py",
            "_internal/release_dependency_policy.py",
            "Docs/gfx-preview.md",
            "EvolveModManager.exe",
        ):
            self.assertIsNone(external_dependency_in_path(relative))
        assert_external_dependencies_absent((
            "_internal/qt_ffdec_prompt.py",
            "Docs/gfx-preview.md",
        ))

    def test_release_zip_refuses_external_runtime_binaries(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            built = root / "build"
            built.mkdir()
            for name in ("EvolveModManager.exe", "EvolveModWorker.exe",
                         "BUILD_COMMIT.txt", "BUILD_CHANNEL.txt"):
                (built/name).write_bytes(b"fixture")
            internal = built / "_internal"
            internal.mkdir()
            (internal/"qt_ffdec_prompt.py").write_bytes(b"fixture")
            check_app_folder(built)
            release, archive = publish(built, root / "dist")
            self.assertTrue(release.is_dir())
            with zipfile.ZipFile(archive) as z:
                self.assertIn("EvolveModManager/_internal/qt_ffdec_prompt.py",
                              z.namelist())
                assert_external_dependencies_absent(z.namelist())
            (internal/"jre").mkdir()
            (internal/"jre"/"java.exe").write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "FFDec or Java"):
                publish(built, root / "other-dist")


if __name__ == "__main__":
    unittest.main()
