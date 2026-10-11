"""Regression coverage for native animation reference discovery and CDF viewing."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from character_animation_catalog import (
    AnimationReference, caf_header, discover_animations,
    parse_animation_references, prefer_idle,
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
            self.assertEqual(2, len(refs))
            self.assertEqual('characters/hunters/merc_tech/animations/3p/tech_idle_forward.caf',
                             refs[0].virtual_path)
            self.assertTrue(refs[0].available)
            self.assertFalse(refs[1].available)
            self.assertTrue(caf_header(file))
            self.assertEqual('nw_alert_stand_idle_forward', prefer_idle(refs).name)

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


if __name__ == '__main__':
    unittest.main()
