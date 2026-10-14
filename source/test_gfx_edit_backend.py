"""GFX editing project/session tests with synthetic, copyright-free assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gfx_edit_backend import (
    create_gfx_session, read_gfx_session, use_edited_gfx_file,
    undo_gfx_session, apply_gfx_session, replace_gfx_resource,
    export_gfx_xml, rebuild_gfx_from_xml, export_gfx_scripts,
    rebuild_gfx_scripts, launch_gfx_gui, GfxError,
)
from gfx_synthetic_fixtures import synthetic_gfx


class GfxEditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "Project"
        path = self.project / "files" / "Libs" / "UI" / "main_menu.gfx"
        path.parent.mkdir(parents=True)
        self.original = synthetic_gfx(scripts=True, exported_names=True)
        path.write_bytes(self.original)
        self.virtual = "Libs/UI/main_menu.gfx"
        self.target = path
        self.sha = hashlib.sha256(self.original).hexdigest()
        (self.project / ".evolve-pak-workspace.json").write_text(json.dumps({
            "entries": [{"path": self.virtual, "format": "raw",
                         "sha256": self.sha}],
        }))
        self.candidate = self.root / "changed.gfx"
        self.modified = synthetic_gfx(width=80, scripts=True, exported_names=True)
        self.candidate.write_bytes(self.modified)

    def test_private_edit_copy_and_approved_project_commit_preserve_original(self):
        snap = create_gfx_session(self.project, self.virtual)
        self.assertEqual(snap.original_sha256, self.sha)
        self.assertEqual(snap.working_file.read_bytes(), self.original)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(snap.changed)
        modified = use_edited_gfx_file(self.project, snap.session, self.candidate)
        self.assertTrue(modified.changed)
        self.assertEqual(modified.edits, 1)
        self.assertEqual(modified.working_file.read_bytes(), self.modified)
        self.assertEqual(self.target.read_bytes(), self.original)
        result = apply_gfx_session(
            self.project, snap.session, expected_sha256=self.sha)
        self.assertTrue(result["changed"])
        self.assertFalse(result["game_modified"])
        self.assertFalse(result["pak_modified"])
        self.assertEqual(self.target.read_bytes(), self.modified)
        backups = list((self.project / "EditorBackups").rglob("main_menu.gfx"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)

    def test_undo_restores_private_original_without_touching_project(self):
        snap = create_gfx_session(self.project, self.virtual)
        use_edited_gfx_file(self.project, snap.session, self.candidate)
        restored = undo_gfx_session(self.project, snap.session)
        self.assertEqual(restored.edits, 0)
        self.assertFalse(restored.changed)
        self.assertEqual(restored.working_file.read_bytes(), self.original)
        self.assertEqual(self.target.read_bytes(), self.original)
        with self.assertRaisesRegex(GfxError, "no changes"):
            apply_gfx_session(self.project, snap.session)

    def test_invalid_movies_and_unsafe_sessions_fail_closed(self):
        snap = create_gfx_session(self.project, self.virtual)
        self.candidate.write_bytes(b"not a gfx")
        with self.assertRaisesRegex(GfxError, "GFX"):
            use_edited_gfx_file(self.project, snap.session, self.candidate)
        self.assertEqual(self.target.read_bytes(), self.original)
        with self.assertRaisesRegex(GfxError, "does not belong"):
            read_gfx_session(self.root, snap.session)
        with self.assertRaisesRegex(GfxError, "unchanged"):
            use_edited_gfx_file(self.project, snap.session, snap.working_file)

    def test_reject_changed_project_after_ffdec_session_opened(self):
        snap = create_gfx_session(self.project, self.virtual)
        use_edited_gfx_file(self.project, snap.session, self.candidate)
        self.target.write_bytes(b"externally modified")
        with self.assertRaisesRegex(GfxError, "changed since"):
            apply_gfx_session(self.project, snap.session,
                              expected_sha256=self.sha)
        self.assertFalse((self.project / "EditorBackups").exists())

    def test_resource_replacement_is_scoped_to_present_character_kind(self):
        snap = create_gfx_session(self.project, self.virtual)
        svg = self.root / "shape.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="80" '
                       'height="50"><rect width="80" height="50" '
                       'fill="lime"/></svg>')
        with self.assertRaisesRegex(GfxError, "does not match"):
            replace_gfx_resource(self.project, snap.session,
                                 "image", 1, svg)
        with self.assertRaisesRegex(GfxError, "does not match"):
            replace_gfx_resource(self.project, snap.session,
                                 "shape", 99, svg)
        with self.assertRaisesRegex(GfxError, "supported image/text/shape"):
            replace_gfx_resource(self.project, snap.session,
                                 "shape", 1, self.candidate)
        def synthesize(args, *, configured, folder, cancelled=None):
            self.assertEqual(args[0], "-replace")
            self.assertEqual(args[3], "1")
            Path(args[2]).write_bytes(self.modified)
        with patch("gfx_edit_backend._ffdec", side_effect=synthesize):
            staged = replace_gfx_resource(
                self.project, snap.session, "shape", 1, svg,
                configured="/mock/ffdec.jar")
        self.assertEqual(staged.edits, 1)
        self.assertEqual(staged.working_file.read_bytes(), self.modified)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_imported_movie_dependencies_are_scoped(self):
        source = synthetic_gfx(imports=True)
        self.target.write_bytes(source)
        import_file = synthetic_gfx()
        relative = "Libs/UI/gfxfontlib_locfont.gfx"
        snap = create_gfx_session(
            self.project, self.virtual, dependencies={relative: import_file})
        self.assertEqual(
            (snap.session / "gfxfontlib_locfont.gfx").read_bytes(),
            import_file)
        with self.assertRaisesRegex(GfxError, "Unreferenced"):
            create_gfx_session(
                self.project, self.virtual,
                dependencies={"Libs/UI/missing.gfx": import_file})

    def test_external_ffdec_gui_does_not_edit_project_before_commit(self):
        snap = create_gfx_session(self.project, self.virtual)
        with patch("gfx_edit_backend.ffdec_gui_command",
                   return_value=("java", "-jar", "ffdec.jar")), patch(
                "gfx_edit_backend.subprocess.Popen") as launch:
            launch.return_value.pid = 123
            self.assertEqual(
                launch_gfx_gui(self.project, snap.session), 123)
        self.assertEqual(launch.call_args.args[0][-1], str(snap.working_file))
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_xml_roundtrip_rebuild_is_edit_session_only(self):
        snap = create_gfx_session(self.project, self.virtual)
        xml = self.root / "movie.xml"
        xml.write_text("<SWF />")
        def fake_xml(args, *, configured, folder, cancelled=None):
            self.assertEqual(args[0], "-xml2swf")
            Path(args[2]).write_bytes(self.modified)
        with patch("gfx_edit_backend._ffdec", side_effect=fake_xml):
            result = rebuild_gfx_from_xml(self.project, snap.session, xml)
        self.assertTrue(result.changed)
        self.assertEqual(self.target.read_bytes(), self.original)

    @unittest.skipUnless(
        __import__("os").environ.get("EVOLVE_GFX_FFDEC"),
        "Pinned FFDec is unavailable; real GFX editing CI runs separately")
    def test_real_ffdec_shape_replace_produces_green_render_and_undo(self):
        import io
        import os
        from PIL import Image
        from gfx_preview_backend import render_gfx
        installed = os.environ["EVOLVE_GFX_FFDEC"]
        snap = create_gfx_session(self.project, self.virtual)
        shape = self.root / "replacement.svg"
        shape.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="64" height="48"><rect width="64" height="48" '
            'fill="#00ff00"/></svg>', encoding="utf-8")
        result = replace_gfx_resource(
            self.project, snap.session, "shape", 1, shape, configured=installed)
        self.assertTrue(result.changed)
        self.assertEqual(self.target.read_bytes(), self.original)
        output = render_gfx(
            result.working_file.read_bytes(),
            virtual=self.virtual, backend=installed)
        with Image.open(io.BytesIO(output)) as image:
            pixel = image.convert("RGBA").getpixel((30, 20))
            self.assertGreater(pixel[1], 200, pixel)
            self.assertLess(pixel[0], 90, pixel)
        undone = undo_gfx_session(self.project, snap.session)
        self.assertFalse(undone.changed)
        self.assertEqual(undone.working_file.read_bytes(), self.original)

    def test_actionscript_sources_export_recompile_without_project_write(self):
        snap = create_gfx_session(self.project, self.virtual)
        dest = self.root / "script-sources"
        def fake_export(args, *, configured, folder, cancelled=None):
            self.assertEqual(args[0:2], ["-export", "script"])
            scripts = Path(args[2])
            scripts.joinpath("scripts").mkdir(parents=True)
            scripts.joinpath("scripts", "main.as").write_text("trace('start')")
        with patch("gfx_edit_backend._ffdec", side_effect=fake_export):
            exported = export_gfx_scripts(
                self.project, snap.session, dest, configured="mock.jar")
        self.assertEqual(exported["count"], 1)
        self.assertTrue((dest / "scripts" / "main.as").exists())
        (dest / "scripts" / "main.as").write_text("trace('new')")
        def fake_import(args, *, configured, folder, cancelled=None):
            self.assertEqual(args[0], "-importScript")
            self.assertTrue(
                Path(args[-1], "scripts", "main.as").is_file())
            Path(args[2]).write_bytes(self.modified)
        with patch("gfx_edit_backend._ffdec", side_effect=fake_import):
            result = rebuild_gfx_scripts(
                self.project, snap.session, dest, configured="mock.jar")
        self.assertEqual(result.edits, 1)
        self.assertTrue(result.changed)
        self.assertEqual(self.target.read_bytes(), self.original)
        (dest / "unknown.bin").write_bytes(b"unsafe")
        with self.assertRaisesRegex(GfxError, "only contain .as"):
            rebuild_gfx_scripts(self.project, snap.session, dest,
                                configured="mock.jar")

    @unittest.skipUnless(
        __import__("os").environ.get("EVOLVE_GFX_FFDEC"),
        "Pinned FFDec is unavailable; XML rebuild must use the real tool")
    def test_real_ffdec_tag_xml_roundtrip_retains_gfx_movie(self):
        import os
        from gfx_preview_backend import inspect_gfx
        from gfx_edit_backend import _ffdec
        installed = os.environ["EVOLVE_GFX_FFDEC"]
        snap = create_gfx_session(self.project, self.virtual)
        xml = self.root / "movie.xml"
        export_gfx_xml(self.project, snap.session, xml, configured=installed)
        self.assertTrue(xml.is_file())
        self.assertGreater(xml.stat().st_size, 100)
        compiled = self.root / "converted.gfx"
        _ffdec(["-xml2swf", str(xml), str(compiled)],
               configured=installed, folder=self.root)
        payload = compiled.read_bytes()
        actual = inspect_gfx(payload)
        expected = inspect_gfx(self.original)
        self.assertEqual((actual.width, actual.height, actual.frames),
                         (expected.width, expected.height, expected.frames))
        self.assertEqual(actual.sprites, expected.sprites)
        self.assertEqual(actual.exports, expected.exports)

    def test_xml_export_is_only_an_external_copy(self):
        snap = create_gfx_session(self.project, self.virtual)
        xml = self.root / "edit.xml"
        with patch("gfx_edit_backend._ffdec",
                   side_effect=lambda args, **_: Path(args[-1]).write_text(
                       "<SWF tags='1'/>")):
            exported = export_gfx_xml(self.project, snap.session, xml)
        self.assertEqual(exported.resolve(), xml.resolve())
        self.assertIn("SWF", xml.read_text())
        self.assertEqual(self.target.read_bytes(), self.original)
        with self.assertRaises(GfxError):
            export_gfx_xml(self.project, snap.session, xml)


if __name__ == "__main__":
    unittest.main()
