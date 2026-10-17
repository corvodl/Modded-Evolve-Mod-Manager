"""Synthetic regression coverage for safe LOD0, UV/material and animation export."""
import unittest
from types import SimpleNamespace
from model_blender_export import glb_bytes
from model_textured_export import _unpack_glb, _pack_glb
from model_export_readiness import (
    audit_material_uvs, ensure_lod0_parts, embedded_action_names)


def mesh(vertices=None, uvs=None, subsets=None, normals=None):
    return SimpleNamespace(
        vertices=vertices or ((0.,0.,0.), (1.,0.,0.), (0.,1.,0.),
                              (0.,0.,0.)),
        triangles=((0,1,2),(3,1,2)),
        uvs=uvs if uvs is not None else ((0.,0.),(1.,0.),(0.,1.),(0.,0.)),
        vertex_normals=normals if normals is not None else ((0.,0.,1.),)*4,
        vertex_tangents=(),
        subsets=subsets if subsets is not None else ((0,6,0),))


class SourceReadyExportTests(unittest.TestCase):
    def test_static_weld_compacts_matching_uv_normal_material_duplicates(self):
        original=glb_bytes(mesh(), static_weld=False)
        compact=glb_bytes(mesh(), static_weld=True)
        a,_=_unpack_glb(original)
        b,_=_unpack_glb(compact)
        self.assertEqual(a['accessors'][0]['count'],4)
        self.assertEqual(b['accessors'][0]['count'],3)
        self.assertEqual(b['extras']['modelRepair']['weldedStaticVertices'],1)
        # The actual CDF/rig export deliberately preserves native IDs.
        self.assertEqual(a['accessors'][0]['count'],4)

    def test_static_weld_keeps_uv_seams_and_hard_normal_edges(self):
        uvs=((0.,0.),(1.,0.),(0.,1.),(0.25,0.25))
        blob=glb_bytes(mesh(uvs=uvs), static_weld=True)
        doc,_=_unpack_glb(blob)
        self.assertEqual(doc['accessors'][0]['count'],4)
        normals=((0.,0.,1.),)*3 + ((0.,0.,-1.),)
        blob=glb_bytes(mesh(normals=normals), static_weld=True)
        doc,_=_unpack_glb(blob)
        self.assertEqual(doc['accessors'][0]['count'],4)

    def test_material_boundaries_do_not_weld(self):
        original=mesh(subsets=((0,3,0),(3,3,1)))
        doc,_=_unpack_glb(glb_bytes(original, static_weld=True))
        self.assertEqual(doc['accessors'][0]['count'],4)
        self.assertEqual(len(doc['materials']),2)
        self.assertEqual(len(doc['meshes'][0]['primitives']),2)

    def test_uvs_are_required_for_every_textured_material_primitive(self):
        blob=glb_bytes(mesh())
        doc,raw=_unpack_glb(blob)
        doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']={'index':0}
        doc['textures']=[{'source':0}]
        good=_pack_glb(doc,raw)
        self.assertEqual(audit_material_uvs(good)['textured_primitives_verified'],1)
        doc['accessors'][1]['count']=3
        with self.assertRaisesRegex(ValueError, 'complete UV0'):
            audit_material_uvs(_pack_glb(doc,raw))
        del doc['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0']
        with self.assertRaisesRegex(ValueError, 'complete UV0'):
            audit_material_uvs(_pack_glb(doc,raw))

    def test_render_parts_do_not_use_lower_lods(self):
        actor=lambda name: SimpleNamespace(rig_parts=(
            ('characters/hunters/merc_tech/' + name,None,None,3),))
        self.assertEqual(ensure_lod0_parts(actor('tech_3p_lod0.skinm'))['lod'],0)
        self.assertEqual(ensure_lod0_parts(actor('tech_3p.skinm'))['lod'],0)
        with self.assertRaisesRegex(ValueError, 'LOD0'):
            ensure_lod0_parts(actor('tech_3p_lod2.skinm'))

    def test_verified_embedded_clips_require_names_and_one_rig(self):
        doc,raw=_unpack_glb(glb_bytes(mesh()))
        self.assertEqual(embedded_action_names(_pack_glb(doc,raw)),())
        doc['skins']=[{'joints':[0]}]
        doc['animations']=[{'name':'idle'}, {'name':'run'}]
        self.assertEqual(embedded_action_names(_pack_glb(doc,raw)),('idle','run'))
        doc['animations'].append({'name':'idle'})
        self.assertEqual(embedded_action_names(_pack_glb(doc,raw)),())


if __name__=='__main__':
    unittest.main()
