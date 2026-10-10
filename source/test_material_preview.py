"""Synthetic material, UV and Activity Log regressions (no game assets distributed)."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from PIL import Image

from model_preview import read_preview_mesh
from material_preview import PreviewMaterials, load_preview_materials, render_textured_mesh


def uv_mesh():
    verts=[(-1,-1,0,1),(1,-1,0,1),(1,1,0,1),(-1,1,0,1)]
    streams=[(0,8,b''.join(struct.pack('<4e',*p) for p in verts)),
             (2,8,struct.pack('<8f',0,0,1,0,1,1,0,1)),
             (5,2,struct.pack('<6H',0,1,2,0,2,3))]
    chunk_count=4
    start=16+16*chunk_count
    chunk_sub=struct.pack('<4I',1,1,0,0)+struct.pack('<5I',0,6,0,4,0)+struct.pack('<4f',0,0,0,0)
    parts=[(0x08001017,chunk_sub)]
    parts.extend((0x08001016,struct.pack('<6I',0,typ,len(data)//stride,stride,0,0)+data)
                 for typ,stride,data in streams)
    desc=[];offset=start
    for index,(kind,content) in enumerate(parts):
        desc.append(struct.pack('<4I',kind,index+1,len(content),offset))
        offset+=len(content)
    return b'CrChF\x07\x00\x00'+struct.pack('<II',chunk_count,16)+b''.join(desc)+b''.join(data for _,data in parts)

class MaterialRenderingTests(unittest.TestCase):
    def test_uv_stream_and_subset(self):
        mesh=read_preview_mesh(uv_mesh())
        self.assertEqual(len(mesh.uvs),4)
        self.assertEqual(mesh.subsets,((0,6,0),))
        self.assertEqual(mesh.uvs[1],(1,0))
    def test_textured_pixels_and_change(self):
        mesh=read_preview_mesh(uv_mesh())
        a=PreviewMaterials((Image.new('RGB',(8,8),(220,20,20)),),('skin',),'test.mtl')
        b=PreviewMaterials((Image.new('RGB',(8,8),(20,20,220)),),('skin',),'test.mtl')
        im=render_textured_mesh(mesh,a,260,260)
        blue=render_textured_mesh(mesh,b,260,260)
        self.assertNotEqual(im.tobytes(),blue.tobytes())
        self.assertNotEqual(im.getpixel((130,130)),(32,33,42))
    def test_missing_material_is_safe(self):
        mesh=read_preview_mesh(uv_mesh())
        with self.assertRaisesRegex(ValueError,'No resolved diffuse'):
            render_textured_mesh(mesh,PreviewMaterials((None,),('none',),'missing'),220,220)
    def test_activity_log_starts_visible_and_shows_during_work(self):
        import inspect
        from pak_manager_gui import Manager
        source=inspect.getsource(Manager.__init__)
        self.assertIn('self.toggle_log(show=True)',source)
        self.assertIn('self.toggle_log(show=True)',inspect.getsource(Manager.run_steps))
    def test_gui_activity_log_and_models_texture_toggle(self):
        import tkinter as tk
        from types import SimpleNamespace
        from unittest.mock import patch
        from workspace_editor import WorkspaceEditor
        from dark_theme import apply_theme
        try:
            root=tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                workspace=Path(folder);base=workspace/'files'/'characters'/'goliath'
                base.mkdir(parents=True)
                (base/'goliath.skinm').write_bytes(uv_mesh())
                (base/'goliath.mtl').write_text('<Material><SubMaterials><Material Name="body" Shader="Illum"><Textures><Texture Map="Diffuse" File="characters/goliath/color.tif" /></Textures></Material></SubMaterials></Material>')
                Image.new('RGB',(8,8),(100,40,120)).save(base/'color.dds',format='DDS')
                (workspace/'.evolve-pak-workspace.json').write_text(json.dumps({'version':1,'entries':[{'path':'characters/goliath/'+p,'format':'raw'} for p in ('goliath.skinm','goliath.mtl','color.dds')]}))
                apply_theme(root)
                manager=type('Stub',(),{'window':root,'busy':False,'t':lambda _self,key:key})()
                editor=WorkspaceEditor(manager,workspace)
                try:
                    editor.tabs.select(editor.tab_frames['models']);root.update()
                    tree=editor.trees['models'];tree.selection_set(editor.path_items['models']['characters/goliath/goliath.skinm']);root.update()
                    self.assertEqual(editor.view_mode,'model')
                    self.assertIsNotNone(editor.model_preview.materials)
                    self.assertTrue(editor.model_preview.use_textures.get())
                    self.assertTrue(editor.model_preview.mesh.uvs)
                finally:editor.window.destroy()
        finally:root.destroy()

    def test_material_resolution_selected_loose_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace=root/'workspace';texture_folder=root/'textures'
            (workspace/'files'/'characters'/'goliath').mkdir(parents=True)
            texture_folder.mkdir()
            mtl=b'<Material><SubMaterials><Material Name="Body" Shader="Illum"><Textures><Texture Map="Diffuse" File="characters/goliath/textures/color.tif" /></Textures></Material></SubMaterials></Material>'
            (workspace/'files'/'characters'/'goliath'/'goliath.mtl').write_bytes(mtl)
            (workspace/'.evolve-pak-workspace.json').write_text(json.dumps({'version':1,'entries':[{'path':'characters/goliath/goliath.mtl','format':'raw'}]}))
            # Pillow can export a DDS even without an external converter.
            Image.new('RGB',(8,8),(50,100,170)).save(texture_folder/'color.dds',format='DDS')
            result=load_preview_materials(workspace,'characters/goliath/goliath.skinm',texture_folder)
            self.assertEqual(result.count,1)
            self.assertEqual(result.textures[0].size,(8,8))

if __name__=='__main__':unittest.main()
