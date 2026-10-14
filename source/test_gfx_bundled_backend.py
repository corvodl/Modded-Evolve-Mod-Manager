"""Bundled JPEXS + Temurin discovery and distribution gates."""
from pathlib import Path
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import app_runtime
from gfx_preview_backend import (
    bundled_ffdec_paths, ffdec_command, preferred_ffdec_override, GfxError
)
from gfx_edit_backend import ffdec_gui_command


class BundledFFDecTests(unittest.TestCase):
    def test_private_runtime_launches_renderer_and_non_headless_gui(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "third_party" / "FFDec"
            java = base / "jre" / "bin" / "java.exe"
            javaw = base / "jre" / "bin" / "javaw.exe"
            jar = base / "ffdec.jar"
            for file in (java, javaw, jar, base / "BUNDLED-READY.txt"):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"fixture")
            with patch.object(app_runtime, "BUNDLE", root), patch.dict(
                    os.environ, {}, clear=True):
                self.assertEqual(bundled_ffdec_paths(), (java, javaw, jar))
                self.assertEqual(ffdec_command(),
                                 (str(java), "-Djava.awt.headless=true",
                                  "-jar", str(jar)))
                self.assertEqual(ffdec_gui_command(None),
                                 (str(javaw), "-jar", str(jar)))
                self.assertIsNone(
                    preferred_ffdec_override("C:/old/removed/ffdec.jar"))
                self.assertEqual(ffdec_command()[-1], str(jar))
                other = root / "manual" / "ffdec.jar"
                other.parent.mkdir()
                other.write_bytes(b"other")
                self.assertEqual(ffdec_command(other),
                                 (str(java), "-Djava.awt.headless=true",
                                  "-jar", str(other)))
            java.unlink()
            self.assertIsNone(bundled_ffdec_paths(bundle=root))

    def test_missing_companions_are_reported_not_downloaded(self):
        with TemporaryDirectory() as temp, patch.object(
                app_runtime, "BUNDLE", Path(temp)), patch.dict(
                os.environ, {}, clear=True):
            self.assertIsNone(bundled_ffdec_paths())
            with self.assertRaisesRegex(GfxError, "Repair or reinstall"):
                ffdec_command()
            with self.assertRaisesRegex(GfxError, "missing or unsafe"):
                ffdec_command(Path(temp) / "not_here.jar")

    def test_spec_build_installer_and_licenses_must_be_included(self):
        root = Path(__file__).resolve().parent.parent
        spec = (root / "source" / "EvolveModManager.spec").read_text()
        batch = (root / "Build-Windows-EXE.cmd").read_text()
        prepare = (root / "source" / "Prepare-FFDec.ps1").read_text()
        installer = (root / "installer" / "EvolveModManager.iss").read_text()
        smoke = (root / "source" / "packaging_smoke.py").read_text()
        self.assertIn("third_party/FFDec", spec)
        self.assertIn("Prepare-FFDec.ps1", batch)
        self.assertIn("OpenJDK21U-jdk-sources_21.0.12_8.tar.gz", prepare)
        self.assertIn("FFDec-source-b2ef2d3.zip", prepare)
        self.assertIn("35f4930eb7c380afe66f2117f90b006deac0631473ad7500bb39c78f68645ecd", prepare)
        self.assertIn("b8aa18fef5edb69bee8618f99677d66d0873d22cb40d974c15ac9ffcdecf73ba", prepare)
        self.assertIn("jre/bin/java.exe", spec)
        self.assertIn("EvolveModManager\\_internal\\*", installer)
        self.assertIn("bundled_ffdec_paths", smoke)


if __name__ == "__main__":
    unittest.main()
