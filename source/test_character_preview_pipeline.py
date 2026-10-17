"""Regression coverage for native animation reference discovery and CDF viewing."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from character_animation_catalog import (
    AnimationReference, caf_header, discover_animations,
    parse_animation_references, prefer_idle, native_clip_playability,
)
from character_assembly import assemble_character, combine_meshes
from material_preview import PreviewMaterials
from model_preview import PreviewMesh


class CharacterPipelineTests(unittest.TestCase):
    def test_only_declared_animation_names_and_exact_paths(self):
        payload = b'''<Params><AnimationList>
            <Animation name="#filepath" path="characters/hunters/merc_tech/animations/3p"/>
            <Animation name="*" path="*/bs_*.bspace"/>
            <Animation name="$TracksDatabase" path="characters/hunters/merc_tech/animations/tech_3p.dba"/>
            <Animation name="nw_alert_stand_idle_forward" path="tech_idle_forward.caf"/>
            <Animation name="nw_alert_swim_idle_forward" path="swim_idle_forward.caf"/>
            </AnimationList></Params>'''
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'tech_idle_forward.caf'
            file.write_bytes(b'CrChF\x07\x00\x00' + struct.pack('<II', 1, 16) + b'\0'*16)
            refs = parse_animation_references(payload,
                lambda virtual: file if virtual.endswith('/tech_idle_forward.caf') else None)
            self.assertEqual(3, len(refs))
            self.assertEqual('dba', refs[0].kind)
            self.assertEqual('DBA: tech_3p', refs[0].name)
            self.assertEqual('metadata', native_clip_playability(refs[0])[0])
            self.assertEqual('characters/hunters/merc_tech/animations/3p/tech_idle_forward.caf',
                             refs[1].virtual_path)
            self.assertTrue(refs[1].available)
            self.assertFalse(refs[2].available)
            self.assertTrue(caf_header(file))
            self.assertEqual('nw_alert_stand_idle_forward', prefer_idle(refs).name)

    def test_database_source_can_use_external_converter_for_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'hunter_tracks.dba'
            database.write_bytes(b'DBA fixture only: no native decoder')
            ref = AnimationReference(
                'DBA: hunter_tracks', 'characters/hunters/merc_tech/animations/3p/hunter_tracks.dba',
                'dba', True, database)
            state, hint = native_clip_playability(ref)
            self.assertEqual('converter', state)
            self.assertIn('converter', hint)
            self.assertIn('bundled converter', hint)

    def test_layered_aimposes_are_not_claimed_as_standalone_playback(self):
        layered = AnimationReference(
            'nw_alert_stand_aimposes_forward',
            'characters/hunters/merc_tech/animations/3p/nw_alert_stand_aimposes_forward.caf',
            'caf', True, Path('synthetic.caf'))
        additive = AnimationReference('nw_stand_additive', 'foo.caf',
                                      'caf', True, Path('synthetic.caf'))
        standalone = AnimationReference(
            'nw_alert_stand_idle_forward', 'idle.caf', 'caf', True,
            Path('synthetic.caf'))
        missing = AnimationReference('missing', 'missing.caf', 'caf', False)
        blendspace = AnimationReference('look_blendspace', 'look.bspace',
                                        'bspace', True, Path('look.bspace'))
        self.assertEqual(native_clip_playability(layered)[0], 'layered')
        self.assertEqual(native_clip_playability(additive)[0], 'layered')
        self.assertIn('base animation', native_clip_playability(layered)[1])
        self.assertEqual(native_clip_playability(missing)[0], 'missing')
        self.assertEqual(native_clip_playability(blendspace)[0], 'metadata')
        self.assertEqual(native_clip_playability(standalone)[0], 'candidate')
        self.assertIs(prefer_idle((layered, standalone)), standalone)

    def test_animation_catalog_uses_newly_imported_caf_without_reloading_model(self):
        from animation_on_demand import _exact_loose
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = 'characters/hunters/merc_tech/tech_3p_skeleton.chrparams'
            clip = ('characters/hunters/merc_tech/animations/3p/'
                    'tech_idle_forward.caf')
            params = root/'config.chrparams'
            params.write_text(
                '<Params><AnimationList>'
                '<Animation name="#filepath" '
                'path="characters/hunters/merc_tech/animations/3p"/>'
                '<Animation name="stand_idle" path="tech_idle_forward.caf"/>'
                '</AnimationList></Params>')
            header = b'CrChF\x07\x00\x00' + struct.pack('<II', 1, 16) + b'\0' * 16
            a, b = root/'animations1.caf', root/'animations2.caf'
            a.write_bytes(header)
            b.write_bytes(header)
            stale_model_index = {
                name: [{'workspace': root, 'file': params, 'relative': name}]}
            imported = {
                clip: [{'workspace': None, 'file': a, 'relative': clip,
                        '_asset_library': 'first'},
                       {'workspace': None, 'file': b, 'relative': clip,
                        '_asset_library': 'second'}]}
            with patch('asset_library.extracted_index', return_value=imported):
                refs, idle, source = discover_animations(
                    'characters/hunters/merc_tech/tech_3p.skinm',
                    stale_model_index, preferred_workspace=root)
            self.assertEqual(len(refs), 1)
            self.assertTrue(refs[0].available)
            self.assertEqual(refs[0].file, a)
            self.assertEqual(idle.name, 'stand_idle')
            b.write_bytes(header[:-1] + b'X')
            self.assertIsNone(_exact_loose(imported, clip))

    def test_native_catalog_uses_character_specific_skeleton(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = 'characters/hunters/merc_tech/tech_3p_skeleton.chrparams'
            clip = 'characters/hunters/merc_tech/animations/3p/tech_idle_forward.caf'
            param = root/'cfg';param.write_text(
                '<Params><AnimationList><Animation name="#filepath" '
                'path="characters/hunters/merc_tech/animations/3p"/>'
                '<Animation name="stand_idle" path="tech_idle_forward.caf"/>'
                '</AnimationList></Params>')
            caf = root/'track.caf'
            caf.write_bytes(b'CrChF\x07\x00\x00'+struct.pack('<II',1,16)+b'\0'*16)
            index = {
                name: [{'workspace': root, 'relative': name, 'file': param}],
                clip: [{'workspace': root, 'relative': clip, 'file': caf}],
            }
            refs, idle, source = discover_animations(
                'characters/hunters/merc_tech/tech_3p.skinm',
                index, preferred_workspace=root)
            self.assertEqual(source, name)
            self.assertEqual(1, len(refs))
            self.assertEqual('stand_idle', idle.name)
            # Other characters must never inherit Kala's named animations.
            foreign, foreign_idle, _ = discover_animations(
                'characters/hunters/merc_abe/abe_3p.skinm',
                index, preferred_workspace=root)
            self.assertFalse(foreign)
            self.assertIsNone(foreign_idle)

    def _maggie_shared_mtl_fixture(self, root):
        prefix = 'characters/hunters/merc_maggiesavage/'
        cdf = root / 'maggiesavage_3p.cdf'
        skins = ('maggiesavage_3p', 'maggiesavage_3p_flashlight',
                 'maggiesavage_3p_headwear')
        cdf.write_text(
            '<CharacterDefinition><Model File="' + prefix +
            'maggiesavage_3p_skeleton.chr"/><AttachmentList>' +
            ''.join('<Attachment Type="CA_SKIN" AName="' + label +
                    '" Binding="' + prefix + stem + '.skin"/>'
                    for label, stem in zip(
                        ('maggiebody', 'flashlight', 'headgear'), skins)) +
            '</AttachmentList></CharacterDefinition>', encoding='utf-8')
        index = {prefix + 'maggiesavage_3p.cdf': [
            {'workspace': root, 'file': cdf}]}
        for stem in skins:
            file = root / (stem + '.skinm')
            file.write_bytes(b'fixture')
            index[prefix + stem + '.skinm'] = [
                {'workspace': root, 'file': file}]
        mesh = PreviewMesh(
            ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)),
            ((0, 1, 2),), (.5, .5, 0.), 1., 'synthetic',
            ((0., 0.), (1., 0.), (0., 1.)), ((0, 3, 0),))
        return prefix, index, mesh

    def test_maggie_savage_shared_pixels_are_counted_once(self):
        # A single full-quality 10K image is under 768 MiB, but counting it
        # three times (body, flashlight, headgear) incorrectly rejects part 3.
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix, index, mesh = self._maggie_shared_mtl_fixture(root)
            image = SimpleNamespace(width=10240, height=10240)
            mats = PreviewMaterials(
                (image,), ('shared body',), 'maggiesavage_3p.mtl',
                normal_maps=(image,), specular_maps=(image,))
            with patch('character_assembly.read_preview_mesh',
                       return_value=mesh), patch(
                    'character_assembly.load_preview_materials',
                    return_value=mats) as loader:
                result = assemble_character(
                    root, prefix + 'maggiesavage_3p.skinm', index,
                    pre_material_geometry=lambda _mesh: None)
            self.assertEqual(('maggiebody', 'flashlight', 'headgear'),
                             result.included)
            self.assertFalse(result.omitted)
            self.assertEqual(3, len(result.materials.textures))
            self.assertTrue(all(tex is image for tex in result.materials.textures))
            self.assertEqual(loader.call_count, 3)
            self.assertTrue(all('material_cache' in call.kwargs
                                for call in loader.call_args_list))

    def test_distinct_oversized_maggie_textures_keep_budget_guard(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix, index, mesh = self._maggie_shared_mtl_fixture(root)
            # Three separate 9K RGBA textures exceed the 768 MiB limit.
            materials = [
                PreviewMaterials((SimpleNamespace(width=9216, height=9216),),
                                 ('part',), 'distinct.mtl')
                for _ in range(3)]
            with patch('character_assembly.read_preview_mesh',
                       return_value=mesh), patch(
                    'character_assembly.load_preview_materials',
                    side_effect=materials):
                result = assemble_character(
                    root, prefix + 'maggiesavage_3p.skinm', index,
                    pre_material_geometry=lambda _mesh: None)
            self.assertEqual(3, len(result.mesh.triangles))
            self.assertTrue(any(
                'headgear textures: CDF preview textures exceed 768 MiB'
                in msg for msg in result.omitted))
            self.assertIsNone(result.materials.textures[2])

    def test_sparse_material_fallback_does_not_exhaust_slots(self):
        first = PreviewMesh(((0,0,0), (1,0,0), (0,1,0)),
            ((0,1,2),), (.5,.5,0),1,'body',
            ((0,0),(1,0),(0,1)), ((0,3,255),))
        second = PreviewMesh(((0,0,1), (1,0,1), (0,1,1)),
            ((0,1,2),), (.5,.5,1),1,'weapon',
            ((0,0),(1,0),(0,1)), ((0,3,5),))
        mesh, material = combine_meshes([
            ('body', first, None), ('weapon', second, None)])
        self.assertEqual(6, len(mesh.vertices))
        self.assertEqual(((0,3,0), (3,3,1)), mesh.subsets)
        self.assertEqual(2, len(material.textures))
        self.assertEqual((3,4,5), mesh.triangles[1])

    def test_cdf_loads_body_and_only_selected_equipment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = 'characters/hunters/merc_tech/'
            cdf = root/'char.cdf'
            cdf.write_text('''<CharacterDefinition>
              <Model File="characters/hunters/merc_tech/tech_3p_skeleton.chr"/>
              <AttachmentList>
                <Attachment Type="CA_SKIN" AName="body"
                    Binding="characters/hunters/merc_tech/tech_3p.skin"/>
                <Attachment Type="CA_SKIN" AName="pistol"
                    Binding="characters/hunters/merc_tech/techpistol_3p.skin"/>
                <Attachment Type="CA_SKIN" AName="rifle"
                    Binding="characters/hunters/merc_tech/techbansheegun_3p.skin"/>
                <Attachment Type="CA_SKIN" AName="riftdevice"
                    Binding="characters/hunters/merc_tech/techriftdevice_3p.skin"/>
                <Attachment Type="CA_SKIN" AName="riftdevicearm"
                    Binding="characters/hunters/merc_tech/techriftdevicearm_3p.skin"/>
              </AttachmentList>
              <SkinSets><SkinSet name="HiddenWhenRiftDeviceSelected">
                <skin name="pistol"/><skin name="rifle"/>
              </SkinSet></SkinSets>
              </CharacterDefinition>''')
            index = {prefix+'tech_3p.cdf':
                [{'workspace':root,'file':cdf,'relative':prefix+'tech_3p.cdf'}]}
            for name in ('tech_3p.skinm','techpistol_3p.skinm','techbansheegun_3p.skinm',
                         'techriftdevice_3p.skinm','techriftdevicearm_3p.skinm'):
                file = root/name
                file.write_bytes(b'stub')
                index[prefix+name] = [{
                    'workspace':root,'file':file,'relative':prefix+name}]
            mesh = PreviewMesh(((0,0,0), (1,0,0), (0,1,0)),((0,1,2),),
                (.5,.5,0),1,'part',((0,0),(1,0),(0,1)),((0,3,3),))
            mats = PreviewMaterials((None,None,None,None),
                ('a','b','c','d'),'stub')
            with patch('character_assembly.read_preview_mesh', return_value=mesh), \
                 patch('character_assembly.load_preview_materials', return_value=mats):
                body = assemble_character(root, prefix+'tech_3p.skinm', index)
                selected = assemble_character(
                    root, prefix+'techpistol_3p.skinm', index)
                device = assemble_character(
                    root, prefix+'tech_3p.skinm', index,
                    equipment=prefix+'techriftdevice_3p.skin')
            self.assertEqual(('body','pistol'), body.included)
            self.assertEqual(('body','pistol'), selected.included)
            self.assertEqual('characters/hunters/merc_tech/techpistol_3p.skin', selected.selected_equipment)
            self.assertEqual(2, len(selected.mesh.triangles))
            self.assertFalse(any('rifle' in name for name in selected.included))
            self.assertEqual(('body','riftdevice','riftdevicearm'), device.included)


    def test_kala_banshee_and_rift_device_are_exclusive_cdf_equipment(self):
        # Synthetic excerpt matching the real tech_3p.cdf attachment names,
        # binding paths, and the two mutually exclusive equipment SkinSets.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = 'characters/hunters/merc_tech/'
            cdf = root / 'tech_3p.cdf'
            cdf.write_text(
                '<CharacterDefinition><Model File="' + prefix +
                'tech_3p_skeleton.chr"/><AttachmentList>' +
                ''.join('<Attachment Type="CA_SKIN" AName="' + label +
                        '" Binding="' + prefix + model + '.skin"/>'
                        for label, model in (
                            ('body', 'tech_3p'),
                            ('armorreducer', 'techarmorreducer_3p'),
                            ('bansheegun', 'techbansheegun_3p'),
                            ('pistoltech', 'techpistol_3p'),
                            ('riftdevice', 'techriftdevice_3p'),
                            ('riftdevicearm', 'techriftdevice_3p_arm'))) +
                '</AttachmentList><SkinSets>' +
                '<SkinSet name="HiddenWhenBansheeGunSelected">' +
                ''.join('<skin name="' + name + '"/>' for name in
                        ('armorreducer','pistoltech','riftdevice','riftdevicearm')) +
                '</SkinSet><SkinSet name="HiddenWhenRiftDeviceSelected">' +
                ''.join('<skin name="' + name + '"/>' for name in
                        ('armorreducer','bansheegun','pistoltech')) +
                '</SkinSet><SkinSet name="HiddenWhenArmorReducerSelected">' +
                ''.join('<skin name="' + name + '"/>' for name in
                        ('bansheegun','pistoltech','riftdevice','riftdevicearm')) +
                '</SkinSet></SkinSets></CharacterDefinition>')
            index = {prefix+'tech_3p.cdf':
                     [{'workspace': root, 'file': cdf,
                       'relative': prefix+'tech_3p.cdf'}]}
            for stem in ('tech_3p','techarmorreducer_3p',
                         'techbansheegun_3p','techpistol_3p',
                         'techriftdevice_3p','techriftdevice_3p_arm'):
                model = root / (stem + '.skinm')
                model.write_bytes(b'mesh placeholder')
                virtual = prefix + stem + '.skinm'
                index[virtual] = [{
                    'workspace': root, 'file': model, 'relative': virtual}]
            mesh = PreviewMesh(
                ((0.,0.,0.), (1.,0.,0.), (0.,1.,0.)),
                ((0,1,2),),(.5,.5,0.),1.,'synthetic',
                ((0.,0.), (1.,0.), (0.,1.)), ((0,3,0),))
            mats = PreviewMaterials(
                (None,), ('material slot',), 'synthetic source')
            with patch('character_assembly.read_preview_mesh',
                       return_value=mesh), patch(
                       'character_assembly.load_preview_materials',
                       return_value=mats):
                default = assemble_character(
                    root, prefix+'tech_3p.skinm', index)
                banshee = assemble_character(
                    root, prefix+'tech_3p.skinm', index,
                    equipment=prefix+'techbansheegun_3p.skin')
                rift = assemble_character(
                    root, prefix+'tech_3p.skinm', index,
                    equipment=prefix+'techriftdevice_3p.skin')
                opened_arm = assemble_character(
                    root, prefix+'techriftdevice_3p_arm.skinm', index)
                armor = assemble_character(
                    root, prefix+'tech_3p.skinm', index,
                    equipment=prefix+'techarmorreducer_3p.skin')
                body = assemble_character(
                    root, prefix+'tech_3p.skinm', index, equipment='body')
            self.assertEqual(default.included, ('body', 'bansheegun'))
            self.assertEqual(banshee.included, ('body', 'bansheegun'))
            self.assertEqual(rift.included,
                             ('body', 'riftdevice', 'riftdevicearm'))
            self.assertEqual(opened_arm.included,
                             ('body', 'riftdevice', 'riftdevicearm'))
            self.assertEqual(opened_arm.selected_equipment,
                             prefix+'techriftdevice_3p.skin')
            self.assertEqual(armor.included, ('body', 'armorreducer'))
            self.assertEqual(body.included, ('body',))


if __name__ == '__main__':
    unittest.main()
