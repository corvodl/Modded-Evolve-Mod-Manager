"""Synthetic signed-PAK provider tests. No game files or keys are included."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from threading import Event
from unittest.mock import patch

from level_dependency_loader import (
    ensure_map_archives, map_archive_catalog, needed_virtual_paths,
    providers_for_missing)


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
