"""Multi-PAK isolated extraction and cross-archive material lookup tests."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from multi_pak_assets import (create_batch, load_collection, collection_for_workspace,
                              model_material_links, _resolve, _asset_index)
from test_editor_refresh import fixture


class AssetBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.stage=self.root/'stage'
        self.paks=self.stage/'paks'/'Game'
        self.paks.mkdir(parents=True)
        self.pubkey=rsa.generate_private_key(public_exponent=65537,key_size=1024)
        self.pub=self.root/'public_key.bin'
        self.pub.write_bytes(self.pubkey.public_key().public_bytes(Encoding.DER,PublicFormat.PKCS1))
        self.first=self.paks/'props.pak'
        self.second=self.paks/'texture.pak'
        for p in (self.first,self.second):fixture(p,self.pubkey)
        (self.stage/'rekey_plan.json').write_text(json.dumps({'blocked':[],
            'entries':[{'path':'Game/'+p.name,'kind':'signed-encrypted'} for p in (self.first,self.second)]}))
        self.batch=self.root/'Batch'
        self.before={p:p.read_bytes() for p in (self.first,self.second)}

    def extract(self):
        return create_batch(self.stage,self.pub,self.batch,['Game/props.pak','Game/texture.pak'])

    def test_separate_editable_projects_and_unchanged_signed_sources(self):
        data=self.extract()
        self.assertEqual(data['state'],'complete')
        self.assertEqual(len(data['archives']),2)
        self.assertNotEqual(data['archives'][0]['workspace'],data['archives'][1]['workspace'])
        paths=[]
        for row in data['archives']:
            ws=self.batch/row['workspace']
            paths.append(ws/'files'/'libs'/'Abilities'/'test.xml')
            self.assertTrue((ws/'.evolve-pak-workspace.json').is_file())
            self.assertIsNotNone(collection_for_workspace(ws))
        self.assertEqual(len(paths),2)
        paths[0].write_text('<changed/>')
        self.assertNotEqual(paths[0].read_bytes(),paths[1].read_bytes())
        self.assertEqual(self.before,{p:p.read_bytes() for p in (self.first,self.second)})
        catalog=load_collection(self.batch)
        index=_asset_index(self.batch,catalog)
        links,state=_resolve(index,'libs/Abilities/test.xml')
        self.assertEqual(state,'ambiguous')
        self.assertEqual(len(links),2)

    def test_safe_failure_for_duplicates_unknown_and_partial(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            create_batch(self.stage,self.pub,self.batch,['Game/props.pak','game/PROPS.pak'])
        with self.assertRaises(ValueError):
            create_batch(self.stage,self.pub,self.batch,['Game/props.pak','Game/missing.pak'])
        with self.assertRaisesRegex(ValueError,'filter'):
            create_batch(self.stage,self.pub,self.batch,['Game/props.pak','Game/texture.pak'],filters=['goliath'])
        self.assertFalse(self.batch.exists())
        with patch('multi_pak_assets.cmd_extract',side_effect=RuntimeError('simulated extraction failure')):
            with self.assertRaisesRegex(RuntimeError,'simulated'):
                self.extract()
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            load_collection(self.batch)
        self.assertEqual(self.before,{p:p.read_bytes() for p in (self.first,self.second)})

    def test_tif_refs_resolve_to_cooked_dds_across_archives(self):
        self.extract()
        data=load_collection(self.batch)
        a=self.batch/data['archives'][0]['workspace']
        b=self.batch/data['archives'][1]['workspace']
        model='characters/monsters/goliath/goliath1_lod1.skinm'
        material='characters/monsters/goliath/goliath1.mtl'
        tex='characters/monsters/goliath/textures/goliath1_diff.dds.0'
        def add_file(ws,rel,contents):
            path=ws/'files'/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(contents)
            manifest=ws/'.evolve-pak-workspace.json'
            m=json.loads(manifest.read_text());m['entries'].append({'path':rel,'format':'raw'})
            manifest.write_text(json.dumps(m))
        add_file(a,model,b'CrChF-not-needed-for-resource-matching')
        mat=ET.Element('Material');sub=ET.SubElement(mat,'Textures')
        ET.SubElement(sub,'Texture',{'Map':'Diffuse','File':'characters/monsters/goliath/textures/goliath1_diff.tif'})
        ET.SubElement(sub,'Texture',{'Map':'Specular','File':'characters/monsters/goliath/textures/goliath1_spec.tif'})
        add_file(b,material,ET.tostring(mat))
        add_file(b,tex,b'fixture')
        report=model_material_links(a,model)
        self.assertEqual(report['status'],'found')
        self.assertEqual(report['material_archive'],'Game/texture.pak')
        self.assertEqual([t['status'] for t in report['textures']],['found','missing'])
        self.assertEqual(report['textures'][0]['paths'],[tex])
        # No asset bytes are applied to the mesh automatically.
        self.assertEqual((a/'files'/model).read_bytes(),b'CrChF-not-needed-for-resource-matching')

    def test_cross_archive_model_companion_preview(self):
        from model_preview import find_preview_mesh
        from test_model_preview import test_mesh
        from multi_pak_assets import locate_batch_asset
        self.extract()
        data=load_collection(self.batch)
        a=self.batch/data['archives'][0]['workspace']
        b=self.batch/data['archives'][1]['workspace']
        skin='characters/monsters/goliath/goliath1.skin'
        companion=skin+'m'
        def add_file(ws,rel,content):
            file=ws/'files'/rel;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(content)
            manifest=ws/'.evolve-pak-workspace.json';m=json.loads(manifest.read_text())
            m['entries'].append({'path':rel,'format':'raw'});manifest.write_text(json.dumps(m))
        add_file(a,skin,b'character metadata')
        add_file(b,companion,test_mesh())
        mesh=find_preview_mesh(a,skin,b'character metadata')
        self.assertEqual((len(mesh.vertices),len(mesh.triangles)),(4,4))
        self.assertEqual(locate_batch_asset(a,companion),(b/'files'/companion).resolve())

    def test_invalid_lookup_and_ambiguous_material_ref(self):
        self.extract()
        data=load_collection(self.batch)
        a=self.batch/data['archives'][0]['workspace']
        self.assertIsNone(collection_for_workspace(self.root))
        index=_asset_index(self.batch,data)
        self.assertEqual(_resolve(index,'C:/Windows/System32/fake.dds')[1],'unsafe-path')
        self.assertEqual(_resolve(index,'../outside.dds')[1],'unsafe-path')
        self.assertEqual(_resolve(index,'textures/missing.tif')[1],'missing')

    def test_gui_ctrl_selection_returns_only_visible_archives(self):
        import tkinter as tk
        from tkinter import ttk
        from dark_theme import apply_theme
        from pak_manager_gui import Manager
        from ui_copy import load_text
        try: root=tk.Tk()
        except tk.TclError as e:self.skipTest(str(e))
        try:
            apply_theme(root)
            manager=Manager.__new__(Manager)
            manager.window=root;manager.copy,_=load_text()
            for name in ('search','archive_count','archive_label','current_workspace','external_pak','output_pak'):
                setattr(manager,name,tk.StringVar(master=root))
            manager.archive_entries=['Game/props.pak','Game/texture.pak','Game/other.pak']
            manager.visible=[]
            container=ttk.Frame(root);container.pack(fill='both',expand=True)
            manager.draw_edit(container);root.update()
            manager.refresh_list()
            manager.archives.selection_set(['Game/props.pak', 'Game/other.pak'])
            self.assertEqual(set(manager.selected_archive_relatives()),{'Game/props.pak','Game/other.pak'})
        finally: root.destroy()


if __name__=='__main__': unittest.main()
