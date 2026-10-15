"""Read-only tests for authored geometry, packed maps and material layers."""
from io import BytesIO
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image
from material_preview import load_preview_materials
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_textured_export import _unpack_glb, embed_diffuse_textures
from renderer_3 import FRAGMENT
from texture_transform import transform_for, gl_matrix3


def mesh_with_authored_basis():
    # Original data-stream types 1 (float3 normals) and 6 (int16 pairs
    # of tangent and binormal). No original commercial game data needed.
    verts = [(0., 0., 0., 1.), (1., 0., 0., 1.), (0., 1., 0., 1.)]
    streams = (
        (0, 8, b''.join(struct.pack('<4e', *v) for v in verts)),
        (1, 12, struct.pack('<9f', *((0., 0., 1.) * 3))),
        (2, 8, struct.pack('<6f', 0., 0., 1., 0., 0., 1.)),
        (5, 2, struct.pack('<3H', 0, 1, 2)),
        (6, 16, (struct.pack('<8h', 32767, 0, 0, 32767,
                             0, 32767, 0, 32767) * 3)),
    )
    chunks = [struct.pack('<6I', 0, typ, len(raw)//stride, stride, 0, 0)+raw
              for typ, stride, raw in streams]
    offset = 16 + len(chunks)*16
    directory = []
    for i, chunk in enumerate(chunks):
        directory.append(struct.pack('<4I', 0x08001016, i+1,
                                     len(chunk), offset))
        offset += len(chunk)
    return (b'CrChF\x07\0\0' + struct.pack('<II',len(chunks),16)
            + b''.join(directory) + b''.join(chunks))


class ExtendedTexturePipelineTests(unittest.TestCase):
    def test_original_normal_and_signed_tangent_are_exported(self):
        mesh = read_preview_mesh(mesh_with_authored_basis())
        self.assertEqual(mesh.vertex_normals[0], (0., 0., 1.))
        self.assertEqual(mesh.vertex_tangents[0], (1., 0., 0., 1.))
        doc, payload = _unpack_glb(glb_bytes(mesh))
        attrs = doc['meshes'][0]['primitives'][0]['attributes']
        normal = doc['accessors'][attrs['NORMAL']]
        tangent = doc['accessors'][attrs['TANGENT']]
        nview = doc['bufferViews'][normal['bufferView']]
        tview = doc['bufferViews'][tangent['bufferView']]
        self.assertEqual(struct.unpack_from('<3f',payload,nview['byteOffset']),
                         (0., 1., 0.))
        self.assertEqual(struct.unpack_from('<4f',payload,tview['byteOffset']),
                         (1., 0., 0., 1.))

    def test_gloss_channel_and_distinct_detail_uvs(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            prefix='characters/hunters/merc_test/'
            material=root/'skin.mtl'
            material.write_text(
                '<Material><Textures>'
                '<Texture Map="Diffuse" File="'+prefix+'skin_d.tif"/>'
                '<Texture Map="Specular" GlossChannel="A" File="'+prefix+'skin_s.tif"/>'
                '<Texture Map="Normal" File="'+prefix+'skin_n.tif"/>'
                '<Texture Map="Detail" File="'+prefix+'skin_detail.tif">'
                '<TexMod TileU="4" TileV="6" OffsetU="0.25"/>'
                '</Texture>'
                '<Texture Map="DetailNormal" File="'+prefix+'skin_detail_n.tif"/>'
                '</Textures></Material>',encoding='utf-8')
            files=[material]
            for name, mode, color in (
                ('skin_d','RGB',(180,130,90)),
                ('skin_s','RGBA',(50,70,90,219)),
                ('skin_n','RGB',(128,128,255)),
                ('skin_detail','RGB',(80,90,100)),
                ('skin_detail_n','RGB',(128,128,255))):
                f=root/(name+'.dds')
                Image.new(mode,(4,4),color).save(f,'DDS')
                files.append(f)
            idx={prefix+file.name: [{'file':file,'relative':prefix+file.name,
                                      'workspace':None}] for file in files}
            materials=load_preview_materials(root,prefix+'skin.skinm',index=idx)
            self.assertIsNotNone(materials.gloss_maps[0])
            self.assertEqual(materials.gloss_maps[0].getpixel((0,0)),(219,219,219))
            self.assertIsNotNone(materials.detail_normal_maps[0])
            self.assertEqual(transform_for(materials,0,'detail')[:3],(4.,6.,0.25))
            self.assertEqual(transform_for(materials,0,'normal')[0],1.)
            self.assertEqual(len(gl_matrix3(transform_for(materials,0,'detail'))),9)
            mesh=SimpleNamespace(vertices=[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],
                 triangles=[(0,1,2)],uvs=[(0.,0.),(1.,0.),(0.,1.)],subsets=())
            blob, count=embed_diffuse_textures(glb_bytes(mesh),materials)
            self.assertEqual(count,1)
            doc,_=_unpack_glb(blob)
            self.assertEqual(len(doc['images']),6)
            layers=doc['materials'][0]['extras']['cryEngineTextureLayers']
            self.assertEqual({x['role'] for x in layers},
                             {'detail','detail_normal'})
            self.assertEqual(next(x for x in layers if x['role']=='detail')
                             ['uvTransform'][:2],[4.,6.])

    def test_combined_level_scene_remaps_both_gloss_textures(self):
        from material_preview import PreviewMaterials
        from level_blender_export import SceneAssembler
        mesh=SimpleNamespace(vertices=[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],
             triangles=[(0,1,2)],uvs=[(0.,0.),(1.,0.),(0.,1.)],subsets=())
        materials=PreviewMaterials(
            (Image.new('RGB',(4,4),(80,80,80)),),('slot',),'test.mtl',
            gloss_maps=(Image.new('L',(4,4),200),))
        blob,_=embed_diffuse_textures(glb_bytes(mesh),materials)
        scene=SceneAssembler('two materials')
        scene.append_glb('one',blob)
        scene.append_glb('two',blob)
        doc,_=_unpack_glb(scene.finish())
        self.assertEqual(len(doc['materials']),2)
        self.assertEqual(len(doc['textures']),4)
        a=doc['materials'][0]['pbrMetallicRoughness']['metallicRoughnessTexture']['index']
        b=doc['materials'][1]['pbrMetallicRoughness']['metallicRoughnessTexture']['index']
        self.assertEqual((a,b),(1,3))

    def test_sampler_uvs_and_dual_normal_shader_paths(self):
        self.assertIn('texture2D(normal_map,normal_coord)', FRAGMENT)
        self.assertIn('texture2D(detail_map,detail_coord)', FRAGMENT)
        self.assertIn('texture2D(detail_normal_map,detail_normal_coord)', FRAGMENT)
        self.assertIn('texture2D(gloss_map,gloss_coord)', FRAGMENT)


if __name__ == '__main__':
    unittest.main()
