"""Synthetic fixtures for CryEngine placed-object level editor 1.0."""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from level_editor_core import LevelProject, parse_vec

LAYER_A = "levels/DesertMountains/layers/Natural.lyr"
LAYER_B = "levels/DesertMountains/layers/Structures.lyr"
OBJECTS_A = b"""<?xml version="1.0"?>
<Objects><Object Name="Rock_A" Type="Brush"
Pos="15,25,35" Rotate="0,0,0,1" Scale="1,1,1"
Geometry="objects/rocks/rock.cgf" NonEditable="preserve"/>
<Object Name="Tree_A" Pos="-15,5,1" Scale="2,2,2"/></Objects>"""
OBJECTS_B = b"""<?xml version="1.0"?>
<Objects><Object Name="Beacon" Type="Entity" Pos="5,3,0"
EntityClass="SpawnPoint" KeepThis="yes" /></Objects>"""


class LevelCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace=Path(self.tmp.name)/"level-project"
        self.files=self.workspace/"files"
        for name,content in ((LAYER_A,OBJECTS_A),(LAYER_B,OBJECTS_B)):
            file=self.files/name
            file.parent.mkdir(parents=True,exist_ok=True)
            file.write_bytes(content)
        self.patch=patch("evolve_pak_workspace.load_workspace",return_value={
            "entries":[{"path":LAYER_A},{"path":LAYER_B}]})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_read_objects_across_layers_without_changing_files(self):
        scene=LevelProject(self.workspace)
        self.assertEqual(scene.level_name,"DesertMountains")
        self.assertEqual(len(scene.layers),2)
        self.assertEqual(len(scene.objects),3)
        self.assertEqual(scene.objects[0].pos,(15.,25.,35.))
        self.assertEqual(scene.objects[0].geometry,"objects/rocks/rock.cgf")
        self.assertEqual(scene.dirty_layers(),set())
        self.assertEqual((self.files/LAYER_A).read_bytes(),OBJECTS_A)

    def test_round_trip_undo_redo_and_backup(self):
        scene=LevelProject(self.workspace)
        scene.apply(LAYER_A,0,"Pos",(18,26,37))
        scene.apply(LAYER_A,0,"Rotate",(0,0,.5,.5))
        self.assertEqual(scene.get(LAYER_A,0).pos,(18.,26.,37.))
        self.assertTrue(scene.undo())
        self.assertEqual(scene.get(LAYER_A,0).rotate,(0.,0.,0.,1.))
        self.assertTrue(scene.redo())
        result=scene.save()
        self.assertEqual(result,(LAYER_A,))
        modified=ET.fromstring((self.files/LAYER_A).read_bytes())
        node=modified.find("Object")
        self.assertEqual(node.attrib["NonEditable"],"preserve")
        self.assertEqual(node.attrib["Pos"],"18,26,37")
        self.assertEqual(node.attrib["Rotate"],"0,0,0.5,0.5")
        self.assertEqual((self.workspace/".level-editor-backups"/LAYER_A).read_bytes(),OBJECTS_A)
        self.assertEqual((self.files/LAYER_B).read_bytes(),OBJECTS_B)
        self.assertFalse(scene.dirty_layers())

    def test_material_override_save_reopen_and_undo(self):
        scene=LevelProject(self.workspace)
        original=(self.files/LAYER_A).read_bytes()
        changed=scene.apply_material(
            LAYER_A,0,"objects/man_made/props/lights/spotlight01/spotlight01_cine.mtl")
        self.assertTrue(changed)
        self.assertIn(LAYER_A,scene.dirty_layers())
        self.assertEqual(
            scene._node(LAYER_A,0).get("Material"),
            "objects/man_made/props/lights/spotlight01/spotlight01_cine")
        self.assertTrue(scene.undo())
        self.assertNotIn(LAYER_A,scene.dirty_layers())
        self.assertEqual((self.files/LAYER_A).read_bytes(),original)
        self.assertTrue(scene.redo())
        scene.save()
        reopened=LevelProject(self.workspace)
        self.assertEqual(
            reopened._node(LAYER_A,0).get("Material"),
            "objects/man_made/props/lights/spotlight01/spotlight01_cine")
        self.assertEqual(
            (self.workspace/".level-editor-backups"/LAYER_A).read_bytes(),
            original)
        with self.assertRaises(ValueError):
            reopened.apply_material(LAYER_A,0,"../../wrong.mtl")

    def test_undo_to_original_does_not_rewrite_xml(self):
        scene=LevelProject(self.workspace)
        scene.apply(LAYER_A,0,"Scale",(2,3,4))
        scene.undo()
        self.assertFalse(scene.dirty_layers())
        self.assertEqual(scene.save(),())
        self.assertEqual((self.files/LAYER_A).read_bytes(),OBJECTS_A)

    def test_reject_external_changes_and_wrong_transform(self):
        scene=LevelProject(self.workspace)
        with self.assertRaises(ValueError):
            scene.apply(LAYER_A,0,"Geometry",("unsafe",))
        with self.assertRaises(ValueError):
            scene.apply(LAYER_A,0,"Scale",(0,1,1))
        with self.assertRaises(ValueError):
            scene.apply(LAYER_A,0,"Pos",(float("nan"),1,2))
        scene.apply(LAYER_A,0,"Pos",(11,22,33))
        (self.files/LAYER_A).write_bytes(OBJECTS_A+b"\n")
        with self.assertRaisesRegex(ValueError,"changed on disk"):
            scene.save()

    def test_real_evolve_level_layout_without_lyr_uses_mission_xml(self):
        """Level.pak actually contains leveldata.xml, mission_*.xml, no .lyr."""
        file = self.files / "mission_mission0.xml"
        xml = (
            b'<Mission Name="Mission0"><Objects>'
            b'<Entity Name="AnimObject" EntityId="673" EntityClass="AnimObject" '
            b'Layer="ManMade" Pos="303.936,314.97638,50.38" '
            b'Rotate="0.89493436,0,0,-0.446" Scale="1,1,1" '
            b'UnknownGameProperty="preserved"/>'
            b'<Object Name="Rock" Type="Brush" Pos="10,20,30" '
            b'Geometry="objects/rocks/rock.cgf"/>'
            b'</Objects></Mission>'
        )
        file.write_bytes(xml)
        (self.files/"leveldata.xml").write_bytes(
            b'<LevelData><LevelInfo Name="Lava_Rivers" /></LevelData>')
        (self.files/"levelinfo.xml").write_bytes(
            b'<LevelInfo Name="game/Levels/Lava_Rivers"/>')
        fake = patch("evolve_pak_workspace.load_workspace", return_value={
            "source_pak":"Game/levels/Lava_Rivers/level.pak",
            "entries":[{"path":"mission_mission0.xml"},
                       {"path":"leveldata.xml"},{"path":"levelinfo.xml"}]})
        with fake:
            self.assertTrue(LevelProject.contains_levels(self.workspace))
            scene = LevelProject(self.workspace)
        self.assertEqual(scene.level_name, "Lava_Rivers")
        self.assertEqual(scene.layer_source,
                         "CryEngine mission_*.xml object placements")
        self.assertEqual(len(scene.objects),2)
        self.assertEqual(scene.objects[0].kind,"Entity")
        self.assertEqual(scene.objects[0].pos,(303.936,314.97638,50.38))
        scene.apply("mission_mission0.xml",0,"Pos",(100,200,300))
        scene.save()
        saved = ET.fromstring(file.read_bytes())
        self.assertEqual(saved.find("Objects/Entity").get("UnknownGameProperty"),
                         "preserved")
        self.assertEqual(saved.find("Objects/Entity").get("Pos"),"100,200,300")
        self.assertEqual((self.workspace/".level-editor-backups"/
                          "mission_mission0.xml").read_bytes(),xml)

    def test_parse_vec_untrusted_strings(self):
        self.assertEqual(parse_vec("1,2,3"),(1.,2.,3.))
        self.assertIsNone(parse_vec("1e309,2,3"))
        self.assertIsNone(parse_vec("../x,1,2"))


if __name__=="__main__":
    unittest.main()
