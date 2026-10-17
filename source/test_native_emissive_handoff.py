"""Native CDF material handoff and authored emissive color regressions."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from PIL import Image
from material_preview import PreviewMaterials
from emissive_materials import emission_settings, bake_emission_map
from native_material_handoff import native_animation_materials
from model_blender_export import glb_bytes
from model_animation import AnimatedGLB
from model_textured_export import _unpack_glb, embed_diffuse_textures
from model_preview import read_preview_mesh
from test_model_preview import test_mesh


class AuthoredGlowTintTests(TestCase):
    def test_legacy_illum_decal_uses_authored_tint_instead_of_white(self):
        mtl = ET.fromstring('''<Material Shader="Illum"
            Diffuse="0.0723,0.1470,0.4233" Emissive="0,0,0">
            <PublicParams GlowAmount="3"/>
            <Textures><Texture Map="Decal" File="chroma_fan.tif"/></Textures>
            </Material>''')
        strength, color, source = emission_settings(mtl)
        self.assertEqual(strength, 3.)
        self.assertEqual(color, (0.0723,0.1470,0.4233))
        self.assertEqual(source, 'chroma_fan.tif')
        glow = bake_emission_map(None, color=color,
                                 emission=Image.new('RGB',(2,2),'white'),
                                 diffuse_alpha_mask=False)
        r,g,b = glow.getpixel((0,0))
        self.assertGreater(b, g)
        self.assertGreater(g, r)
        self.assertGreater(r, 0)

    def test_explicit_emission_color_has_priority_over_diffuse_tint(self):
        mtl=ET.fromstring('''<Material Shader="Illum" Diffuse="0,0,1"
             EmissiveColor="1,0,0" GlowAmount="3">
             <Textures><Texture Map="Decal" File="tube.tif"/></Textures>
             </Material>''')
        self.assertEqual(emission_settings(mtl)[1], (1.,0.,0.))

    def test_plain_alpha_and_modern_emittance_do_not_borrow_diffuse_tint(self):
        body=ET.fromstring('''<Material Shader="Illum" Diffuse="0,0,1"
             StringGenMask="%ALPHAGLOW%" GlowAmount="3"/>''')
        modern=ET.fromstring('''<Material Shader="PBR" Diffuse="0,0,1"
             EmissiveIntensity="2"><Textures>
             <Texture Map="Emittance" File="glow.tif"/></Textures></Material>''')
        self.assertIsNone(emission_settings(body)[1])
        self.assertIsNone(emission_settings(modern)[1])


class NativeMaterialHandoffTests(TestCase):
    def materials(self):
        blue=Image.new('RGB',(3,3),(8,40,130))
        red=Image.new('RGB',(3,3),(130,0,0))
        return PreviewMaterials(
            textures=(None,Image.new('RGB',(3,3),'gray'),None),
            names=('body: Flesh','body: Glow','arm: Other'),
            source='CDF character assembly',
            hidden=(False,False,False),
            emissive_maps=(None,blue,red),
            emissive_strengths=(0.,3.,2.),
            emissive_masks=('none','none','diffuse_alpha'),
            emissive_status=('none','decoded source blue','decoded source red'),
            original_slot_ids=(3,11,1),
            original_materials=('body.mtl','body.mtl','arm.mtl'),
            emissive_references=('','chroma_fan.tif','other.tif'),
            map_uv_transforms=(('emissive',((1.,1.,0.,0.,0.),
                                            (3.,4.,0.,0.,0.),
                                            (1.,1.,0.5,0.,0.))),))

    def test_compacted_reordered_glb_uses_source_slot_identity(self):
        src=self.materials()
        doc={'materials':[{'name':'CryEngine material slot 2 (texture not embedded)'},
                          {'name':'CryEngine material slot 1 (texture not embedded)'}]}
        final=native_animation_materials(doc,src)
        self.assertIsNotNone(final)
        self.assertEqual(final.names,('arm: Other','body: Glow'))
        self.assertEqual(final.original_slot_ids,(1,11))
        self.assertEqual(final.original_materials,('arm.mtl','body.mtl'))
        self.assertEqual(final.emissive_strengths,(2.,3.))
        self.assertEqual(final.emissive_masks,('diffuse_alpha','none'))
        self.assertEqual(final.emissive_references,('other.tif','chroma_fan.tif'))
        self.assertEqual(final.map_uv_transforms[0][1][1],(3.,4.,0.,0.,0.))
        self.assertEqual(final.emissive_maps[1].getpixel((0,0)),(8,40,130))

    def test_unverified_glb_slot_mapping_never_substitutes_materials(self):
        src=self.materials()
        for names in (['Diffuse material 1'],
                      ['CryEngine material slot 70'],
                      ['CryEngine material slot 1','CryEngine material slot 1'],
                      []):
            self.assertIsNone(native_animation_materials(
                {'materials':[{'name':n} for n in names]},src))


class EmbeddedAnimationMaterialsTests(TestCase):
    def test_embedded_glb_reader_keeps_blue_emissive_and_independent_uv(self):
        mesh=read_preview_mesh(test_mesh(),'example.skinm')
        emissive=Image.new('RGB',(2,2),(15,50,165))
        materials=PreviewMaterials(
            textures=(Image.new('RGB',(2,2),'gray'),), names=('Glow',),
            source='example.mtl',
            emissive_maps=(emissive,),emissive_strengths=(3.,),
            map_uv_transforms=(('emissive',((3.,4.,0.,0.,0.),)),))
        source=glb_bytes(mesh)
        glb,_=embed_diffuse_textures(source,materials)
        doc,binary=_unpack_glb(glb)
        restored=AnimatedGLB._embedded_materials(
            SimpleNamespace(doc=doc,binary=binary))
        self.assertEqual(restored.source,'Embedded GLB textures')
        self.assertEqual(restored.emissive_strengths,(100.,))
        self.assertEqual(
            doc['materials'][0]['extras']['cryEngineAuthoredEmissiveStrength'], 3.)
        self.assertEqual(
            doc['materials'][0]['extensions']['KHR_materials_emissive_strength']['emissiveStrength'],
            100.)
        self.assertEqual(restored.map_uv_transforms[0][1][0],(3.,4.,0.,0.,0.))
        self.assertEqual(restored.emissive_maps[0].getpixel((0,0)),(15,50,165))

    def test_no_emission_does_not_make_surface_glow(self):
        mesh=read_preview_mesh(test_mesh(),'plain.skinm')
        glb,_=embed_diffuse_textures(glb_bytes(mesh),
            PreviewMaterials((Image.new('RGB',(2,2),'white'),),
                              ('Plain',),'plain.mtl'))
        doc,binary=_unpack_glb(glb)
        output=AnimatedGLB._embedded_materials(
            SimpleNamespace(doc=doc,binary=binary))
        self.assertEqual(output.emissive_maps,(None,))
        self.assertEqual(output.emissive_strengths,(0.,))

    def test_ui_native_preview_handoff_keeps_remapped_materials(self):
        from qt_gpu_viewer import QtGPUModelPanel
        mapped=native_animation_materials(
            {'materials':[{'name':'CryEngine material slot 1'}]},
            NativeMaterialHandoffTests().materials())
        canvas=SimpleNamespace(materials=None,set_mesh=Mock())
        canvas.set_mesh.side_effect=lambda mesh,materials,**kwargs: setattr(
            canvas,'materials',materials)
        select=SimpleNamespace(blockSignals=Mock(),clear=Mock(),
                 addItems=Mock(),setCurrentText=Mock())
        panel=SimpleNamespace(anim_timer=SimpleNamespace(stop=Mock()),
             play_button=SimpleNamespace(setText=Mock()),
             canvas=canvas,clip_select=select,
             playlist_items=SimpleNamespace(clear=Mock(),addItems=Mock()),
             _pending_playback_preferences=None,
             _populate_material_slots=Mock(),
             _update_emissive_source_status=Mock(),
             _display_animation_frame=Mock(),
             anim_playing=False)
        imported=SimpleNamespace(preview_mesh='animated CDF mesh',
                 materials=PreviewMaterials((None,),('GLB',),'Embedded GLB textures'),
                 clips={'idle':None})
        QtGPUModelPanel._show_animated(panel,imported,auto_play=False,
                                       materials_override=mapped)
        self.assertIs(canvas.materials,mapped)
        self.assertEqual(canvas.materials.emissive_strengths,(3.,))
        panel._update_emissive_source_status.assert_called_once()


if __name__=='__main__':
    import unittest
    unittest.main()
