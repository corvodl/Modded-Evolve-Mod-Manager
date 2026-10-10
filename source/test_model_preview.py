"""Read-only CrChF render preview tests; fixtures have no game asset bytes."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from model_preview import read_preview_mesh, render_mesh, find_preview_mesh
from model_asset import inspect_model
from test_crchf_models import fixture as header_fixture


def test_mesh():
    # A tetrahedron: half4 vertex stream and 16-bit index stream, CrChF v7.
    vertices = [(-1., -1., 0., 1.), (1., -1., 0., 1.), (0., 1., 0., 1.), (0., 0., 2., 1.)]
    positions = b''.join(struct.pack('<4e',*p) for p in vertices)
    faces = (0, 1, 2, 0, 1, 3, 1, 2, 3, 2, 0, 3)
    indices = struct.pack('<12H', *faces)
    s1=struct.pack('<6I',0,0,len(vertices),8,0,0)+positions
    s2=struct.pack('<6I',0,5,len(faces),2,0,0)+indices
    table=struct.pack('<4I',0x08001016,1,len(s1),48)+struct.pack('<4I',0x08001016,2,len(s2),48+len(s1))
    return b'CrChF\x07\0\0'+struct.pack('<II',2,16)+table+s1+s2


class MeshPreviewTests(unittest.TestCase):
    def test_known_mesh_positions_indices(self):
        data=test_mesh()
        mesh=read_preview_mesh(data)
        self.assertEqual((len(mesh.vertices),len(mesh.triangles)),(4,4))
        self.assertEqual(mesh.vertices[3],(0.,0.,2.))
        self.assertEqual(mesh.triangles[0],(0,1,2))
        self.assertEqual(inspect_model(data).version,7)

    def test_render_is_visible_and_rotatable(self):
        mesh=read_preview_mesh(test_mesh())
        image=render_mesh(mesh,400,300)
        self.assertEqual(image.size,(400,300))
        self.assertNotEqual(image.getpixel((200,150)),(32,33,42))
        self.assertNotEqual(image.tobytes(),render_mesh(mesh,400,300,yaw=1.3).tobytes())
        self.assertNotEqual(image.tobytes(),render_mesh(mesh,400,300,wireframe=True).tobytes())

    def test_invalid_indices_are_rejected(self):
        data=bytearray(test_mesh()); data[-2:]=struct.pack('<H',999)
        with self.assertRaisesRegex(ValueError,'outside the vertex array'):
            read_preview_mesh(bytes(data))

    def test_unsupported_old_geometry_and_no_render_stream(self):
        with self.assertRaisesRegex(ValueError,'No supported render mesh'):
            read_preview_mesh(header_fixture())

    def test_skin_uses_companion_without_changing_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp); root=work/'files'/'characters'/'monsters'
            root.mkdir(parents=True)
            src=root/'goliath.skin'
            src.write_bytes(b'opaque-geometry-metadata')
            comp=root/'goliath.skinm'; comp.write_bytes(test_mesh())
            a=hashlib.sha256(src.read_bytes()).digest(); b=hashlib.sha256(comp.read_bytes()).digest()
            mesh=find_preview_mesh(work,'characters/monsters/goliath.skin',src.read_bytes())
            self.assertEqual(mesh.source_name,'characters/monsters/goliath.skinm')
            self.assertEqual((hashlib.sha256(src.read_bytes()).digest(),hashlib.sha256(comp.read_bytes()).digest()),(a,b))
            comp.unlink()
            with self.assertRaisesRegex(ValueError,'companion'):
                find_preview_mesh(work,'characters/monsters/goliath.skin',src.read_bytes())

    def test_models_tab_embeds_interactive_preview(self):
        import tkinter as tk
        from dark_theme import apply_theme
        from workspace_editor import WorkspaceEditor
        try: root=tk.Tk()
        except tk.TclError as error:self.skipTest(str(error))
        root.withdraw()
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Path(tmp)
            file=workspace/'files'/'goliath.skinm';file.parent.mkdir(parents=True);file.write_bytes(test_mesh())
            (workspace/'.evolve-pak-workspace.json').write_text(json.dumps({'entries':[{'path':'goliath.skinm','format':'raw'}]}))
            apply_theme(root)
            manager=SimpleNamespace(window=root,busy=False,t=lambda key:key)
            editor=None
            try:
                with patch('workspace_editor.messagebox.showerror') as error_popup:
                    editor=WorkspaceEditor(manager,workspace)
                    editor.tabs.select(editor.tab_frames['models']);root.update()
                    tree=editor.trees['models'];tree.selection_set(editor.path_items['models']['goliath.skinm']);root.update()
                    self.assertEqual(editor.view_mode,'model')
                    self.assertIsNotNone(editor.model_preview.mesh)
                    self.assertTrue(editor.model_preview.frame.winfo_manager())
                    self.assertEqual(len(editor.model_preview.mesh.triangles),4)
                    error_popup.assert_not_called()
                    editor.model_preview.yaw+=1; editor.model_preview.render()
                    self.assertTrue(editor.model_preview._photo is not None or editor.model_preview._gpu is not None)
            finally:
                if editor:editor.window.destroy()
                root.destroy()

if __name__=='__main__':unittest.main()
