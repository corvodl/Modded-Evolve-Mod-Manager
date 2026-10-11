"""Regression: imported Asset Library models need no editable-workspace JSON.
Equipment .skinm can use CDF or embedded material references, not only
a same-name .mtl. No ambiguous material substitution is permitted.
"""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from model_material_resolve import find_model_material, nearby_model_materials
from material_preview import load_preview_materials
from multi_pak_assets import model_material_links
from studio_polish_117 import model_cache_key, prepare_model_cached
from test_material_preview import uv_mesh


class LibraryModelMaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / ('a' * 20)
        self.files = self.workspace / 'files'
        self.files.mkdir(parents=True)
        self.model = 'characters/hunters/merc_tech/techriftdeviceworld.skinm'
        self.material = 'characters/hunters/merc_tech/materials/riftdevice.mtl'
        self.cdf = 'characters/hunters/merc_tech/tech_3p.cdf'
        self.dif = 'characters/hunters/merc_tech/materials/textures/rift_diff.dds'
        self.index = {}
        self.add_asset(self.model, uv_mesh())
        self.add_asset(self.cdf, (
            '<CharacterDefinition><Model File="characters/hunters/merc_tech/tech_skeleton.chr"/>'
            '<AttachmentList><Attachment AName="device" Type="CA_SKIN" '
            'Binding="characters/hunters/merc_tech/techriftdeviceworld.skin" '
            'Material="' + self.material + '"/></AttachmentList></CharacterDefinition>'
        ).encode())
        self.add_asset(self.material, (
            '<Material Name="device" Shader="Illum"><Textures>'
            '<Texture Map="Diffuse" File="textures/rift_diff.tif"/>'
            '</Textures></Material>'
        ).encode())
        texture_file = self.files / self.dif
        texture_file.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(8,8),(60,110,155)).save(texture_file,format='DDS')
        self.index[self.dif] = [self.record(self.dif)]
        (self.workspace / 'asset-library.json').write_text(json.dumps({
            'version': 1,'name':'characters_hunters_merc_tech_data.pak',
            'entries':[{'path':path,'bytes':Path(hit[0]['file']).stat().st_size}
                       for path,hit in self.index.items()],
        }),encoding='utf-8')

    def record(self, rel):
        return {'file':self.files/rel, 'workspace':None,
                'archive':'fixture.pak','relative':rel}

    def add_asset(self, rel, data):
        path=self.files/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        self.index[rel]=[self.record(rel)]

    def test_cdf_material_and_relative_diffuse_for_equipment(self):
        record, basis, attempted = find_model_material(
            self.index,self.model,preferred_workspace=self.workspace)
        self.assertEqual(record['relative'],self.material)
        self.assertEqual(basis,'CDF attachment material')
        materials=load_preview_materials(self.workspace,self.model,index=self.index)
        self.assertEqual(materials.count,1)
        self.assertEqual(materials.textures[0].size,(8,8))
        links=model_material_links(self.workspace,self.model,index=self.index)
        self.assertEqual(links['status'],'found')
        self.assertEqual(links['material'],self.material)
        self.assertEqual(links['basis'],'CDF attachment material')

    def test_model_cache_accepts_imported_manifest_without_workspace_json(self):
        self.assertFalse((self.workspace/'.evolve-pak-workspace.json').exists())
        key=model_cache_key(self.workspace,self.model,index=self.index)
        self.assertEqual(key[1],self.model)
        # No need for a signing-stage PAK or legacy workspace JSON.
        result, cached, _ = prepare_model_cached(
            self.workspace,self.model,index=self.index,force=True)
        self.assertGreater(len(result['mesh'].vertices),0)
        self.assertIsNotNone(result['materials'])

    def test_embedded_explicit_material_path(self):
        self.add_asset(self.cdf,b'<CharacterDefinition><Model File="other.chr"/></CharacterDefinition>')
        embedded='characters/hunters/merc_tech/materials/riftdevice.mtl'
        self.add_asset(self.model,uv_mesh()+b'\0'+embedded.encode()+b'\0')
        hit,basis,_=find_model_material(self.index,self.model)
        self.assertEqual(hit['relative'],self.material)
        self.assertEqual(basis,'compiled model material reference')

    def test_missing_material_has_no_fake_fallback(self):
        self.add_asset(self.cdf,b'<CharacterDefinition><Model File="other.chr"/></CharacterDefinition>')
        self.add_asset(self.model,uv_mesh())
        # Riftdevice is not an exact prefix of techriftdeviceworld.
        hit,basis,_=find_model_material(self.index,self.model)
        self.assertIsNone(hit)
        self.assertEqual(basis,'unresolved')
        self.assertIn(self.material,nearby_model_materials(self.index,self.model))

    def test_unique_equipment_prefix_and_ambiguous_alternative(self):
        simple='characters/hunters/merc_tech/techriftdevice.mtl'
        self.add_asset(simple,b'<Material/>')
        self.add_asset(self.cdf,b'<CharacterDefinition><Model File="other.chr"/></CharacterDefinition>')
        result,basis,_=find_model_material(self.index,self.model)
        self.assertEqual(result['relative'],simple)
        self.assertEqual(basis,'unique equipment-prefix material')
        self.add_asset('characters/hunters/merc_tech/techriftde.mtl',b'<Material/>')
        result,basis,_=find_model_material(self.index,self.model)
        self.assertIsNone(result)
        self.assertEqual(basis,'unresolved')


if __name__ == '__main__':
    unittest.main()
