"""Archive qualification and copied-layer material persistence regressions."""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from level_asset_mount import level_from_source, map_relative_asset, mount_map_index


class ScopedMountTests(unittest.TestCase):
    def test_source_map_name_not_pak_filename(self):
        self.assertEqual(level_from_source(
            r"C:\Games\Evolve\Game\levels\Tutorial\level.pak"), "tutorial")
        self.assertEqual(level_from_source(
            r"C:\Games\Evolve\Game\levels\Desert_Canyon\terraintexture.pak"),
            "desert_canyon")
        self.assertEqual(level_from_source(r"C:\Games\Evolve\Game\levels_Desert_Canyon.pak"), "desert_canyon")
        self.assertIsNone(level_from_source(r"C:\Games\Evolve\Game\characters\data.pak"))
        self.assertTrue(map_relative_asset("brush/designer_100.cgf"))
        self.assertTrue(map_relative_asset("tile0_0.raw"))
        self.assertFalse(map_relative_asset("objects/trees/tree01.cgf"))

    def test_compiled_root_collision_is_map_specific(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            workspace=root/"test_workspace"
            (workspace/"files").mkdir(parents=True)
            lib=root/"library"
            a,b=(lib/"tutorial__aaaa",lib/"forest__bbbb")
            for p in (a,b):
                (p/"files"/"brush").mkdir(parents=True)
                (p/"files"/"brush"/"designer_100.cgf").write_bytes(p.name.encode())
            def record(folder):
                return {
                    "file": folder/"files"/"brush"/"designer_100.cgf",
                    "workspace": None,
                    "archive": "level.pak",
                    "relative": "brush/designer_100.cgf",
                }
            index={"brush/designer_100.cgf":[record(a),record(b)]}
            manifests={
                a:{"source":r"C:\Game\levels\Tutorial\level.pak","name":"level.pak"},
                b:{"source":r"C:\Game\levels\Forest_Cliffs\level.pak","name":"level.pak"},
            }
            with (patch("asset_library._manifest_dirs",return_value=[a,b]),
                  patch("asset_library._load_manifest",side_effect=lambda p:manifests[p]),
                  patch("asset_library.extracted_index",return_value=index),
                  patch("asset_library._home",return_value=lib),
                  patch("evolve_pak_workspace.load_workspace",return_value={"entries":[]})):
                scoped,metadata=mount_map_index(workspace,"tutorial")
            results=scoped["brush/designer_100.cgf"]
            self.assertEqual(len(results),1)
            self.assertEqual(results[0]["file"],a/"files"/"brush"/"designer_100.cgf")
            self.assertEqual(metadata["mounts"],1)


if __name__=="__main__":
    unittest.main()
