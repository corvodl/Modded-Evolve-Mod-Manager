"""Non-invasive v1.1.16 regression checks (no live game or GPU required)."""
import json
from pathlib import Path
import tempfile
import unittest

from qt_theme_116 import PALETTES, palette, qt_palette_style
from quality_lab_116 import (
    project_health, benchmark_workspace, animation_playlist,
    validate_animation_playlist, compare_animation_clips)
from developer_diagnostics_116 import (
    make_watch_profile, resolve_watch_profile, relay_timeline,
    validate_private_scenario, run_private_scenario)


class ThemeTests(unittest.TestCase):
    def test_modes_and_palette_validation(self):
        self.assertIn('Light',PALETTES)
        self.assertIn('Dark',PALETTES)
        self.assertEqual(palette('Dark',{'accent':'#12aBcD'})['accent'],'#12abcd')
        with self.assertRaises(ValueError):
            palette('Dark', {'accent':'red; background:black'})
        with self.assertRaises(ValueError):
            palette('Unsafe')
        css=qt_palette_style('QWidget { background:#0b0b0d; color:#f5f3f6; }',
                             'Light', {'accent':'#2255aa'})
        self.assertIn('#f5f6f8',css)
        self.assertIn('#2255aa',css)


class QualityTests(unittest.TestCase):
    def _project(self,root):
        (root/'files').mkdir()
        (root/'files'/'test.xml').write_text('<data health="120"/>',encoding='utf-8')
        (root/'.evolve-pak-workspace.json').write_text(json.dumps({
            'version':1,'source_pak':'test.pak','entries':[{'path':'test.xml'}]
        }),encoding='utf-8')

    def test_project_health_and_benchmark(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self._project(root)
            health=project_health(root)
            self.assertEqual(health['status'],'Healthy')
            benchmark=benchmark_workspace(root)
            self.assertGreaterEqual(benchmark['files_sampled'],1)
            self.assertIn('synthetic',benchmark['disclaimer'])
            (root/'files'/'test.xml').write_text('<bad',encoding='utf-8')
            self.assertEqual(project_health(root)['xml_issues_count'],1)
            (root/'files'/'test.xml').unlink()
            self.assertEqual(project_health(root)['missing_count'],1)

    def test_playlist_matching_and_durations(self):
        value=animation_playlist(['idle','run','idle'],[('idle',0.5)],speed=1.5)
        self.assertEqual(value['clips'],['idle','run'])
        loaded,available,missing=validate_animation_playlist(value,{'idle'})
        self.assertEqual(available,['idle'])
        self.assertEqual(missing,['run'])
        durations=compare_animation_clips({'idle':(2,), 'run':(1,)}, 'idle','run')
        self.assertEqual(durations['duration_difference'],1)
        with self.assertRaises(ValueError):
            validate_animation_playlist({'format':'evolve-animation-playlist','version':999},[])


class DeveloperDiagnosticsTests(unittest.TestCase):
    def test_relocatable_watch_requires_correct_executable(self):
        build={'sha256':'a'*64,'size':100}
        mod=[{'name':'Evolve.exe','base':0x1000,'size':0x1000}]
        profile=make_watch_profile([('Health',0x1100,'Float')],build,mod)
        self.assertEqual(profile['watches'][0]['offset'],0x100)
        moved=[{'name':'Evolve.exe','base':0x5000,'size':0x1000}]
        self.assertEqual(resolve_watch_profile(profile,build,moved),
                         [('Health',0x5100,'Float')])
        with self.assertRaises(ValueError):
            resolve_watch_profile(profile,{'sha256':'b'*64,'size':100},moved)
        with self.assertRaises(ValueError):
            make_watch_profile([('Heap',0x9000,'Float')],build,mod)

    def test_local_protocol_simulator_has_no_network(self):
        scenario={'format':'evolve-private-scenario-v1','steps':[
            {'path':'/health','status':200,'body':{'ok':True},'delay_ms':10},
            {'path':'/relay','drop':True,'delay_ms':500}]}
        report=run_private_scenario(scenario)
        self.assertEqual(report['dropped'],1)
        self.assertEqual(report['total_delay_ms'],510)
        with self.assertRaises(ValueError):
            validate_private_scenario({'format':'evolve-private-scenario-v1',
                                       'steps':[{'path':'http://remote.test'}]})

    def test_relay_timeline_redacts_addresses_and_orders_by_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            first=Path(td)/'a.log'
            second=Path(td)/'b.log'
            first.write_text('11:30:03 relay connected 1.2.3.4\n',encoding='utf-8')
            second.write_text('11:30:01 party created token=abcdef\n',encoding='utf-8')
            result=relay_timeline([first,second])
            self.assertEqual(result['mode'],'timestamp')
            self.assertEqual(result['events'][0]['stage'],'party')
            self.assertNotIn('1.2.3.4',json.dumps(result))
            self.assertNotIn('abcdef',json.dumps(result))


if __name__ == '__main__':
    unittest.main()
