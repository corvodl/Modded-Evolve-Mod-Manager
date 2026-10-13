"""Synthetic combined CryEngine map -> Blender GLB regression tests."""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from level_blender_export import SceneAssembler, export_level_glb, _world_matrix
from level_scene_core import WorldTransform
from model_textured_export import _unpack_glb


def mesh():
    return SimpleNamespace(
        vertices=np.asarray([(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],dtype=np.float32),
        triangles=[(0,1,2)],
        uvs=np.asarray([(0.,0.),(1.,0.),(0.,1.)],dtype=np.float32),
        subsets=())


class CombinedMapExportTests(TestCase):
    def test_world_transform_coordinate_basis_and_nonuniform_scale(self):
        world=WorldTransform(pos=(5.,8.,12.),scale=(2.,3.,4.))
        mat=np.asarray(_world_matrix(world)).reshape(4,4,order='F')
        self.assertEqual(tuple(mat[:3,3]),(5.,12.,-8.))
        self.assertEqual(tuple(np.linalg.norm(mat[:3,:3],axis=0)),(2.,4.,3.))

    def test_resource_dedup_preserves_editable_instances(self):
        from model_blender_export import glb_bytes
        builder=SceneAssembler("Tutorial")
        blob=glb_bytes(mesh())
        resource=builder.append_glb(("objects/rock.cgf",""),blob)
        self.assertEqual(resource,builder.append_glb(("objects/rock.cgf",""),blob))
        builder.add_instance(resource,"Rock A",world=WorldTransform(pos=(1.,2.,3.)))
        builder.add_instance(resource,"Rock B",world=WorldTransform(pos=(9.,8.,7.)))
        doc,binary=_unpack_glb(builder.finish())
        self.assertEqual(len(doc["meshes"]),1)
        self.assertEqual(len(doc["nodes"]),2)
        self.assertEqual(doc["nodes"][0]["mesh"],doc["nodes"][1]["mesh"])
        self.assertNotEqual(doc["nodes"][0]["matrix"],doc["nodes"][1]["matrix"])

    def test_combines_placed_meshes_into_one_glb(self):
        from qt_level_scene_render import RenderAsset
        with TemporaryDirectory() as temp:
            output=Path(temp)/"Tutorial.glb"
            a=SimpleNamespace(
                name="Rock A",id="rock-a",parent_id="",source="layers/objects.lyr",
                kind="Brush",model="objects/rock.cgf",material="",
                world=WorldTransform(pos=(10.,20.,30.)),status="available")
            b=SimpleNamespace(**{**vars(a),"id":"rock-b","name":"Rock B",
                                 "world":WorldTransform(pos=(11.,21.,31.))})
            terrain=SimpleNamespace(
                vertices=np.asarray([(0,0,1),(1,0,1),(0,1,1)],np.float32),
                indices=np.asarray([0,1,2],np.uint32),
                width=2,height=2,unit=1.,source="Tutorial Heightmap.dat",
                decoded_samples=4,stride=1)
            asset=RenderAsset(mesh(),None,None,0,"model.cgf",((0,0,0),(1,1,0)))
            project=SimpleNamespace(level_name="Tutorial",scene=(a,b))
            with patch("qt_level_scene_render.prepare_scene",
                       return_value=({("objects/rock.cgf",""):asset},{},
                                     {"terrain":terrain})):
                report=export_level_glb(project,output)
            self.assertEqual(report["objects"],2)
            self.assertEqual(report["mesh_resources"],2)
            doc,_=_unpack_glb(output.read_bytes())
            self.assertEqual(len(doc["meshes"]),2)
            self.assertEqual(len(doc["nodes"]),3)
            self.assertEqual(doc["nodes"][1]["extras"]["guid"],"rock-a")
            self.assertEqual(doc["nodes"][2]["extras"]["guid"],"rock-b")

    def test_selected_mesh_does_not_include_entire_map_terrain(self):
        from qt_level_scene_render import RenderAsset
        with TemporaryDirectory() as temp:
            output=Path(temp)/"Selected.glb"
            a=SimpleNamespace(name="One",id="one",parent_id="",source="test",
                kind="Brush",model="objects/rock.cgf",material="",
                world=WorldTransform(),status="")
            b=SimpleNamespace(**{**vars(a),"id":"two","name":"Two"})
            asset=RenderAsset(mesh(),None,None,0,"model.cgf",((0,0,0),(1,1,0)))
            with patch("qt_level_scene_render.prepare_scene",
                return_value=({("objects/rock.cgf",""):asset},{},{"terrain":None})):
                report=export_level_glb(
                    SimpleNamespace(level_name="Tutorial",scene=(a,b)),
                    output,selection={"two"})
            self.assertEqual(report["objects"],1)
            doc,_=_unpack_glb(output.read_bytes())
            self.assertEqual(doc["nodes"][0]["name"],"Two")
