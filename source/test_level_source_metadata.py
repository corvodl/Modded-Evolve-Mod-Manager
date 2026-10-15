"""Verified metadata-only CryEngine fixture tests, no game assets."""
from __future__ import annotations
from io import BytesIO
from zipfile import ZipFile
import unittest

from level_source_metadata import (
    embedded_metadata, inspect_editor_cry, inspect_heightmap,
    inspect_vegetation
)

class SourceMetadataTests(unittest.TestCase):
    def test_embedded_heightmap_metadata_not_flat_terrain(self):
        payload=(b"\x01\x00\x01" +
                 b'<Heightmap Width="512" Height="512" UnitSize="1" TextureSize="1024" MaxHeight="256" WaterLevel="-1000000">'
                 b'<SurfaceTypes><SurfaceType Name="sand"/></SurfaceTypes><Layers/></Heightmap>' +
                 b"\0"*2048)
        obj=inspect_heightmap(payload)
        self.assertEqual(obj["Width"],512)
        self.assertEqual(obj["WaterLevel"],-1000000.)
        self.assertFalse(obj["PayloadDecoded"])
        with self.assertRaises(ValueError):
            embedded_metadata(b"<Heightmap/>","Heightmap")

    def test_vegetation_catalog_not_instance_count(self):
        raw=(b"\xff\x01" +
             b'<VegetationMap Version="3"><Objects>'
             b'<Object Category="Forest" FileName="objects/trees/tree.cgf"/>'
             b'<Object Category="Forest" FileName="objects/trees/tree.cgf"/>'
             b'</Objects></VegetationMap>')
        info=inspect_vegetation(raw)
        self.assertEqual(info["Definitions"],2)
        self.assertEqual(info["ExplicitMeshes"],1)
        self.assertFalse(info["InstancePayloadDecoded"])

    def test_bounded_cry_editor_context_zip(self):
        target=BytesIO()
        with ZipFile(target,"w") as archive:
            archive.writestr("level.editor_xml",
                             '<Level><ObjectLayers><RootLayer/></ObjectLayers>'
                             '<Objects><Object Id="a"/></Objects></Level>')
        info=inspect_editor_cry(target.getvalue())
        self.assertEqual(info["ObjectDeclarations"],1)
        self.assertEqual(info["LayerDeclarations"],1)
        self.assertFalse(info["SourceCompiled"])


if __name__ == "__main__":
    unittest.main()
