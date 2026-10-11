"""Regression tests for workflow and isolated Developer diagnostics."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
from tempfile import TemporaryDirectory
import unittest
import urllib.request
from unittest.mock import patch

from workflow_lab import (
 preset_from_xml,apply_preset,search_text,replace_text_preview,apply_text_preview,
 save_recovery,read_recovery,clear_recovery,project_conflicts,
 cache_asset_manifest)
from developer_runtime_lab import parse_aob,signature_matches,compare_snapshot,MemorySnapshot
from developer_network_lab import parse_relay_log,simulate_network_conditions,parse_protocol_fixture
from developer_private_services import LocalFixtureService
from material_editor_v2 import reference_fields,material_adjust
from asset_geometry_cache import cached_geometry
from model_preview import PreviewMesh


def workspace(root,name,files):
    path=Path(root)/name
    (path/'files').mkdir(parents=True)
    rows=[]
    for rel,content in files.items():
        file=path/'files'/rel
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(content)
        rows.append({'path':rel,'format':'raw','sha256':hashlib.sha256(content).hexdigest()})
    (path/'.evolve-pak-workspace.json').write_text(json.dumps({
        'version':1,'source_pak':str(Path(root)/(name+'.pak')),
        'entries':rows}),encoding='utf-8')
    return path


class WorkflowLabTests(unittest.TestCase):
    def test_gameplay_presets_validate_source_before_apply(self):
        base='<Ability damage="150" cooldown="14.5" enabled="true"/>'
        preset=preset_from_xml(base,{0:'175',1:'12'})
        result=apply_preset(base,preset)
        self.assertIn('damage="175"',result)
        self.assertIn('cooldown="12"',result)
        with self.assertRaises(ValueError):
            apply_preset(base.replace('150','200'),preset)
        with self.assertRaises(ValueError):
            preset_from_xml(base,{0:'not a number'})

    def test_global_search_preview_and_backup(self):
        with TemporaryDirectory() as tmp:
            root=workspace(tmp,'a',{'libs/ability.xml':b'<Ability damage="100"/>'})
            hits=search_text(root,'damage')
            self.assertEqual(len(hits),1)
            plan=replace_text_preview(root,'100','250',['libs/ability.xml'])
            self.assertIn('250',plan[0][2])
            changed=apply_text_preview(root,plan)
            self.assertEqual(changed,('libs/ability.xml',))
            self.assertIn(b'250',(root/'files'/'libs'/'ability.xml').read_bytes())
            self.assertTrue((root/'EditorBackups').exists())
            with self.assertRaises(ValueError):
                apply_text_preview(root,plan)

    def test_recovery_is_baseline_bound(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            original=b'<Root/>'
            save_recovery(root,'data.xml','<Root edit="1"/>',original)
            self.assertIn('edit=',read_recovery(root,'data.xml',original))
            self.assertIsNone(read_recovery(root,'data.xml',b'<Root edited="true"/>'))
            clear_recovery(root,'data.xml')
            self.assertIsNone(read_recovery(root,'data.xml',original))

    def test_conflicts_and_manifest_cache(self):
        with TemporaryDirectory() as tmp:
            a=workspace(tmp,'a',{'a.xml':b'<A health="100"/>'})
            b=workspace(tmp,'b',{'a.xml':b'<A health="200"/>'})
            result=project_conflicts(a,b)
            self.assertEqual(len(result),1)
            cache=Path(tmp)/'cache'
            first,hit=cache_asset_manifest(a,cache)
            self.assertFalse(hit)
            second,hit=cache_asset_manifest(a,cache)
            self.assertTrue(hit)
            self.assertEqual(first,second)

    def test_material_editor_preserves_structure(self):
        source='<Material Diffuse="1.0"><Textures><Texture Map="Diffuse" File="test/a.dds"/></Textures></Material>'
        refs=reference_fields(source)
        self.assertEqual(refs[0].value,'test/a.dds')
        adjusted=material_adjust(source,{0:'0.8'},{0:'test/b.dds'})
        self.assertIn('Diffuse="0.8"',adjusted)
        self.assertIn('File="test/b.dds"',adjusted)
        with self.assertRaises(ValueError):
            material_adjust(source,{}, {0:'../../bad.dds'})

    def test_gpu_geometry_cache_not_stale(self):
        with TemporaryDirectory() as tmp:
            mesh=PreviewMesh(
                vertices=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)),
                triangles=((0,1,2),),center=(0.,0.,0.),radius=1.,
                source_name='test.skinm',
                uvs=((0.,0.),(1.,0.),(0.,1.)))
            first,hit=cached_geometry(mesh,tmp)
            self.assertFalse(hit)
            later,hit=cached_geometry(mesh,tmp)
            self.assertTrue(hit)
            self.assertEqual(first.vertices.shape,later.vertices.shape)


class DeveloperLabTests(unittest.TestCase):
    def test_wildcard_aob_snapshot(self):
        signature=parse_aob('DE AD ?? EF')
        self.assertEqual(signature,(0xde,0xad,None,0xef))
        self.assertEqual(signature_matches(
            bytes.fromhex('00 DE AD 01 EF 05 DE AD FF EF'),0x1000,signature),
            (0x1001,0x1006))
        with self.assertRaises(ValueError):
            parse_aob('?? ??')

    def test_memory_snapshot_diff_is_bounded(self):
        a=MemorySnapshot(5,0x100,b'\0\1\2',1.,'a')
        b=MemorySnapshot(5,0x100,b'\0\3\2',2.,'b')
        result=compare_snapshot(a,b)
        self.assertEqual(result['diffs'],((0x101,1,3),))
        with self.assertRaises(ValueError):
            compare_snapshot(a,MemorySnapshot(6,0x100,b'\0\3\2',2.,'b'))

    def test_relay_parser_redacts_secrets_and_ip(self):
        events=parse_relay_log('relay connection failed 203.0.113.8 token=private')
        self.assertEqual(len(events),1)
        self.assertTrue(events[0]['error'])
        self.assertNotIn('private',events[0]['message'])
        self.assertNotIn('203.0.113.8',events[0]['message'])

    def test_private_simulator_never_sends_packets(self):
        report=simulate_network_conditions(['a','b','c'],latency_ms=120,loss_percent=5)
        self.assertEqual(len(report),3)
        self.assertTrue(all('expected_delay_ms' in item for item in report))
        obj=parse_protocol_fixture(b'{"type":"test"}','JSON')
        self.assertEqual(obj['message_type'],'structured JSON')

    def test_loopback_fixture_service(self):
        service=LocalFixtureService({'/health':{'status':'ok'}})
        try:
            endpoint=service.start()
            self.assertTrue(endpoint.startswith('http://127.0.0.1:'))
            with urllib.request.urlopen(endpoint+'/health',timeout=2) as response:
                result=json.loads(response.read())
            self.assertEqual(result['status'],'ok')
        finally:
            service.stop()


if __name__=='__main__':
    unittest.main()
