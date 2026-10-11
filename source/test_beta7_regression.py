"""Beta 7 regressions: active archive context and cross-PAK material lookup."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from project_selection import editor_collection_for_workspace
from material_preview import _workspace_index, load_preview_materials
from character_animation_catalog import AnimationReference, native_clip_playability
from test_editor_tabs_png import fixture_dds


class EditorArchiveIdentityTests(TestCase):
    def test_new_standalone_project_drops_stale_batch_selector(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            new = root / 'new'; old = root / 'old'; other = root / 'other'
            for folder in (new, old, other):
                folder.mkdir()
            old_batch = {'Game/old_data.pak': old, 'Game/old_ts.pak': other}
            self.assertEqual(editor_collection_for_workspace(new, old_batch), {})
            self.assertEqual(
                editor_collection_for_workspace(old, old_batch)['Game/old_data.pak'],
                old.resolve())

    def test_missing_project_cannot_create_fake_selector(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'old'; old.mkdir()
            self.assertEqual(
                editor_collection_for_workspace(root / 'missing', {'old': old}), {})


class IndexedMaterialsTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model = 'characters/hunters/merc_scrap/scrap_3p.skinm'
        self.material = 'characters/hunters/merc_scrap/scrap_3p.mtl'
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.mtlfile = self.root / 'scrap.mtl'
        self.index = {
            self.material: [{
                'file': self.mtlfile, 'workspace': self.workspace,
                'relative': self.material,
            }]
        }

    def _texture(self, filename, different=False):
        file = self.root / filename
        data = fixture_dds('DXT5', (16, 16), 1)
        file.write_bytes(data[:-1] + bytes([1]) if different else data)
        return file

    def _record(self, file, virtual):
        return {'file': file, 'workspace': None, 'relative': virtual}

    def _mtl(self, ref):
        self.mtlfile.write_text(
            '<Material><SubMaterials><Material Name="face" Shader="Illum">'
            '<Textures><Texture Map="Diffuse" File="' + ref + '"/>'
            '</Textures></Material></SubMaterials></Material>')

    def test_material_relative_cooked_dds_from_asset_library(self):
        self._mtl('textures/head_diff.tif')
        virtual = 'characters/hunters/merc_scrap/textures/head_diff.dds'
        file = self._texture('head_diff.dds')
        self.index[virtual] = [self._record(file, virtual)]
        result = load_preview_materials(self.workspace, self.model, index=self.index)
        self.assertEqual(result.count, 1)

    def test_duplicate_identical_basename_recovered_not_missing(self):
        self._mtl('textures/incorrect/hair_diff.tif')
        first = self._texture('first.dds')
        second = self._texture('second.dds')
        for virtual, file in (
            ('characters/hunters/shared/hair_diff.dds', first),
            ('characters/hunters/merc_scrap/hair_diff.dds', second)):
            self.index[virtual] = [self._record(file, virtual)]
        result = load_preview_materials(self.workspace, self.model, index=self.index)
        self.assertEqual(result.count, 1)

    def test_different_basename_copies_remain_ambiguous(self):
        self._mtl('textures/incorrect/hair_diff.tif')
        first = self._texture('first.dds')
        second = self._texture('second.dds', different=True)
        self.index['other/hair_diff.dds'] = [
            self._record(first, 'other/hair_diff.dds')]
        self.index['third/hair_diff.dds'] = [
            self._record(second, 'third/hair_diff.dds')]
        with self.assertRaisesRegex(ValueError, 'No diffuse DDS'):
            load_preview_materials(self.workspace, self.model, index=self.index)

    def test_library_is_merged_even_when_batch_index_was_found(self):
        virtual = 'characters/hunters/shared/face_diff.dds.0'
        file = self._texture('face.dds')
        extra = {virtual: [self._record(file, virtual)]}
        with patch('material_preview.related_asset_index',
                   return_value={self.material: self.index[self.material]}):
            with patch('asset_library.extracted_index', return_value=extra):
                result = _workspace_index(self.workspace, self.root, self.root)
        self.assertIn(virtual, result)


class CAFStatusTests(TestCase):
    def test_absent_caf_does_not_claim_dba_playback(self):
        ref = AnimationReference('nw_alert_stand_dropshipdeploy0_forward',
                                 'characters/hunters/merc_scrap/animations/3p/clip.caf',
                                 'caf', False)
        status, message = native_clip_playability(ref)
        self.assertEqual(status, 'missing')
        self.assertIn('DBA', message)
        self.assertIn('not yet supported', message)


if __name__ == '__main__':
    import unittest
    unittest.main()
