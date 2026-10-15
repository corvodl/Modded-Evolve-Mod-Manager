"""Synthetic CryEngine scene hierarchy regression tests; no game assets."""
import math
import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from level_scene_core import build_scene, model_reference, normalize_reference


class LevelSceneTests(unittest.TestCase):
    def test_prefab_brush(self):
        node = ET.fromstring('<Object Type="Brush" Id="a" Name="Spot" Pos="2,3,4" Rotate="1,0,0,0" Prefab="objects/lights/spot.cgf" Material="objects/lights/spot"/>')
        self.assertEqual(model_reference(node.attrib), "objects/lights/spot.cgf")
        objects, problems = build_scene([("Tutorial/Layers/Default.lyr", [node])])
        self.assertEqual(objects[0].model, "objects/lights/spot.cgf")
        self.assertEqual(objects[0].material, "objects/lights/spot")
        self.assertEqual(objects[0].world.pos, (2, 3, 4))
        self.assertFalse(problems)

    def test_parent_rotation_scale(self):
        parent = ET.fromstring('<Object Type="Group" Id="P" Pos="10,20,30" Rotate="0.70710678,0,0,0.70710678" Scale="2,3,1"/>')
        child = ET.fromstring('<Object Type="GeomEntity" Id="C" Parent="P" Pos="1,0,2" Rotate="1,0,0,0" Geometry="objects/dropship.cdf"/>')
        objs, problems = build_scene([("level.lyr", [parent, child])])
        child_world = next(x for x in objs if x.id == "c").world
        self.assertAlmostEqual(child_world.pos[0], 10., places=5)
        self.assertAlmostEqual(child_world.pos[1], 22., places=5)
        self.assertAlmostEqual(child_world.pos[2], 32., places=5)
        self.assertFalse(problems)

    def test_cycle_diagnostic(self):
        a = ET.fromstring('<Object Id="a" Parent="b" Name="A" Pos="0,0,0"/>')
        b = ET.fromstring('<Object Id="b" Parent="a" Name="B" Pos="0,0,0"/>')
        _, problems = build_scene([("l", [a,b])])
        self.assertTrue(any("cyclic" in x for x in problems))

    def test_bare_basename_not_accepted_as_model(self):
        self.assertFalse(model_reference({"EntityClass": "Spotlight", "Material": "objects/spot"}))
        with self.assertRaises(ValueError):
            normalize_reference("../../spot.cgf")


if __name__ == "__main__":
    unittest.main()
