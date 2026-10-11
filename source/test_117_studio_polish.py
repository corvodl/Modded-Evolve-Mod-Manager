"""Read-only v1.1.17 studio regression tests without GPU/game dependencies."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import tempfile
import unittest

import numpy as np
from studio_polish_117 import (
    theme_preset, validate_theme_preset, read_theme_file,
    sanitized_project_session, project_session_key,
    prepare_model_cached, clear_model_cache, model_cache_stats,
    export_preflight)
from renderer_3 import ordered_transparency_batches


class PresetTests(unittest.TestCase):
    def test_roundtrip_and_reject_invalid_color(self):
        preset=theme_preset('Red Studio','Dark',{'accent':'#44aabb'})
        self.assertEqual(validate_theme_preset(preset),preset)
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'preset.json'
            target.write_text(json.dumps(preset),encoding='utf-8')
            self.assertEqual(read_theme_file(target),preset)
            target.write_text(json.dumps({**preset,'format':'wrong'}))
            with self.assertRaises(ValueError):
                read_theme_file(target)
        with self.assertRaises(ValueError):
            theme_preset('Bad','Light',{'accent':'javascript:alert(1)'})

    def test_session_sanitizes_camera_and_untrusted_filenames(self):
        with tempfile.TemporaryDirectory() as td:
            key=project_session_key(td)
            self.assertEqual(len(key),24)
            state=sanitized_project_session({
                'tab':2,'selected':{'models':'safe.skinm','files':'../unsafe'},
                'sizes':{'models':[200,900],'files':[-2,10]},
                'camera':{'rotation':[10,-20],'pan':[float('nan'),4],'zoom':2.0}},
                {'safe.skinm':{}})
            self.assertEqual(state['selected'],{'models':'safe.skinm'})
            self.assertEqual(state['sizes'],{'models':[200,900]})
            self.assertEqual(state['camera']['zoom'],2.)
            self.assertNotIn('pan',state['camera'])


class CacheTests(unittest.TestCase):
    def test_cache_hits_and_source_change_invalidation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'files').mkdir()
            model=root/'files'/'a.skinm'
            model.write_bytes(b'old')
            (root/'.evolve-pak-workspace.json').write_text('{"version":1}')
            counter=[0]
            def fake_resolve(*args,**kw):
                counter[0]+=1
                return {'raw':b'abc','geometry':None,'materials':None}
            clear_model_cache()
            with patch('async_model_resolve.resolve_model',fake_resolve):
                first,hit,_=prepare_model_cached(root,'a.skinm')
                self.assertFalse(hit)
                second,hit,_=prepare_model_cached(root,'a.skinm')
                self.assertTrue(hit)
                self.assertEqual(counter[0],1)
                model.write_bytes(b'updated-longer')
                third,hit,_=prepare_model_cached(root,'a.skinm')
                self.assertFalse(hit)
                self.assertEqual(counter[0],2)
                self.assertEqual(model_cache_stats()['entries'],2)
                clear_model_cache(root)
                self.assertEqual(model_cache_stats()['entries'],0)


class ExportTests(unittest.TestCase):
    def test_preflight_reports_only_verified_properties(self):
        mesh=SimpleNamespace(vertices=[1,2,3],triangles=[(0,1,2)])
        refs=[SimpleNamespace(name='idle',kind='caf',available=True)]
        fake_report={'texture_count':2,'texture_status':'2 diffuse maps',
                     'rig_status':'Rigged: 15 bones','bone_count':15}
        with patch('model_textured_export.textured_model_from_workspace',
                   return_value=(b'glb-bytes',fake_report)),\
             patch('model_textured_export._unpack_glb',
                   return_value=({'skins':[{'joints':list(range(15))}]},b'')),\
             patch('multi_pak_assets.model_material_links',
                   return_value={'status':'found'}):
            result=export_preflight(mesh,'.','model.skinm',animation_refs=refs)
            self.assertEqual(result['bones'],15)
            self.assertEqual(result['textures'],2)
            self.assertEqual(result['animation_candidates'],1)
            self.assertIn('Candidates only',result['animation_note'])


class RenderingTests(unittest.TestCase):
    def test_translucent_draws_follow_opaque_and_sort_by_depth(self):
        verts=np.array([[0,0,1],[0,0,-5],[1,0,0]],dtype=np.float32)
        idx=np.array([0,1,2],dtype=np.uint32)
        draws=[(0,1,1),(1,1,1),(2,1,0)]
        order=ordered_transparency_batches(draws,idx,verts,{1},(0,0))
        self.assertEqual(order,[(2,1,0),(1,1,1),(0,1,1)])


if __name__ == '__main__':
    unittest.main()
