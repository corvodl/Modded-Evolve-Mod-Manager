"""Beta full-model & flat original-asset export regressions.

Fixtures represent PAK virtual paths without distributing any Evolve data.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from character_full_models import discover_full_characters
from character_asset_export import (
    character_asset_entries, export_character_asset_folder)
from multi_pak_assets import _resolve


class FullCharacterDiscoveryTests(unittest.TestCase):
    def test_parnell_full_character_is_separate_from_original_meshes(self):
        base = 'characters/hunters/merc_parnelltank/'
        files = [base + name for name in (
            'parnelltank_3p.cdf', 'parnelltank_3p.skin',
            'parnelltank_3p.skinm', 'parnelltank_3p_lod1.skinm',
            'parnelltank_3p_skeleton.chr',
            'parnelltank_fp.skinm', 'parnelltank_fp.cdf',
            'parnelltankrocketlauncher_fp.skinm',
            'parnelltankshotgun_fp.skinm')]
        choices = discover_full_characters(files)
        self.assertEqual(set(choices), {base + 'parnelltank_3p.skinm'})
        self.assertEqual(choices[base + 'parnelltank_3p.skinm'].definition,
                         base + 'parnelltank_3p.cdf')
        self.assertIn('Full Character',
                      choices[base + 'parnelltank_3p.skinm'].label)
        self.assertEqual(len(files), 9)  # No original file entries removed.

    def test_requires_exact_cdf_and_compiled_body_mesh(self):
        base = 'characters/monsters/teleporter/'
        self.assertFalse(discover_full_characters([
            base + 'teleporter_3p.cdf', base + 'teleporter_3p_lod1.skinm']))
        self.assertFalse(discover_full_characters([
            base + 'teleporter_3p.skinm']))
        self.assertFalse(discover_full_characters([
            'characters/hunters/shared/shared_3p.cdf',
            'characters/hunters/merc_other/other_3p.skinm']))


    def test_emet_full_character_uses_body_cdf_not_remote_cannon(self):
        base = 'characters/hunters/merc_emet/'
        files = [base + name for name in (
            'emet_3p.cdf', 'emet_3p.skinm', 'emet_3p.skin',
            'emetremotecannon_fp.skinm', 'emetremotecannon_fp.skin',
            'emetremotecannon_3p.skinm', 'emetremotecannon_3p.skin',
            'emethealingtotemworld_3p.cdf',
            'emethealingtotemworld_3p.skinm',
            'emetdropshipbeaconworld_3p.cdf',
            'emetdropshipbeaconworld_3p.skinm')]
        choices = discover_full_characters(files)
        body = base + 'emet_3p.skinm'
        self.assertEqual(set(choices), {body})
        self.assertEqual(choices[body].definition, base + 'emet_3p.cdf')
        self.assertEqual(choices[body].label, 'Emet • Full Character')
        self.assertNotIn(base + 'emetremotecannon_3p.skinm', choices)
        self.assertNotIn(base + 'emethealingtotemworld_3p.skinm', choices)
        self.assertNotIn(base + 'emetdropshipbeaconworld_3p.skinm', choices)

    def test_emet_cannon_and_deployables_without_body_cdf_are_not_full(self):
        root = 'characters/hunters/merc_emet/'
        cannon = root + 'emetremotecannon_3p.skinm'
        self.assertFalse(discover_full_characters((cannon, cannon[:-1])))
        self.assertFalse(discover_full_characters((
            root + 'emet_3p.cdf', cannon, cannon[:-1])))
        self.assertFalse(discover_full_characters((
            root + 'emet_3p.skinm', cannon, cannon[:-1])))
        self.assertFalse(discover_full_characters((
            'characters/hunters/medic_emet/emet_3p.cdf',
            root + 'emet_3p.skinm')))


class EmetCDFAssemblyTests(unittest.TestCase):
    def test_cannon_without_cdf_does_not_forge_body_glb(self):
        from character_asset_export import build_cdf_folder_glbs
        base = 'characters/hunters/merc_emet/'
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mesh = root / 'files' / (base + 'emetremotecannon_3p.skinm')
            skin = root / 'files' / (base + 'emetremotecannon_3p.skin')
            mesh.parent.mkdir(parents=True)
            mesh.write_bytes(b'arm-only')
            skin.write_bytes(b'arm-skin')
            index = {
                base + 'emetremotecannon_3p.skinm': [
                    {'file': mesh, 'workspace': root}],
                base + 'emetremotecannon_3p.skin': [
                    {'file': skin, 'workspace': root}]}
            assemblies, errors = build_cdf_folder_glbs(
                root, base.rstrip('/'), index)
            self.assertEqual(assemblies, {})
            self.assertEqual(errors, ())

    def test_emet_cdf_prefers_bound_cannon_over_other_equipment(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from character_asset_export import build_cdf_folder_glbs
        from model_blender_export import glb_bytes
        from types import SimpleNamespace
        base = 'characters/hunters/merc_emet/'
        cdf = base + 'emet_3p.cdf'
        body = base + 'emet_3p.skinm'
        cannon = base + 'emetremotecannon_3p.skinm'
        cannon_skin = cannon[:-1]
        other = base + 'emetother_3p.skinm'
        face = base + 'emet_3p_face_normal.skinm'
        mesh = SimpleNamespace(
            vertices=tuple((float(j//3)*2 + (1. if j%3 == 1 else 0.),
                            1. if j%3 == 2 else 0., 0.) for j in range(9)),
            triangles=((0, 1, 2), (3, 4, 5), (6, 7, 8)),
            uvs=(), subsets=((0, 3, 0), (3, 3, 1), (6, 3, 2)))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'files' / cdf
            source.parent.mkdir(parents=True)
            source.write_bytes(b'<CharacterDefinition/>')
            index = {cdf: [{'file': source, 'workspace': root}]}
            calls = []
            def assemble(_workspace, selected, _index, **kwargs):
                self.assertEqual(selected, cdf)
                equipment = kwargs.get('equipment')
                calls.append(equipment)
                selected_model = cannon if equipment == cannon_skin else other
                return SimpleNamespace(
                    cdf=cdf, skeleton=base + 'emet_3p_skeleton.chr',
                    rig_parts=((body, None, None, 3),
                               (face, None, None, 3),
                               (selected_model, None, None, 3)),
                    included=('body', 'normal', 'remotecannon'),
                    omitted=(), selected_faceplate=face[:-1],
                    equipment_options=(('remotecannon', cannon_skin),))
            glb = glb_bytes(mesh)
            with patch('character_assembly.assemble_character', side_effect=assemble), \
                 patch('character_asset_export.assembled_character_glb',
                       return_value=(glb, {'part_count': 2, 'bone_count': 4})), \
                 patch('character_animation_export.embed_verified_assembly_animations',
                       return_value=(glb, {'animations': 0,
                                           'converted_clips': (), 'note': 'none'})):
                assemblies, errors = build_cdf_folder_glbs(
                    root, base.rstrip('/'), index)
            self.assertEqual(errors, ())
            self.assertEqual(calls, [None, cannon_skin])
            info = assemblies['Assemblies/emet_3p.glb'][1]
            self.assertEqual(info['cdf'], cdf)
            self.assertEqual(info['rendered_parts'], (body, face, cannon))
            self.assertEqual(
                info['geometry_verification']['status'],
                'verified_body_and_remote_cannon')
            self.assertTrue(info['geometry_verification']['default_faceplate_verified'])
            self.assertNotIn('Assemblies/Emetremotecannon_3p.glb', assemblies)


class CompositeBlenderExportTests(unittest.TestCase):
    def test_missing_rig_still_exports_both_assembled_mesh_parts(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from model_preview import read_preview_mesh
        from material_preview import PreviewMaterials
        from character_assembly import combine_meshes
        from character_asset_export import assembled_character_glb
        from model_textured_export import _unpack_glb
        from test_model_preview import test_mesh

        raw = read_preview_mesh(test_mesh(), 'body.skinm')
        mats = PreviewMaterials((None,), ('body',), 'test.mtl', ())
        mesh, materials = combine_meshes([
            ('Body', raw, mats), ('Equipped item', raw, mats)])
        character = SimpleNamespace(
            mesh=mesh, materials=materials,
            cdf='characters/hunters/merc_parnelltank/parnelltank_3p.cdf',
            included=('Body', 'Equipped item'))
        with patch('native_character_glb.create_character_glb',
                   side_effect=ValueError('No verified skeleton available')):
            blob, report = assembled_character_glb(
                character, Path('.'), character.cdf, {})
        document, _ = _unpack_glb(blob)
        self.assertEqual(document['accessors'][0]['count'], len(raw.vertices) * 2)
        self.assertEqual(report['part_count'], 2)
        self.assertEqual(report['bone_count'], 0)
        self.assertIn('Unrigged assembled mesh', report['rig_status'])


class FlatCharacterFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        def fake_converter(_blob, output, **kwargs):
            Path(output).write_bytes(b'Kaydara FBX Binary  TEST')
            return {'format':'FBX Binary','mesh_count':1,'armatures':0,'actions':[]}
        converter = patch('fbx_export_converter.convert_glb_to_fbx', side_effect=fake_converter)
        converter.start()
        self.addCleanup(converter.stop)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'project'
        self.output = self.root / 'export'
        self.output.mkdir()
        self.prefix = 'characters/hunters/merc_parnelltank'
        self.model = self.prefix + '/parnelltank_3p.skinm'
        self.mtl = self.prefix + '/parnelltank_3p.mtl'
        self.dds = self.prefix + '/textures/body_diff.dds.0'
        self.bad = 'characters/hunters/merc_val/textures/body_diff.dds.0'
        self.index = {}
        self._add(self.model, b'MODEL', self.workspace)
        self._add(self.mtl, b'<Material/>', self.workspace)
        self._add(self.dds, b'DDS-sibling-TS', self.root / 'ts')
        self._add(self.bad, b'UNRELATED', self.root / 'other')

    def _add(self, virtual, data, workspace):
        file = workspace / 'files' / virtual
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        self.index.setdefault(virtual.casefold(), []).append({
            'archive': workspace.name + '.pak',
            'workspace': workspace,
            'relative': virtual, 'file': file,
        })

    @staticmethod
    def _glb():
        from types import SimpleNamespace
        from model_blender_export import glb_bytes
        mesh = SimpleNamespace(vertices=[(0,0,0),(1,0,0),(0,1,0)],
            triangles=[(0,1,2)],uvs=[(0,0),(1,0),(0,1)],subsets=())
        return glb_bytes(mesh)

    def test_export_has_one_readable_folder_no_characters_prefix(self):
        report = export_character_asset_folder(
            self.workspace, self.prefix, self.output, index=self.index,
            full_model=(self._glb(), {
                'texture_count': 2, 'bone_count': 16, 'rig_status': 'Rigged'}))
        dest = Path(report['folder'])
        self.assertEqual(dest.parent, self.output)
        self.assertEqual(dest.name, 'merc_parnelltank')
        self.assertEqual((dest / 'models/parnelltank_3p.skinm').read_bytes(), b'MODEL')
        self.assertEqual((dest / 'textures/body_diff.dds.0').read_bytes(),
                         b'DDS-sibling-TS')
        self.assertTrue((dest / 'merc_parnelltank.fbx').read_bytes().startswith(b'Kaydara FBX'))
        self.assertFalse((dest / 'characters').exists())
        self.assertFalse((dest / 'merc_val').exists())
        self.assertFalse((dest / 'Assemblies').exists())
        self.assertFalse((dest / 'Full_Character.glb').exists())
        manifest = json.loads((dest / 'asset_sources.json').read_text('utf-8'))
        self.assertEqual(manifest['character_folder'], self.prefix)
        self.assertIn(self.dds, [x['virtual'] for x in manifest['files']])
        self.assertEqual(self.workspace.joinpath('files', self.model).read_bytes(),
                         b'MODEL')
        with self.assertRaises(FileExistsError):
            export_character_asset_folder(
                self.workspace, self.prefix, self.output, index=self.index)

    def test_character_layout_collects_equipment_animations_and_texture_sources(self):
        equipment = self.prefix + '/equipment/weapon.skin'
        idle = self.prefix + '/motion/idle.caf'
        self._add(equipment, b'WEAPON', self.workspace)
        self._add(idle, b'CAF', self.workspace)
        report = export_character_asset_folder(
            self.workspace, self.prefix, self.output, index=self.index,
            full_model=(self._glb(), {'rig_status': 'Synthetic test'}))
        root = Path(report['folder'])
        self.assertTrue((root / 'merc_parnelltank.fbx').is_file())
        self.assertEqual((root / 'models/equipment/weapon.skin').read_bytes(), b'WEAPON')
        self.assertEqual((root / 'animations/motion/idle.caf').read_bytes(), b'CAF')
        self.assertEqual((root / 'textures/body_diff.dds.0').read_bytes(),
                         b'DDS-sibling-TS')
        self.assertFalse((root / 'Assemblies').exists())
        manifest = json.loads((root / 'asset_sources.json').read_text('utf-8'))
        self.assertIn('models/equipment/weapon.skin',
                      [row['file'] for row in manifest['files']])

    def test_embedded_character_animation_omits_duplicate_raw_sources(self):
        from model_textured_export import _pack_glb, _unpack_glb
        idle = self.prefix + '/motion/idle.caf'
        action = self.prefix + '/motion/tech_3p.dba'
        self._add(idle, b'CAF', self.workspace)
        self._add(action, b'DBA', self.workspace)
        doc, raw = _unpack_glb(self._glb())
        doc['skins'] = [{'joints':[0]}]
        doc['animations'] = [{'name':'idle'}, {'name':'run'}]
        root_glb = _pack_glb(doc, raw)
        result = export_character_asset_folder(
            self.workspace, self.prefix, self.output, index=self.index,
            full_model=(root_glb, {'cdf':self.prefix + '/tech_3p.cdf',
                                   'embedded_animations':2,
                                   'embedded_animation_clips':('idle','run')}))
        folder = Path(result['folder'])
        self.assertTrue((folder/'merc_parnelltank.fbx').read_bytes().startswith(b'Kaydara FBX'))
        self.assertFalse((folder/'animations').exists())
        self.assertEqual(result['animations']['animations'], 2)
        self.assertIsNone(result['animations']['animated_glb'])
        report = json.loads((folder/'export_verification.json').read_text('utf-8'))
        self.assertEqual(report['embedded_animations']['merc_parnelltank.fbx'],
                         ['idle','run'])
        self.assertEqual((self.workspace/'files'/idle).read_bytes(), b'CAF')
        self.assertEqual((self.workspace/'files'/action).read_bytes(), b'DBA')

    def test_ambiguous_cross_pak_asset_is_reported_not_guessed(self):
        self._add(self.dds, b'DIFFERENT', self.root / 'ts2')
        report = export_character_asset_folder(
            self.workspace, self.prefix, self.output, index=self.index)
        self.assertEqual(report['files'], 2)
        self.assertEqual(report['unresolved'][0][0], self.dds)
        self.assertFalse((Path(report['folder']) / 'textures/body_diff.dds.0').exists())

    def test_cdf_folder_exports_each_verified_stage_as_separate_glb(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from character_asset_export import build_cdf_folder_glbs
        for stage in (1,2,3):
            self._add(self.prefix + '/parnell' + str(stage) + '.cdf',
                      b'<CharacterDefinition/>', self.workspace)
        other = 'characters/hunters/merc_val/other.cdf'
        self._add(other, b'<CharacterDefinition/>', self.root / 'other')
        seen = []
        def assemble(_workspace, selected, _index, **_):
            seen.append(selected)
            return SimpleNamespace(cdf=selected)
        def glb(model, workspace, rel, index, **kwargs):
            return self._glb(), {
                'texture_count': 2, 'bone_count': 8,
                'rig_status': 'Rigged CDF assembly'}
        with (patch('character_assembly.assemble_character', side_effect=assemble),
              patch('character_asset_export.assembled_character_glb', side_effect=glb)):
            prepared, errors = build_cdf_folder_glbs(
                self.workspace, self.prefix, self.index)
        self.assertEqual(len(prepared), 3)
        self.assertEqual(len(errors), 0)
        self.assertFalse(any('merc_val' in name for name in seen))
        report = export_character_asset_folder(
            self.workspace, self.prefix, self.output, index=self.index,
            assemblies=prepared, assembly_errors=errors)
        target = Path(report['folder'])
        self.assertEqual(len(report['cdf_assemblies']), 3)
        for stage in (1,2,3):
            output = target/'models'/('parnell'+str(stage)+'.fbx')
            self.assertTrue(output.read_bytes().startswith(b'Kaydara FBX'))
        manifest = json.loads((target/'asset_sources.json').read_text('utf-8'))
        self.assertEqual(len(manifest['cdf_assemblies']), 3)
        self.assertFalse((target/'models'/'other.glb').exists())

    def test_unsafe_cdf_assembly_path_rejected_atomically(self):
        with self.assertRaisesRegex(ValueError,'Invalid CDF'):
            export_character_asset_folder(
                self.workspace, self.prefix, self.output,
                index=self.index, assemblies={'Assemblies/../hijack.glb':
                    (b'glTFfake',{'cdf':'invalid'})})
        self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_destination_does_not_modify_assets(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            export_character_asset_folder(
                self.workspace, self.prefix, self.workspace / 'files',
                index=self.index)
        with self.assertRaisesRegex(ValueError, 'outside'):
            export_character_asset_folder(
                self.workspace, self.prefix,
                self.workspace.joinpath('files', self.prefix), index=self.index)

    def test_cancelled_export_removes_partial_directory(self):
        def stopped():
            return True
        with self.assertRaises(InterruptedError):
            export_character_asset_folder(
                self.workspace, self.prefix, self.output,
                index=self.index, cancelled=stopped)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
