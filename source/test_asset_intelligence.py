"""Regression tests for archive identity, project cleanup, diffs and preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from evolve_pak_workspace import VERSION
from project_selection import workspace_for_archive, workspace_matches
from project_lifecycle import find_old_projects, delete_abandoned_project
from asset_intelligence import compare_projects, text_difference, validate_project


def project(root, name, source, files):
    path=root/name
    extracted=path/'files'
    extracted.mkdir(parents=True)
    import hashlib
    rows=[]
    for rel,raw in files.items():
        target=extracted/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw)
        rows.append({'path':rel, 'archive_name':rel,'format':'raw',
                     'sha256':hashlib.sha256(raw).hexdigest()})
    (path/'.evolve-pak-workspace.json').write_text(
        json.dumps({'version':VERSION,'source_pak':str(source),
                    'entries':rows}),encoding='utf-8')
    return path


class PakLifecycleTests(unittest.TestCase):
    def test_doubleclick_never_opens_stale_other_project(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            stage=root/'stage'/'paks'
            stage.mkdir(parents=True)
            first=stage/'one.pak'
            second=stage/'two.pak'
            first.write_bytes(b'one')
            second.write_bytes(b'two')
            old=project(root,'old',first,{'libs/a.xml':b'<A/>'})
            self.assertTrue(workspace_matches(old,first))
            self.assertFalse(workspace_matches(old,second))
            self.assertIsNone(workspace_for_archive(
                root/'stage','two.pak',old))
            new=project(root,'new',second,{'libs/b.xml':b'<B/>'})
            self.assertEqual(workspace_for_archive(
                root/'stage','two.pak',old,{'two.pak':new}),new)
            self.assertIsNone(workspace_for_archive(
                root/'stage','one.pak',new))
            self.assertFalse(workspace_matches(root/'absent',first))

    def test_old_unmodified_projects_need_explicit_deletion(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            projects=root/'Projects'
            projects.mkdir()
            source=root/'a.pak'
            source.write_bytes(b'pak')
            stale=project(projects,'stale',source,{'file.xml':b'<A/>'})
            edited=project(projects,'edited',source,{'file.xml':b'<A/>'})
            (edited/'files'/'file.xml').write_bytes(b'<B/>')
            # Mark all files and nested directories as old to simulate
            # unused projects; scan itself must never mutate or delete.
            old=time.time()-100*86400
            for base in (stale,edited):
                for directory,dirs,files in os.walk(base):
                    for name in files:
                        os.utime(Path(directory)/name,(old,old))
                    for name in dirs:
                        os.utime(Path(directory)/name,(old,old))
            projects_list=find_old_projects(projects,now=time.time())
            self.assertEqual(2,len(projects_list))
            listed={p.path.name:p for p in projects_list}
            self.assertTrue(listed['stale'].eligible)
            self.assertFalse(listed['edited'].eligible)
            self.assertTrue(stale.exists())
            with self.assertRaises(ValueError):
                delete_abandoned_project(projects,edited)
            with self.assertRaises(ValueError):
                delete_abandoned_project(projects,stale,active=(stale,))
            saved=delete_abandoned_project(projects,stale)
            self.assertGreater(saved,0)
            self.assertFalse(stale.exists())
            self.assertTrue(edited.exists())
            self.assertTrue(source.is_file())

    def test_compare_extracted_pak_projects_and_xml_text(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            left=project(root,'left',root/'original.pak',
                {'libs/hunter.xml':b'<Actor health="100"/>',
                 'same.bin':b'unchanged','gone.bin':b'gone'})
            right=project(root,'right',root/'modified.pak',
                {'libs/hunter.xml':b'<Actor health="125"/>',
                 'same.bin':b'unchanged','new.bin':b'added'})
            result=dict((path,state) for state,path in compare_projects(left,right))
            self.assertEqual(result['libs/hunter.xml'],'Modified')
            self.assertEqual(result['gone.bin'],'Removed')
            self.assertEqual(result['new.bin'],'Added')
            self.assertNotIn('same.bin',result)
            diff=text_difference(left,right,'libs/hunter.xml')
            self.assertIn('health="100"',diff)
            self.assertIn('health="125"',diff)

    def test_validation_rejects_malformed_modified_xml(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            extracted=project(root,'p',root/'pak.pak',
                              {'test.xml':b'<Root health="100"/>'})
            modified,issues=validate_project(extracted)
            self.assertEqual(modified,())
            self.assertFalse(issues)
            (extracted/'files'/'test.xml').write_bytes(b'<Root')
            modified,issues=validate_project(extracted)
            self.assertEqual(modified,('test.xml',))
            self.assertTrue(issues)


if __name__=='__main__':
    unittest.main()
