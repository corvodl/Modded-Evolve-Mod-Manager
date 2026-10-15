"""Synthetic signed-PAK provider tests. No game files or keys are included."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from threading import Event
from unittest.mock import patch

from level_dependency_loader import (
    ensure_map_archives, map_archive_catalog, needed_virtual_paths,
    providers_for_missing, texture_provider_candidates, material_dependency_candidates,
    copy_editable_map_from_library, discover_map_status)


class LevelDependencyTests(unittest.TestCase):
    def test_catalog_covers_both_source_and_compiled_map_archives(self):
        catalog = map_archive_catalog([
            Path('/game/Game/levels_Tutorial.pak'),
            Path('/game/Game/levels/Tutorial/level.pak'),
            Path('/game/Game/levels/Tutorial/terraintexture.pak'),
            Path('/game/Game/levels_Desert_Canyon.pak'),
            Path('/game/Game/objects_basic_props.pak')])
        self.assertEqual(len(catalog['tutorial']), 3)
        self.assertEqual(len(catalog['desert_canyon']), 1)
        self.assertNotIn('objects_basic_props', catalog)

    def test_reference_search_is_case_insensitive_and_exact(self):
        scene = [SimpleNamespace(model='Objects/Tree/Tree.CGF', material='Objects/Tree/Bark.MTL'),
                 SimpleNamespace(model='Objects/Tree/Tree.cgf', material='')]
        actual = needed_virtual_paths(SimpleNamespace(scene=scene))
        self.assertEqual(actual, {'objects/tree/tree.cgf','objects/tree/bark.mtl'})

    def test_cooked_texture_candidates_keep_material_directory(self):
        candidates = texture_provider_candidates(
            'rock_diff.tif','objects/natural/rocks/rock.mtl')
        self.assertEqual(candidates[:3], (
            'objects/natural/rocks/rock_diff.tif',
            'objects/natural/rocks/rock_diff.dds',
            'objects/natural/rocks/rock_diff.dds.0'))
        self.assertNotIn('rock_diff.dds', candidates)

    @patch('model_material_resolve.find_model_material')
    def test_mtl_texture_dependencies_are_exact_and_lazy(self, find_mtl):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            material = Path(temp)/'stone.mtl'
            material.write_text(
                '<Material><Textures><Texture Map="Diffuse" '
                'File="stone_d.tif"/><Texture Map="Bumpmap" '
                'File="stone_n.tif"/></Textures></Material>',encoding='utf-8')
            find_mtl.return_value = ({
                'file':material,'relative':'objects/stone/stone.mtl'},'mesh hint',())
            project = SimpleNamespace(
                workspace=Path(temp),
                scene=(SimpleNamespace(model='objects/stone/stone.cgf',material=''),))
            groups = material_dependency_candidates(project,{
                'objects/stone/stone.cgf':[{'file':Path(temp)/'stone.cgf'}]})
            self.assertEqual(len(groups),2)
            self.assertIn('objects/stone/stone_d.dds.0',groups[0])
            self.assertIn('objects/stone/stone_n.dds',groups[1])

    def test_atomic_editable_copy_is_independent_of_asset_library(self):
        from tempfile import TemporaryDirectory
        import hashlib
        import json
        with TemporaryDirectory() as home:
            root = Path(home)
            folder = root/'library'/('level.pak__' + 'f'*20)
            original = folder/'files'/'mission_mission0.xml'
            original.parent.mkdir(parents=True)
            raw = (b'<Mission><Objects><Entity Name="Tree" Id="1" '
                   b'Pos="0,0,0" /></Objects></Mission>')
            original.write_bytes(raw)
            info = folder/'files'/'levelinfo.xml'
            info.write_bytes(b'<LevelInfo Name="Tutorial" />')
            entries=[]
            for path in (original,info):
                value=path.read_bytes()
                entries.append({
                    'path':path.name,'bytes':len(value),
                    'sha256':hashlib.sha256(value).hexdigest()})
            (folder/'asset-library.json').write_text(json.dumps({
                'version':1,'name':'level.pak',
                'source':str(root/'Game'/'levels'/'Tutorial'/'level.pak'),
                'entries':entries}),encoding='utf-8')
            target=root/'projects'/'Level-tutorial'
            copy_editable_map_from_library(
                'tutorial',target,library_roots=(root/'library',))
            modified=target/'files'/'mission_mission0.xml'
            modified.write_bytes(b'<Mission/>')
            self.assertEqual(original.read_bytes(),raw)
            with self.assertRaises(ValueError):
                from evolve_pak_workspace import cmd_build
                cmd_build(SimpleNamespace(workspace=target))
            self.assertEqual(copy_editable_map_from_library(
                'tutorial',target,library_roots=(root/'library',)),target)

    @patch('level_dependency_loader.material_dependency_candidates')
    @patch('level_dependency_loader.providers_for_missing')
    @patch('level_asset_mount.mount_map_index')
    @patch('level_dependency_loader.import_pak')
    @patch('level_dependency_loader.imported_archives')
    @patch('level_dependency_loader.discover_game_paks')
    def test_existing_mesh_still_traces_missing_material_texture(
            self, scan, imported, importer, mounted, providers, materials):
        mesh = Path('/game/levels/Tutorial/level.pak')
        shared = Path('/game/objects_basic_props.pak')
        scan.return_value=SimpleNamespace(files=(mesh,shared))
        imported.side_effect=lambda root: (
            ({'source':str(mesh)},) if str(root)=='/library' else ())
        mounted.return_value=({'objects/tree/tree.cgf':[{'file':'imported'}]}, {})
        materials.return_value=(('objects/tree/diff.dds.0',),)
        providers.return_value={'objects/tree/diff.dds.0':[shared]}
        importer.return_value={'status':'imported','name':shared.name}
        mounted.side_effect=lambda *a,**kw: ({'objects/tree/tree.cgf':[{'file':'imported'}],
            **({'objects/tree/diff.dds.0':[{'file':'imported texture'}]} if importer.called else {})}, {})
        project=SimpleNamespace(level_name='Tutorial',workspace=Path('/project'),
                                scene=(SimpleNamespace(model='objects/tree/tree.cgf',
                                                      material=''),))
        result=ensure_map_archives(
            project,'/game','/stage','/cache',imported_root='/library')
        self.assertEqual(result['imported'],[shared.name])
        self.assertEqual(importer.call_count,1)
        self.assertFalse(result['missing'])

    @patch('level_dependency_loader.material_dependency_candidates')
    @patch('level_dependency_loader.providers_for_missing')
    @patch('level_asset_mount.mount_map_index')
    @patch('level_dependency_loader.import_pak')
    @patch('level_dependency_loader.imported_archives')
    @patch('level_dependency_loader.discover_game_paks')
    def test_dds_fragments_in_different_signed_paks_are_both_imported(
            self, scan, imported, importer, mounted, providers, materials):
        p0=Path('/game/objects_basic_props.pak')
        p1=Path('/game/textures.pak')
        scan.return_value=SimpleNamespace(files=(p0,p1))
        imported.return_value=()
        mounted.return_value=({'objects/rock/rock.cgf':[{'file':'preimported'}]}, {})
        materials.return_value=(('objects/rock/rock_d.dds.0',),)
        providers.return_value={
            'objects/rock/rock_d.dds.0':[p0],
            'objects/rock/rock_d.dds.1':[p1]}
        importer.return_value={'status':'imported'}
        project=SimpleNamespace(level_name='Tutorial',workspace=Path('/project'),
                                scene=(SimpleNamespace(model='objects/rock/rock.cgf',
                                                      material=''),))
        result=ensure_map_archives(project,'/game','/stage','/cache')
        self.assertEqual(set(result['imported']),{p0.name,p1.name})
        self.assertNotIn('DDS fragment: objects/rock/rock_d.dds.1',result['missing'])
        self.assertTrue(providers.call_args.kwargs['stream_roots'])

    @patch('level_dependency_loader._keys', return_value=('key',))
    @patch('level_dependency_loader._verify_pak')
    def test_verified_provider_matching_and_other_map_fencing(self, verified, keys):
        verified.side_effect = lambda source, _keys: (
            {'entries': {'objects/Bark/Bark.mtl': {},
                         'objects/tree/tree.cgf': {}, 'objects/tree/tree.cgf.1': {}}},
            None, source.name)
        candidates = [Path('/game/objects_min_props.pak'),
                      Path('/game/objects_basic_props.pak'),
                      Path('/game/levels_Desert_Canyon.pak')]
        found = providers_for_missing(
            candidates, {'objects/tree/tree.cgf', 'objects/bark/bark.mtl'},
            stage='/mock')
        self.assertEqual(len(found['objects/tree/tree.cgf']), 2)
        self.assertEqual(verified.call_count, 2)
        self.assertNotIn(Path('/game/levels_Desert_Canyon.pak'),
                         found['objects/tree/tree.cgf'])

    @patch('level_dependency_loader._keys', return_value=('key',))
    @patch('level_dependency_loader._verify_pak')
    def test_cancelled_inventory_makes_no_import(self, verified, keys):
        canceled = Event()
        canceled.set()
        self.assertIsNone(providers_for_missing(
            [Path('/game/objects_basic_props.pak')], {'objects/tree/tree.cgf'},
            stage='/mock', cancel=canceled))
        verified.assert_not_called()

    @patch('level_dependency_loader.providers_for_missing')
    @patch('level_dependency_loader.mount_map_index', create=True)
    @patch('level_asset_mount.mount_map_index')
    @patch('level_dependency_loader.import_pak')
    @patch('level_dependency_loader.imported_archives', return_value=())
    @patch('level_dependency_loader.discover_game_paks')
    def test_import_missing_exact_shared_archive_once(
            self, scan, imported, importer, mounted, unused, providers):
        sources = (Path('/game/levels/Tutorial/level.pak'),
                   Path('/game/levels_Tutorial.pak'),
                   Path('/game/objects_basic_props.pak'))
        scan.return_value = SimpleNamespace(files=sources)
        mounted.return_value = ({}, {})
        providers.return_value = {
            'objects/tree/tree.cgf': [sources[2]]}
        importer.side_effect = lambda source, **kw: {
            'status': 'imported', 'name': source.name}
        obj = SimpleNamespace(model='objects/tree/tree.cgf', material='')
        project = SimpleNamespace(level_name='Tutorial',workspace=Path('/tmp/copy'),
                                  scene=(obj,))
        report = ensure_map_archives(project, '/game', '/stage',
                                     '/managed-cache', imported_root='/library')
        self.assertEqual(importer.call_count, 3)
        self.assertEqual(set(report['imported']),
                         {'levels_Tutorial.pak','level.pak','objects_basic_props.pak'})
        self.assertFalse(report['ambiguous'])
        providers.assert_called_once()

    @patch('level_dependency_loader.providers_for_missing')
    @patch('level_asset_mount.mount_map_index', return_value=({}, {}))
    @patch('level_dependency_loader.import_pak')
    @patch('level_dependency_loader.imported_archives', return_value=())
    @patch('level_dependency_loader.discover_game_paks')
    def test_shared_collision_refused_instead_of_guessing(
            self, scan, imported, importer, mounted, providers):
        a,b = Path('/game/objects_basic_props.pak'),Path('/game/objects_min_props.pak')
        scan.return_value = SimpleNamespace(files=(a,b))
        providers.return_value = {'objects/tree.cgf':[a,b]}
        project = SimpleNamespace(
            level_name='Tutorial', workspace=Path('/tmp/copy'),
            scene=(SimpleNamespace(model='objects/tree.cgf',material=''),))
        report = ensure_map_archives(project,'/game','/stage','/cache')
        self.assertEqual(importer.call_count,0)
        self.assertEqual(len(report['ambiguous']),1)
        self.assertIn('objects/tree.cgf',report['ambiguous'][0])


if __name__ == '__main__':
    unittest.main()
