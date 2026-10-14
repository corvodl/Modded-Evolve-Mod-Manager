"""Synthetic 2012 ZIP PAK round-trips and unsafe-archive regression checks."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import stat
import struct
import unittest
import zipfile

from legacy_pak_workspace import (
    PROJECT_FILE, create_project, load_project, inspect_archive,
    read_member, read_editable_text, stage_text, stage_file, revert_file,
    changed_members, export_member, rebuild_pak, sha_file,
)
from legacy_texture_preview import decode_legacy_texture


def _dds_header(w=4, h=4, mip=2):
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x2100F)
    struct.pack_into("<I", header, 12, h)
    struct.pack_into("<I", header, 16, w)
    struct.pack_into("<I", header, 20, w * 4)
    struct.pack_into("<I", header, 28, mip)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 0x41)
    struct.pack_into("<I", header, 88, 32)
    struct.pack_into("<IIII", header, 92,
                     0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000)
    struct.pack_into("<I", header, 108, 0x401008)
    return bytes(header)


def _aux_header(w=4, h=4, mip=2):
    head = bytearray(_dds_header(w, h, mip)[4:])
    struct.pack_into("<I", head, 76, 0x1)  # legacy 8-bit alpha plane
    struct.pack_into("<I", head, 84, 8)    # bpp; offset includes magic below
    return bytes(head)


class LegacyPAKTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "Scripts.pak"
        self.body = b'<Entity name="Legacy" />'
        with zipfile.ZipFile(self.source, "w") as pak:
            for path, data, method in (
                ("Entities/Spawner.ent", self.body, zipfile.ZIP_DEFLATED),
                ("Scripts/AI/monster.lua", b"return 42\n", zipfile.ZIP_DEFLATED),
                ("Sounds/effects.fsb", b"FSB5" + b"X" * 20000, zipfile.ZIP_STORED),
            ):
                member = zipfile.ZipInfo(path, (2012, 10, 24, 12, 12, 12))
                member.compress_type = method
                pak.writestr(member, data)
        self.original_sha = sha_file(self.source)
        self.workspace = self.root / "project"
        create_project(self.source, self.workspace)
        self.project = load_project(self.workspace)

    def test_archive_validates_and_projects_do_not_copy_all_data(self):
        info = inspect_archive(self.source, verify_crc=True)
        self.assertEqual(info["count"], 3)
        self.assertEqual(self.original_sha, sha_file(self.source))
        self.assertTrue((self.workspace / PROJECT_FILE).is_file())
        self.assertFalse((self.workspace / "files").exists())
        self.assertEqual(read_member(self.project, "Entities/Spawner.ent"), self.body)

    def test_edit_xml_lua_raw_and_build_unsigned_zip(self):
        text, codec = read_editable_text(self.project, "Entities/Spawner.ent")
        self.assertIn("Legacy", text)
        stage_text(self.project, "Entities/Spawner.ent",
                   '<Entity name="Updated" />', codec)
        incoming = self.root / "replacement.fsb"
        incoming.write_bytes(b"FSB5changed")
        stage_file(self.project, "Sounds/effects.fsb", incoming)
        self.assertEqual(len(changed_members(self.project)), 2)
        target = self.root / "Scripts-edited.pak"
        outcome = rebuild_pak(self.project, target)
        self.assertEqual(outcome["changes"], 2)
        with zipfile.ZipFile(target) as changed:
            self.assertIsNone(changed.testzip())
            self.assertIn(b"Updated", changed.read("Entities/Spawner.ent"))
            self.assertEqual(changed.read("Scripts/AI/monster.lua"), b"return 42\n")
            self.assertEqual(changed.read("Sounds/effects.fsb"), b"FSB5changed")
            self.assertEqual(changed.getinfo("Sounds/effects.fsb").compress_type,
                             zipfile.ZIP_STORED)
            self.assertEqual(changed.getinfo("Entities/Spawner.ent").date_time[:3],
                             (2012, 10, 24))
        self.assertEqual(sha_file(self.source), self.original_sha)
        with self.assertRaisesRegex((ValueError, FileExistsError), "overwrite|existing"):
            rebuild_pak(self.project, self.source)

    def test_restore_and_large_binary_stream_export(self):
        path = self.root / "external.fsb"
        export_member(self.project, "Sounds/effects.fsb", path)
        self.assertEqual(path.stat().st_size, 20004)
        stage_text(self.project, "Scripts/AI/monster.lua", "return 24\n", "utf-8")
        revert_file(self.project, "Scripts/AI/monster.lua")
        self.assertEqual(changed_members(self.project), [])
        with self.assertRaisesRegex(ValueError, "No staged changes"):
            rebuild_pak(self.project, self.root / "no-changes.pak")

    def test_mutated_source_fails_closed(self):
        with self.source.open("ab") as output:
            output.write(b"Modified outside editor")
        with self.assertRaisesRegex(ValueError, "changed"):
            load_project(self.workspace)
        with self.assertRaisesRegex(ValueError, "changed"):
            rebuild_pak(self.project, self.root / "unsafe.pak")
        self.assertFalse((self.root / "unsafe.pak").exists())

    def test_unsafe_legacy_zip_paths_and_links_rejected(self):
        for member in ("../stolen.txt", "C:/absolute.txt", "a/../b.txt"):
            path = self.root / "unsafe.pak"
            with zipfile.ZipFile(path, "w") as pak:
                pak.writestr(member, b"bad")
            with self.assertRaises(ValueError):
                inspect_archive(path)
        with zipfile.ZipFile(self.root / "unsafe.pak", "w") as pak:
            symlink = zipfile.ZipInfo("symlink.lua")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            pak.writestr(symlink, b"../../elsewhere")
        with self.assertRaisesRegex(ValueError, "symlink"):
            inspect_archive(self.root / "unsafe.pak")
        with zipfile.ZipFile(self.root / "unsafe.pak", "w") as pak:
            pak.writestr("Entities/Cat.xml", "one")
            pak.writestr("entities/cat.XML", "two")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            inspect_archive(self.root / "unsafe.pak")

    def test_legacy_dds_full_mipmap_preview_and_auxiliary_plane(self):
        textures = self.root / "Textures.pak"
        with zipfile.ZipFile(textures, "w") as pak:
            pak.writestr("Textures/red.dds.0",
                         _dds_header() + bytes((0, 0, 255, 255)) * 4)
            pak.writestr("Textures/red.dds.1",
                         bytes((0, 0, 255, 255)) * 16)
            pak.writestr("Textures/red_ddn.dds.0a",
                         _aux_header() + bytes((90,)) * 4)
            pak.writestr("Textures/red_ddn.dds.1a", bytes((130,)) * 16)
        folder = self.root / "textures-project"
        project = load_project(create_project(textures, folder))
        image, text = decode_legacy_texture(project, "Textures/red.dds.0")
        self.assertEqual(image.size, (4, 4))
        self.assertIn("4", text)
        plane, info = decode_legacy_texture(project, "Textures/red_ddn.dds.0a")
        self.assertEqual(plane.mode, "L")
        self.assertEqual(plane.getpixel((0, 0)), 130)
        self.assertIn("auxiliary", info)


if __name__ == "__main__":
    unittest.main()
