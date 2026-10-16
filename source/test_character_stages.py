"""Synthetic character-stage, packed mesh and bind-pose regressions."""
import struct
import unittest
from unittest.mock import patch
import numpy as np
from character_full_models import discover_full_characters
from character_assembly import _parse_cdf
from model_preview import read_preview_mesh
from model_rigging import skin_glb_composite, skin_glb, remap_skin_weights, _chunks
from model_blender_export import glb_bytes
from model_textured_export import _unpack_glb
from model_animation import _accessor, _local_matrix
from test_model_rigging import crchf, tiny_mesh, tiny_skeleton, bone_record

class CharacterStageTests(unittest.TestCase):
    def test_explicit_alpha_cutoff_survives_assembly_export_and_animation(self):
        from dataclasses import replace
        from PIL import Image
        from material_preview import PreviewMaterials, render_textured_mesh
        from character_assembly import combine_meshes
        from model_textured_export import embed_diffuse_textures
        from model_animation import AnimatedGLB
        from test_material_preview import uv_mesh
        mesh=read_preview_mesh(uv_mesh())
        image=Image.new('RGBA',(8,8),(220,20,20,204))
        material=PreviewMaterials((image,),('cutout',),'test',alpha_modes=(True,),alpha_cutoffs=(.92,))
        combined,material=combine_meshes([('body',mesh,material)])
        self.assertEqual(material.alpha_cutoffs,(.92,))
        masked=render_textured_mesh(combined,material,260,260)
        opaque=render_textured_mesh(combined,replace(material,alpha_modes=(False,),alpha_cutoffs=(0.,)),260,260)
        self.assertEqual(masked.getpixel((130,130)),(32,33,42))
        self.assertNotEqual(opaque.getpixel((130,130)),(32,33,42))
        blob,count=embed_diffuse_textures(glb_bytes(combined),material)
        doc,binary=_unpack_glb(blob)
        self.assertEqual(count,1)
        self.assertEqual(doc['materials'][0]['alphaMode'],'MASK')
        self.assertAlmostEqual(doc['materials'][0]['alphaCutoff'],.92)
        reader=object.__new__(AnimatedGLB);reader.doc=doc;reader.binary=binary
        decoded=reader._embedded_materials()
        self.assertEqual(decoded.alpha_cutoffs,(.92,))
        self.assertEqual(decoded.textures[0].getpixel((0,0)),(220,20,20,204))

    def test_dds_resize_preserves_rgb_under_zero_auxiliary_alpha(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from PIL import Image
        from material_preview import _decode_texture
        with TemporaryDirectory() as folder:
            path=Path(folder)/'body.dds'
            Image.new('RGBA',(32,32),(160,90,40,0)).save(path,format='DDS')
            image=_decode_texture({'file':path,'relative':'body.dds','workspace':None},
                                  max_dimension=8)
            self.assertEqual(image.size,(8,8))
            self.assertEqual(image.getpixel((4,4)),(160,90,40,0))

    def test_skin_render_node_transform_aligns_vertices_before_skinning(self):
        chunks=[(kind,bytes(raw)) for (kind,_),raw in _chunks(tiny_mesh()).items()]
        mesh_id=len(chunks)+1
        node=bytearray(204)
        struct.pack_into('<2i',node,64,mesh_id,-1)
        # Row-vector 90-degree Z rotation and an authored 2m X offset.
        struct.pack_into('<16f',node,84,0,1,0,0,-1,0,0,0,0,0,1,0,200,0,0,0)
        raw=crchf(chunks+[(0x08001000,bytes(260)),(0x0823100B,bytes(node))])
        original=read_preview_mesh(tiny_mesh());transformed=read_preview_mesh(raw)
        expected=tuple((2-y,x,z) for x,y,z in original.vertices)
        np.testing.assert_allclose(transformed.vertices,expected,atol=1e-6)
        blob,_=skin_glb(glb_bytes(transformed),raw,tiny_skeleton())
        doc,binary=_unpack_glb(blob)
        positions=_accessor(doc,binary,doc['meshes'][0]['primitives'][0]['attributes']['POSITION'])
        np.testing.assert_allclose(positions,[(x,z,-y) for x,y,z in expected],atol=1e-6)
        # Playback must retain the authored offset, including after root motion.
        from native_caf import append_caf_animations
        from test_native_caf import synthetic_caf
        from model_animation import AnimatedGLB
        animated,_=append_caf_animations(blob,[('move',synthetic_caf())])
        player=AnimatedGLB(animated)
        np.testing.assert_allclose(player.sample('move',0),expected,atol=1e-6)
        np.testing.assert_allclose(player.sample('move',.5),np.array(expected)+[.2,0,0],atol=1e-6)

    def test_stage_labels_are_distinct(self):
        base='characters/monsters/teleporter/teleporter'
        paths=[base+str(i)+ext for i in (1,2,3) for ext in ('.cdf','.skinm')]
        rows=discover_full_characters(paths)
        self.assertEqual([x.label for x in rows.values()],
                         [f'Teleporter{i} • Full Character' for i in (1,2,3)])

    def test_unique_skin_is_a_real_attachment(self):
        _, refs, _ = _parse_cdf(b'<CharacterDefinition><Model File="characters/a.chr"/>'
            b'<AttachmentList><Attachment Type="CA_SKIN_UNIQUE" AName="body" '
            b'Binding="characters/a.skin"/></AttachmentList></CharacterDefinition>')
        self.assertEqual(refs,(('body','characters/a.skin'),))

    def test_packed_vertices_and_subset_count_not_flags(self):
        verts=[(0,0,0),(1,0,0),(0,1,0)]
        stream=struct.pack('<6I',0,15,3,16,0,0)+b''.join(
            struct.pack('<3e2x4B2e',*v,255,255,255,255,.25,.75) for v in verts)
        indices=struct.pack('<6I3H',0,5,3,2,0,0,0,1,2)
        subsets=struct.pack('<4I',6,1,0,0)+struct.pack('<5I4f',0,3,0,3,3,0,0,0,0)
        raw=crchf([(0x08001016,stream),(0x08001016,indices),(0x08001017,subsets)])
        mesh=read_preview_mesh(raw)
        self.assertEqual(mesh.vertices,tuple(verts))
        self.assertEqual(mesh.uvs,((.25,.75),)*3)
        self.assertEqual(mesh.subsets,((0,3,3),))

    def test_skin_bind_pose_is_preserved_when_chr_rest_differs(self):
        raw=tiny_mesh(); mesh=read_preview_mesh(raw)
        different=crchf([(0x08002000,b'\0'*32+bone_record('root',0)+bone_record('child',-1,1.1))])
        blob,_=skin_glb_composite(glb_bytes(mesh),[(raw,tiny_skeleton(),4)],different)
        doc,binary=_unpack_glb(blob)
        joints=doc['skins'][0]['joints'];world={}
        for j in joints:
            parent=next((i for i,n in enumerate(doc['nodes']) if j in n.get('children',[])),None)
            world[j]=world.get(parent,np.eye(4))@_local_matrix(doc['nodes'][j])
        inverse=_accessor(doc,binary,doc['skins'][0]['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1)
        for j,ib in zip(joints,inverse):np.testing.assert_allclose(world[j]@ib,np.eye(4),atol=1e-6)

    def test_conflicting_weighted_accessory_bind_still_rejected(self):
        from character_assembly import combine_meshes
        raw=tiny_mesh(); mesh=read_preview_mesh(raw)
        combined,_=combine_meshes([('body',mesh,None),('extra',mesh,None)])
        other=crchf([(0x08002000,b'\0'*32+bone_record('root',0)+bone_record('child',-1,1.1))])
        with self.assertRaisesRegex(ValueError,'conflicting bind poses'):
            skin_glb_composite(glb_bytes(combined),[(raw,tiny_skeleton(),4),(raw,other,4)],tiny_skeleton())

    def test_eight_weights_export_without_discarding_secondary_block(self):
        chunks=[]
        for (kind,_),view in _chunks(tiny_mesh()).items():
            raw=bytes(view)
            if kind==0x08001016 and struct.unpack_from('<I',raw,4)[0]==9:
                records=[(0,0,0,0,200,0,0,0)]*4+[(1,0,0,0,55,0,0,0)]*4
                raw=struct.pack('<6I',0,9,8,8,0,0)+b''.join(struct.pack('<8B',*v) for v in records)
            chunks.append((kind,raw))
        raw=crchf(chunks); ids,weights,_=remap_skin_weights(raw,4,2)
        self.assertEqual(ids.shape,(4,8));self.assertTrue(np.all(weights.sum(axis=1)==255))
        blob,_=skin_glb(glb_bytes(read_preview_mesh(raw)),raw,tiny_skeleton())
        doc,binary=_unpack_glb(blob); attrs=doc['meshes'][0]['primitives'][0]['attributes']
        self.assertIn('JOINTS_1',attrs)
        np.testing.assert_allclose(_accessor(doc,binary,attrs['WEIGHTS_1'])[:,0],55/255)
        # Composite export must preserve the same secondary influence channel.
        blob,_=skin_glb_composite(glb_bytes(read_preview_mesh(raw)),[(raw,tiny_skeleton(),4)],tiny_skeleton())
        doc,_=_unpack_glb(blob)
        self.assertIn('WEIGHTS_1',doc['meshes'][0]['primitives'][0]['attributes'])
        from test_model_animation import animated_fixture
        from model_animation import AnimatedGLB
        with patch('test_model_animation.tiny_mesh', return_value=raw):
            animation = AnimatedGLB(animated_fixture())
        displacement = animation.sample('Raise Head', .5) - animation.sample('Raise Head', 0)
        np.testing.assert_allclose(displacement[:,0], .5 * 55 / 255, atol=1e-6)


    def test_compiled_extensionless_material_name_resolves_exactly(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from model_material_resolve import find_model_material
        with TemporaryDirectory() as folder:
            base=Path(folder); raw=crchf([(0x08021014,b'Rocky'+bytes(123)+bytes(4))])
            model=base/'rocky2.skinm';model.write_bytes(raw)
            mat=base/'rocky.mtl';mat.write_text('<Material/>')
            prefix='characters/monsters/rocky/'
            index={prefix+'rocky2.skinm':[{'file':model,'relative':prefix+'rocky2.skinm','workspace':None}],
                   prefix+'rocky.mtl':[{'file':mat,'relative':prefix+'rocky.mtl','workspace':None}]}
            found,reason,_=find_model_material(index,prefix+'rocky2.skinm')
            self.assertEqual(found['file'],mat)
            self.assertIn('compiled',reason)


    def test_monster_keeps_all_body_parts_in_body_only_mode(self):
        from character_assembly import assemble_character
        from tempfile import TemporaryDirectory
        from pathlib import Path
        skins=(('tail','characters/monsters/m/m3_tail.skin'),
               ('body','characters/monsters/m/m3.skin'),
               ('head','characters/monsters/m/m3_head.skin'))
        with TemporaryDirectory() as folder:
            mesh=Path(folder)/'mesh';mesh.write_bytes(tiny_mesh())
            with patch('character_assembly._character_definition',return_value=('m.cdf','m.chr',skins,{})), \
                 patch('character_assembly._resolve_file',return_value=mesh), \
                 patch('character_assembly.load_preview_materials',return_value=None):
                c=assemble_character(None,'characters/monsters/m/m3.skinm',{},equipment='body')
            self.assertEqual(c.included,('body','tail','head'))
            self.assertEqual(len(c.mesh.vertices),12)

    def test_hunter_accessory_stays_when_changing_equipment(self):
        from character_assembly import assemble_character
        from tempfile import TemporaryDirectory
        from pathlib import Path
        skins=(('body','characters/hunters/h/h_3p.skin'),
               ('mask','characters/hunters/h/h_3p_mask.skin'),
               ('rifle','characters/hunters/h/hrifle_3p.skin'))
        with TemporaryDirectory() as folder:
            mesh=Path(folder)/'mesh';mesh.write_bytes(tiny_mesh())
            with patch('character_assembly._character_definition',return_value=('h.cdf','h.chr',skins,{})), \
                 patch('character_assembly._resolve_file',return_value=mesh), \
                 patch('character_assembly.load_preview_materials',return_value=None):
                c=assemble_character(None,'characters/hunters/h/h_3p.skinm',{},equipment='body')
            self.assertEqual(c.included,('body','mask'))
            self.assertEqual(c.equipment_options,(skins[2],))


    def test_hunter_arm_skin_is_permanent_body_not_optional_weapon(self):
        from character_assembly import assemble_character, _hunter_anatomical_skin
        from tempfile import TemporaryDirectory
        from pathlib import Path
        cdf = 'characters/hunters/medic_emet/medic_emet.cdf'
        skins = (
            ('Body', 'characters/hunters/medic_emet/emet_body.skin'),
            ('Emet_RightArm', 'characters/hunters/medic_emet/emet_right_arm.skin'),
            ('LeftHand', 'characters/hunters/medic_emet/emet_lhand.skin'),
            ('Rifle', 'characters/hunters/medic_emet/emet_weapon.skin'))
        self.assertTrue(_hunter_anatomical_skin(skins[1][0], skins[1][1], cdf))
        self.assertFalse(_hunter_anatomical_skin('armgun', 'weapons/armgun.skin', cdf))
        with TemporaryDirectory() as tmp:
            mesh = Path(tmp) / 'mesh'
            mesh.write_bytes(tiny_mesh())
            with patch('character_assembly._character_definition',
                       return_value=(cdf, 'emet.chr', skins, {})), \\
                 patch('character_assembly._resolve_file', return_value=mesh), \\
                 patch('character_assembly.load_preview_materials',
                       return_value=None):
                body = assemble_character(None, cdf, {}, equipment='body')
                equipped = assemble_character(None, cdf, {})
            self.assertEqual(body.included, ('Body', 'Emet_RightArm', 'LeftHand'))
            self.assertEqual(equipped.included,
                             ('Body', 'Emet_RightArm', 'LeftHand', 'Rifle'))

    def test_assembly_does_not_silently_drop_part_nine(self):
        from character_assembly import assemble_character
        from tempfile import TemporaryDirectory
        from pathlib import Path
        skins = tuple((label, 'characters/hunters/h/h_3p_%s.skin' % label)
                      for label in ('body', 'leftarm', 'rightarm', 'hand',
                                    'lefthand', 'righthand', 'mask', 'scarf',
                                    'arm', 'rifle'))
        with TemporaryDirectory() as tmp:
            mesh = Path(tmp) / 'mesh'
            mesh.write_bytes(tiny_mesh())
            with patch('character_assembly._character_definition',
                       return_value=('characters/hunters/h/h.cdf', 'h.chr',
                                     skins, {})), \\
                 patch('character_assembly._resolve_file', return_value=mesh), \\
                 patch('character_assembly.load_preview_materials',
                       return_value=None):
                model = assemble_character(
                    None, 'characters/hunters/h/h.cdf', {}, equipment='body')
            self.assertIn('arm', model.included)
            self.assertEqual(len(model.included), 9)
            self.assertNotIn('rifle', model.included)

    def test_additive_motion_flags_override_a_standalone_looking_name(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from test_native_caf import synthetic_caf
        from character_animation_catalog import AnimationReference, native_clip_playability
        raw=bytearray(synthetic_caf())
        _,_,_,offset=struct.unpack_from('<4I',raw,16)
        struct.pack_into('<I',raw,offset,1)
        with TemporaryDirectory() as folder:
            file=Path(folder)/'deploy.caf';file.write_bytes(raw)
            ref=AnimationReference('jetpack_deploy_neutral','deploy.caf','caf',True,file)
            self.assertEqual(native_clip_playability(ref)[0],'layered')
            self.assertIn('base animation',native_clip_playability(ref)[1])

if __name__=='__main__':unittest.main()
