"""Asset Studio 2.0 read-only parsing, safe editing and batch export tests."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from studio_tools import numeric_fields, apply_numeric_edits, asset_search, batch_export
from model_asset import is_model


class AssetStudioTests(unittest.TestCase):
    def test_visual_numeric_controls_only_replace_selected_attributes(self):
        original='<Actor speed="5.00" enabled="true"><Ability name="Rock" damage="200" cooldown="7.5" /></Actor>'
        values=numeric_fields(original)
        self.assertEqual([f.attribute for f in values],['speed','damage','cooldown'])
        changed=apply_numeric_edits(original,{0:'8.5',2:'12'})
        self.assertEqual(changed,original.replace('speed="5.00"','speed="8.5"').replace(
            'cooldown="7.5"','cooldown="12"'))
        self.assertIn('damage="200"',changed)
        self.assertEqual(apply_numeric_edits(original,{}),original)

    def test_visual_xml_rejects_structure_change_and_invalid_values(self):
        original='<Root><Node speed="2" offset="-1.5e+2"/></Root>'
        with self.assertRaises(ValueError):
            apply_numeric_edits(original,{0:'nan'})
        with self.assertRaises(ValueError):
            apply_numeric_edits(original,{0:'1e309'})
        with self.assertRaises(ValueError):
            apply_numeric_edits(original,{3:'1'})
        with self.assertRaises(ValueError):
            numeric_fields('<Root><!DOCTYPE fake><Node speed="2"/></Root>')
        with self.assertRaises(ValueError):
            numeric_fields('<Root speed="5"><Node/></Root><Bad/>')

    def _workspace(self, root, name, files):
        ws=root/name
        (ws/'files').mkdir(parents=True)
        entries=[]
        for rel,content in files.items():
            path=ws/'files'/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
            entries.append({'path':rel,'size':len(content)})
        (ws/'.evolve-pak-workspace.json').write_text(
            json.dumps({'entries':entries}),encoding='utf-8')
        return ws

    def test_asset_search_and_batch_copy_never_overwrite(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            one=self._workspace(root,'one',{'characters/hunter.skinm':b'model', 'scripts/test.xml':b'<Root/>'})
            two=self._workspace(root,'two',{'characters/hunter.skinm':b'other'})
            catalog={'Main Data':one,'Companion':two}
            matched=asset_search(catalog,'hunter',suffix='.skinm')
            self.assertEqual(2,len(matched))
            self.assertEqual({label for label,_,_,_ in matched},{'Main Data','Companion'})
            export=root/'exports'
            export.mkdir()
            selected=[(label,ws,path) for label,ws,path,_ in matched]
            first=batch_export(catalog,selected,export)
            self.assertEqual(2,sum(x['status']=='exported' for x in first))
            again=batch_export(catalog,selected,export)
            self.assertEqual(2,sum(x['status']=='skipped' for x in again))
            self.assertEqual(b'model',(export/'Main Data'/'characters'/'hunter.skinm').read_bytes())
            self.assertEqual(b'other',(export/'Companion'/'characters'/'hunter.skinm').read_bytes())

    def test_batch_rejects_unknown_source_and_unsafe_asset_paths(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            known=self._workspace(root,'known',{'sub/test.txt':b'ok'})
            dest=root/'out'
            dest.mkdir()
            with self.assertRaises(ValueError):
                batch_export({'known':known},[('unknown',root/'other','test.txt')],dest)
            report=batch_export({'known':known},[('known',known,'../escape.txt')],dest)
            self.assertEqual('skipped',report[0]['status'])
            self.assertFalse((root/'escape.txt').exists())


if __name__=='__main__':
    unittest.main()
