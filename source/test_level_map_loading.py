"""Synthetic complete-map regressions; no game content or credentials."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
import numpy as np
from PIL import Image

from level_map_assembly import assemble_map_scene
from level_terrain_tiles import decode_tile, load_tile_atlas
from level_terrain_decode import decode_terrain_chunks
from level_residency import SceneResidency, retained_bytes
from qt_level_scene_render import RenderAsset
from test_level_terrain_decode import synthetic_heightmap


class MapAssemblyTests(unittest.TestCase):
    def test_complementary_runtime_guid_precedence_and_ownership(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'mission.xml'
            path.write_text('<Mission><Objects><Entity Id="same" Pos="99,0,0" />'
                '<Entity Id="extra" Pos="3,0,0"><Properties Geometry="objects/tree.cgf" />'
                '</Entity></Objects></Mission>')
            original=fromstring('<Object Id="same" Pos="1,0,0" />')
            project=SimpleNamespace(workspace=Path(temp),level_name='Tutorial',
                layers={'source.lyr':(None,None,[original])})
            record={'file':path,'relative':'mission_mission0.xml','archive':'level.pak','map_owner':'tutorial'}
            index={'mission_mission0.xml':[record], 'mission_other.xml':[record]}
            scene,issues=assemble_map_scene(project,index)
            self.assertEqual({o.id for o in scene},{'same','extra'})
            self.assertEqual(next(o for o in scene if o.id=='same').world.pos,(1.,0.,0.))
            self.assertEqual(next(o for o in scene if o.id=='extra').model,'objects/tree.cgf')
            self.assertTrue(any('duplicate' in x for x in issues))
            self.assertTrue(any('Inactive mission' in x for x in issues))
            self.assertEqual(list(project.layers),['source.lyr'])
            self.assertEqual(original.get('Pos'),'1,0,0')
            record['map_owner']='other'
            self.assertEqual(len(assemble_map_scene(project,index)[0]),1)

    def test_cancelled_assembly(self):
        cancel=Event();cancel.set()
        project=SimpleNamespace(layers={},workspace=Path('.'),level_name='tutorial')
        with self.assertRaises(InterruptedError):
            assemble_map_scene(project,{'mission_mission0.xml':[]},cancel=cancel)


class TileTests(unittest.TestCase):
    def test_strict_envelope_and_explicit_channels(self):
        raw=struct.pack('<II',2,1)+bytes((255,0,0,0,255,0))
        self.assertEqual(decode_tile(raw).getpixel((0,0)),(255,0,0))
        self.assertEqual(decode_tile(raw,channel_order='BGR').getpixel((0,0)),(0,0,255))
        for data in (raw[:-1],raw+b'0',struct.pack('<II',0,1),struct.pack('<II',0xffffffff,1)):
            with self.assertRaises(ValueError):decode_tile(data)

    def test_atlas_adjacency_and_missing_tile(self):
        with TemporaryDirectory() as temp:
            index={}
            for x in range(2):
                for y in range(2):
                    key=f'tile{x}_{y}.raw';path=Path(temp)/key
                    path.write_bytes(struct.pack('<II',2,2)+bytes((x*100,y*100,0))*4)
                    index[key]=[{'file':path,'relative':key}]
            atlas=load_tile_atlas(index,Path(temp))
            self.assertEqual(atlas.getpixel((3,0)),(100,0,0))
            self.assertEqual(atlas.getpixel((0,3)),(0,100,0))
            del index['tile1_1.raw']
            with self.assertRaises(ValueError):load_tile_atlas(index,Path(temp))

    def test_chunks_have_identical_edges_normals_and_source_height(self):
        chunks=tuple(decode_terrain_chunks(synthetic_heightmap(128,128),stride=1,chunk_cells=32))
        self.assertGreater(len(chunks),1)
        points={}
        duplicate=0
        for chunk in chunks:
            self.assertLessEqual(len(chunk.vertices),33*33)
            for v,n in zip(chunk.vertices,chunk.normals):
                key=tuple(v[:2])
                if key in points:
                    np.testing.assert_array_equal(points[key][0],v)
                    np.testing.assert_array_equal(points[key][1],n)
                    duplicate+=1
                points[key]=(v,n)
        self.assertEqual(len(points),128*128)
        self.assertGreater(duplicate,0)


class ResidencyTests(unittest.TestCase):
    def test_eviction_reload_and_shared_accounting(self):
        def resource(label):
            mesh=SimpleNamespace(vertices=((0.,0.,0.),),radius=1)
            return RenderAsset(mesh,np.zeros(128,dtype=np.float32),None,0,label,((0,0,0),(1,1,1)))
        a,b=resource('a'),resource('b')
        self.assertEqual(retained_bytes([a,a]),retained_bytes([a]))
        budget=max(retained_bytes([a]),retained_bytes([b]))+100
        calls=[]
        def decode(obj,cache):
            calls.append(obj)
            return {'a':a,'b':b}[obj]
        store=SceneResidency({'a':'a','b':'b'},decode,budget)
        self.assertEqual(set(store.load(['a','b'],Event())),{'a'})
        self.assertEqual(set(store.load(['b','a'],Event())),{'b'})
        self.assertEqual(set(store.load(['a'],Event())),{'a'})
        self.assertEqual(set(store.catalog),{'a','b'})
        cancel=Event();cancel.set()
        self.assertIsNone(store.load(['b'],cancel))

    def test_normal_specular_images_count_and_deduplicate(self):
        from material_preview import PreviewMaterials
        image=Image.new('RGBA',(16,16))
        m=PreviewMaterials((image,),('x',),'test',normal_maps=(image,),specular_maps=(image,))
        a=RenderAsset(None,None,m,0,'a',())
        self.assertGreaterEqual(retained_bytes([a]),1024)
        from dataclasses import replace
        separate=replace(m,normal_maps=(image.copy(),),specular_maps=(image.copy(),))
        b=replace(a,materials=separate)
        self.assertGreaterEqual(retained_bytes([b])-retained_bytes([a]),2048)


class DependencyClosureTests(unittest.TestCase):
    def test_imported_material_followed_and_existing_fallback_upgraded(self):
        from level_dependency_loader import ensure_map_archives
        with TemporaryDirectory() as temp:
            root=Path(temp)
            mesh=root/'tree.cgf';mesh.write_bytes(b'not a mesh; explicit override used')
            material=root/'tree.mtl'
            material.write_text('<Material><Textures><Texture File="objects/tree.dds" /></Textures></Material>')
            sources=[root/'materials.pak',root/'stream.pak',root/'fragments.pak']
            records={'objects/tree.cgf':[{'file':mesh,'relative':'objects/tree.cgf'}],
                     'objects/tree.dds':[{'file':mesh,'relative':'objects/tree.dds'}]}
            supplies={sources[0]:{'objects/tree.mtl':[{'file':material,'relative':'objects/tree.mtl'}]},
                      sources[1]:{'objects/tree.dds.0':[{'file':mesh}]},
                      sources[2]:{'objects/tree.dds.1':[{'file':mesh}]}}
            seen=[]
            def providers(remaining,missing,**kw):
                seen.append(set(kw.get('stream_roots',())))
                return {name:[source] for source in remaining for name in supplies[source]
                        if name in missing or any(name.startswith(r+'.') for r in kw.get('stream_roots',()))}
            def importer(source,**kw):
                records.update(supplies[source]);return {'status':'imported'}
            project=SimpleNamespace(workspace=root,level_name='tutorial',scene=(SimpleNamespace(model='objects/tree.cgf',material='objects/tree'),))
            with patch('level_dependency_loader.discover_game_paks',return_value=SimpleNamespace(files=sources)), \
                 patch('level_dependency_loader.imported_archives',return_value=()), \
                 patch('level_asset_mount.mount_map_index',side_effect=lambda *a,**kw:(dict(records),{})), \
                 patch('level_dependency_loader.providers_for_missing',side_effect=providers), \
                 patch('level_dependency_loader.import_pak',side_effect=importer):
                report=ensure_map_archives(project,root,root,root)
            self.assertEqual(set(report['imported']),{p.name for p in sources})
            self.assertFalse(report['missing'])
            self.assertTrue(any('objects/tree.dds' in roots for roots in seen))

if __name__=='__main__':unittest.main()
