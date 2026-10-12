"""Regression fixtures for variant selection, explicit textures and DBA transfer."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET
from PIL import Image
from unittest.mock import patch
from material_preview import PreviewMaterials, _decode_texture
from model_material_resolve import find_model_material
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb, _pack_glb
from texture_selection import apply_texture_choices, texture_choices, load_chosen_texture
from texture_transform import parse_transform, transform_uv
from test_material_preview import uv_mesh
from test_model_animation import animated_fixture
from test_editor_tabs_png import fixture_dds
from character_animation_catalog import parse_animation_references
from database_animation import attach_converted_motion


class ViewerMaterialFixTests(unittest.TestCase):
    def test_cdf_override_is_scoped_to_selected_variant(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary);index = {}
            for name, payload in {
                'body.mtl': b'<Material/>', 'elite.mtl': b'<Material/>',
                'elite.cdf': b'<CharacterDefinition><AttachmentList><Attachment Binding="characters/test/body.skin" Material="characters/test/elite.mtl"/></AttachmentList></CharacterDefinition>'}.items():
                file = root/name;file.write_bytes(payload)
                virtual = 'characters/test/' + name
                index[virtual] = [{'file': file, 'workspace': None, 'relative': virtual}]
            regular, _, _ = find_model_material(index, 'characters/test/body.skinm')
            elite, _, _ = find_model_material(index, 'characters/test/body.skinm', cdf='characters/test/elite.cdf')
            self.assertEqual(regular['relative'], 'characters/test/body.mtl')
            self.assertEqual(elite['relative'], 'characters/test/elite.mtl')

    def test_complete_multi_mip_part_zero_decodes_in_workspace_and_library(self):
        from dds_streaming import inspect_whole_part0
        with TemporaryDirectory() as temporary:
            work = Path(temporary);(work/'files').mkdir()
            raw = fixture_dds('DXT5', (16,16), 3)
            path = work/'files/full.dds.0';path.write_bytes(raw)
            self.assertEqual(inspect_whole_part0(work, 'full.dds.0'), raw)
            for workspace in (None, work):
                image = _decode_texture({'file': path, 'relative': 'full.dds.0', 'workspace': workspace})
                self.assertEqual(image.size, (16,16))
            path.write_bytes(raw[:144])
            with self.assertRaises(ValueError):inspect_whole_part0(work, 'full.dds.0')

    def test_selected_texture_pixels_are_embedded_and_reset_does_not_mutate_default(self):
        mesh = read_preview_mesh(uv_mesh())
        red = Image.new('RGB',(8,8),'red');blue = Image.new('RGB',(8,8),'blue')
        original = PreviewMaterials((red,), ('body',), 'body.mtl')
        chosen = apply_texture_choices(original, mesh, {0:(blue,'skins/blue.png')})
        blob, count = embed_diffuse_textures(glb_bytes(mesh), chosen)
        doc, binary = _unpack_glb(blob)
        view = doc['bufferViews'][doc['images'][0]['bufferView']]
        image = Image.open(BytesIO(binary[view['byteOffset']:view['byteOffset']+view['byteLength']]))
        self.assertEqual(count,1);self.assertEqual(image.getpixel((0,0)),(0,0,255))
        self.assertEqual(original.textures[0].getpixel((0,0)),(255,0,0))
        self.assertIs(apply_texture_choices(original, mesh, {}),original)

    def test_explicit_choice_can_fill_unresolved_slot(self):
        mesh = read_preview_mesh(uv_mesh())
        image = Image.new('RGB',(2,2),'green')
        self.assertEqual(apply_texture_choices(None,mesh,{0:(image,'green.png')}).count,1)
        with self.assertRaises(ValueError):apply_texture_choices(None,mesh,{2:(image,'green.png')})

    def test_folder_picker_preserves_distinct_same_named_textures(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            for folder,color in (('normal','red'),('elite','blue')):
                (root/folder).mkdir();Image.new('RGB',(2,2),color).save(root/folder/'body.png')
            choices=texture_choices({},root)
            self.assertEqual(len(choices),2)
            self.assertNotEqual(load_chosen_texture(choices[0],{}).tobytes(),load_chosen_texture(choices[1],{}).tobytes())

    def test_uv_transform_is_retained_in_glb_and_animated_materials(self):
        transform,dynamic=parse_transform(ET.fromstring('<Texture><TexMod TileU="3" OffsetU="-0.25" /></Texture>'))
        self.assertFalse(dynamic);self.assertEqual(transform_uv(.5,.25,transform),(1.25,.25))
        materials=PreviewMaterials((Image.new('RGB',(2,2),'red'),),('body',),'body.mtl',uv_transforms=(transform,))
        blob,_=embed_diffuse_textures(animated_fixture(),materials)
        doc,_=_unpack_glb(blob)
        self.assertIn('KHR_texture_transform',doc['extensionsUsed'])
        from model_animation import AnimatedGLB
        self.assertEqual(AnimatedGLB(blob).materials.uv_transforms,(transform,))

    def test_annotated_caf_keeps_canonical_path_and_annotation(self):
        refs=parse_animation_references(b'<Params><AnimationList><Animation name="#filepath" path="characters/test"/><Animation name="idle_alias" path="idle.caf (AimPoses_test)"/></AnimationList></Params>',lambda p:None)
        self.assertEqual(len(refs),1);self.assertEqual(refs[0].virtual_path,'characters/test/idle.caf')
        self.assertEqual(refs[0].annotation,'AimPoses_test')

    def test_dba_tracks_transfer_to_existing_geometry(self):
        original=animated_fixture();doc,raw=_unpack_glb(original)
        doc.pop('animations');base=_pack_glb(doc,raw)
        converted_doc,converted_raw=_unpack_glb(original)
        converted_doc['animations'][0]['name']='idle_asset'
        from model_animation import AnimatedGLB
        merged=attach_converted_motion(base,_pack_glb(converted_doc,converted_raw),'characters/test/idle_asset.caf')
        model=AnimatedGLB(merged)
        self.assertIn('idle_asset',model.clips)
        self.assertAlmostEqual(model.sample('idle_asset',.5)[2,0]-model.sample('idle_asset',0)[2,0],.5)
        self.assertEqual(len(model.positions),4)

    def test_dba_rejects_empty_output_wrong_rest_pose_and_ambiguous_clip(self):
        blob=animated_fixture();doc,raw=_unpack_glb(blob)
        empty=dict(doc);empty['animations']=[]
        with self.assertRaisesRegex(ValueError,'no rigged animation'):attach_converted_motion(blob,_pack_glb(empty,raw))
        child=doc['skins'][0]['joints'][1];doc['nodes'][child]['translation']=[99,0,0]
        with self.assertRaisesRegex(ValueError,'rest transform'):attach_converted_motion(blob,_pack_glb(doc,raw))
        doc,raw=_unpack_glb(blob);doc['animations']*=2
        with self.assertRaisesRegex(ValueError,'2 exact'):attach_converted_motion(blob,_pack_glb(doc,raw),'Raise Head.caf')

    def test_database_conversion_stages_skin_and_keeps_original_files(self):
        from database_animation import convert_database_motion
        from character_animation_catalog import AnimationReference
        from test_model_rigging import tiny_mesh, tiny_skeleton
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            skin=root/'body.skin';skin.write_bytes(tiny_skeleton())
            render=root/'body.skinm';render.write_bytes(tiny_mesh())
            database=root/'body.dba';database.write_bytes(b'original database')
            params=root/'body_skeleton.chrparams';params.write_bytes(b'<Params/>')
            exe=root/'converter.exe';exe.write_bytes(b'test executable placeholder')
            index={'characters/test/body_skeleton.chrparams':[{'file':params,'relative':'characters/test/body_skeleton.chrparams','workspace':None}]}
            character=SimpleNamespace(skeleton='characters/test/body_skeleton.chr',
                rig_parts=(('characters/test/body.skinm',render,skin,4),))
            reference=__import__('character_animation_catalog').AnimationReference(
                'DBA: body','characters/test/animations/body.dba','dba',True,database)
            def run(args,**kwargs):
                staged=Path(args[1]);assets=Path(args[args.index('-objectdir')+1])
                self.assertEqual(staged.suffix,'.skin')
                self.assertNotEqual(staged,skin)
                self.assertEqual((assets/'characters/test/animations/body.dba').read_bytes(),b'original database')
                self.assertIn(b'$TracksDatabase',staged.with_suffix('.chrparams').read_bytes())
                (Path(kwargs['cwd'])/'body.glb').write_bytes(animated_fixture())
                return SimpleNamespace(returncode=0,stderr=b'')
            with patch('database_animation.subprocess.run',side_effect=run):
                merged=convert_database_motion(animated_fixture(),reference,index,character,executable=exe,cache_root=root)
            self.assertTrue(merged.startswith(b'glTF'))
            self.assertEqual(database.read_bytes(),b'original database')
            self.assertEqual(params.read_bytes(),b'<Params/>')
            self.assertFalse(list(root.glob('evolve-dba-*')))

    def test_picker_cancel_does_not_change_selection(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        from qt_texture_selection import TextureSelectionDialog
        app=QApplication.instance() or QApplication([])
        mesh=read_preview_mesh(uv_mesh());choices={0:(Image.new('RGB',(2,2),'red'),'red.png')}
        dialog=TextureSelectionDialog(None,mesh,None,{},choices)
        dialog.reset();dialog.reject()
        self.assertIn(0,choices);self.assertNotIn(0,dialog.choices)
        dialog.close()


if __name__=='__main__':unittest.main()
