"""Beta 17 regressions: human-readable PAK folders and correct character DDS ownership.

Uses synthetic DDS/material fixtures only. No game assets are included.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

import asset_library
from asset_library_preview import archive_folder, archive_entries, library_model_index
from character_aliases import (
    CHARACTER_ALIASES, character_alias, character_asset_description,
    pretty_folder)
from material_preview import load_preview_materials
from model_material_resolve import find_model_material
from multi_pak_assets import resolve_material_texture
from pak_dependencies import group_title
from test_material_preview import uv_mesh


class ReadableArchiveFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'AssetLibrary'
        self.root.mkdir()
        self.id = 'a' * 20
        self.original_name = 'characters_monsters_teleporter_ts.pak'
        self.folder = self.root / self.id
        self.folder.mkdir()
        self.virtual = 'characters/monsters/teleporter/textures/diff.dds.0'
        target = self.folder / 'files' / self.virtual
        target.parent.mkdir(parents=True)
        target.write_bytes(b'test')
        (self.folder / asset_library.MANIFEST).write_text(json.dumps({
            'version': 1, 'name': self.original_name,
            'source': 'C:/EvolveGame/' + self.original_name,
            'entries': [{'path': self.virtual, 'bytes': 4,
                         'sha256': asset_library.hashlib.sha256(b'test').hexdigest()}],
        }))
        asset_library._invalidate()
        self.addCleanup(asset_library._invalidate)

    def test_existing_hash_archive_is_migrated_without_changing_id(self):
        old = asset_library.imported_archives(self.root)[0]
        renamed = asset_library.migrate_readable_archive_folders(self.root)
        self.assertEqual(len(renamed), 1)
        self.assertEqual(old['id'], self.id)
        self.assertFalse(self.folder.exists())
        new_folder = archive_folder(self.id, self.root)
        self.assertTrue(new_folder.name.startswith(self.original_name + '__'))
        self.assertEqual(asset_library.imported_archives(self.root)[0]['id'], self.id)
        self.assertEqual(archive_entries(self.id, self.root)[self.virtual].read_bytes(), b'test')
        self.assertEqual(asset_library.extracted_index(self.root)[self.virtual][0]['_asset_library'], self.id)
        self.assertEqual(asset_library.verify_library(self.root)['corrupt'], [])
        self.assertEqual(asset_library.migrate_readable_archive_folders(self.root), ())
        asset_library.remove_archive(self.id, self.root)
        self.assertFalse(new_folder.exists())

    def test_same_archive_name_never_overwrites_other_import(self):
        other_id = 'b' * 20
        other_folder = self.root / other_id
        other_folder.mkdir()
        (other_folder / asset_library.MANIFEST).write_text(json.dumps({
            'version': 1, 'name': self.original_name, 'entries': [],
        }))
        asset_library.migrate_readable_archive_folders(self.root)
        names = {p.name for p in asset_library._manifest_dirs(self.root)}
        self.assertEqual(len(names), 2)
        self.assertIn(self.original_name + '__' + other_id, names)
        self.assertIn(self.original_name + '__' + self.id, names)

    def test_migration_skips_existing_destination(self):
        desired = self.root / asset_library._archive_directory_name(self.original_name, self.id)
        desired.mkdir()
        self.assertEqual(asset_library.migrate_readable_archive_folders(self.root), ())
        self.assertTrue(self.folder.is_dir())


class ScopedCharacterMaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ('c' * 20)
        self.model = 'characters/monsters/teleporter/teleporter1.skinm'
        self.material = 'characters/monsters/teleporter/teleporter.mtl'
        self.dds = 'characters/monsters/teleporter/textures/teleporter_diff.dds.0'
        self.index = {}
        self._add(self.model, uv_mesh(), own=True)
        self._add(self.material,
                  b'<Material><Textures><Texture Map="Diffuse" File="teleporter_diff.tif"/>'
                  b'</Textures></Material>', own=True)
        self._add(self.dds, self._dds((50, 100, 150)))
        self._add('teleporter_diff.dds.0', self._dds((200, 25, 25)))
        (self.workspace / asset_library.MANIFEST).write_text(json.dumps({
            'version': 1, 'name': 'characters_monsters_teleporter_data.pak',
            'entries': [],  # model paths supplied through injected index
        }))

    def _dds(self, rgb):
        image = BytesIO()
        Image.new('RGB', (8, 8), rgb).save(image, format='DDS')
        return image.getvalue()

    def _add(self, virtual, data, own=False):
        folder = self.workspace if own else self.root / ('d' * 20)
        path = folder / 'files' / virtual
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.index.setdefault(virtual.casefold(), []).append({
            'file': path, 'relative': virtual,
            'archive': 'synthetic.pak',
            'workspace': folder if own else None,
        })

    def test_character_relative_diffuse_wins_over_global_basename(self):
        hits, status = resolve_material_texture(self.index, 'teleporter_diff.tif',
                                                 self.material,
                                                 preferred_workspace=self.workspace)
        self.assertEqual(status, 'found')
        self.assertEqual(hits[0]['relative'], self.dds)
        materials = load_preview_materials(
            self.workspace, self.model, index=self.index)
        self.assertEqual(materials.count, 1)
        self.assertEqual(materials.textures[0].getpixel((0, 0)), (50, 100, 150))

    def test_category_shared_texture_resolves_after_character_local(self):
        shared = 'characters/monsters/shared/textures/eye_shared.dds.0'
        self._add(shared, self._dds((45, 75, 135)))
        hits, status = resolve_material_texture(
            self.index, 'shared/textures/eye_shared.tif',
            self.material, preferred_workspace=self.workspace)
        self.assertEqual(status, 'found')
        self.assertEqual(hits[0]['relative'], shared)
        hits, status = resolve_material_texture(
            self.index, 'eye_shared.tif',
            self.material, preferred_workspace=self.workspace)
        self.assertEqual(status, 'found')
        self.assertEqual(hits[0]['relative'], shared)

    def test_unrelated_actor_is_never_guessed_for_missing_dds(self):
        self.index.pop(self.dds.casefold())
        with self.assertRaisesRegex(ValueError, 'No diffuse DDS'):
            load_preview_materials(self.workspace, self.model, index=self.index)

    def test_explicit_cdf_material_override_outranks_same_name(self):
        explicit = 'characters/monsters/teleporter/textures/body_override.mtl'
        cdf = 'characters/monsters/teleporter/teleporter.cdf'
        self._add(explicit, b'<Material/>')
        self._add(cdf, (
            '<CharacterDefinition><Model File="other.chr"/>'
            '<AttachmentList><Attachment Type="CA_SKIN" '
            'Binding="characters/monsters/teleporter/teleporter1.skin" '
            'Material="' + explicit + '"/></AttachmentList></CharacterDefinition>'
        ).encode())
        hit, reason, _ = find_model_material(
            self.index, self.model, preferred_workspace=self.workspace)
        self.assertEqual(hit['relative'], explicit)
        self.assertEqual(reason, 'CDF attachment material')


class CharacterExplorerLabelTests(unittest.TestCase):
    def test_all_character_descriptions_keep_internal_names(self):
        for internal, friendly in CHARACTER_ALIASES.items():
            group = 'hunters' if internal in ('tech', 'scrap') else 'monsters'
            physical = ('merc_' if group == 'hunters' else 'mon_') + internal
            virtual = 'characters/' + group + '/' + physical + '/body.skinm'
            self.assertEqual(character_alias(virtual), friendly)
            self.assertEqual(character_asset_description(virtual),
                             'In-game: ' + friendly + ' | Internal: ' + physical)
            self.assertEqual(pretty_folder(physical, 'characters/' + group),
                             friendly + ' (' + physical + ')')
            pak = 'characters_%s_%s_data.pak' % (group, internal)
            self.assertIn(friendly + ' (' + internal + ')', group_title(pak))


if __name__ == '__main__':
    unittest.main()
