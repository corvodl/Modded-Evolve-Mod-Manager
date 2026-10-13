"""High-quality Blender textures and editable texture-folder regressions."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from PIL import Image
from blender_texture_folder import texture_payloads, write_texture_folder
from material_preview import PreviewMaterials, load_preview_materials
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures


def sample_glb():
    model = SimpleNamespace(vertices=[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],
                            triangles=[(0,1,2)],uvs=[(0,0),(1,0),(0,1)],
                            subsets=())
    image=Image.new("RGBA",(2048,1024),(15,70,200,255))
    mats=PreviewMaterials((image,),("Body",),"body.mtl")
    glb,n=embed_diffuse_textures(glb_bytes(model),mats)
    assert n==1
    return glb


class BlenderTextureFolderTests(TestCase):
    def test_png_folder_matches_embedded_high_resolution_glb(self):
        blob=sample_glb()
        with TemporaryDirectory() as folder:
            root=Path(folder)
            saved=write_texture_folder(blob,root/"Monster.glb")
            self.assertEqual(len(saved),1)
            self.assertEqual(saved[0].parent,root/"Textures")
            self.assertEqual(Image.open(saved[0]).size,(2048,1024))
            self.assertEqual(saved[0].read_bytes(),texture_payloads(blob)[0][2])
            with self.assertRaises(FileExistsError):
                write_texture_folder(blob,root/"Monster.glb")

    def test_two_models_have_distinct_texture_file_names(self):
        blob=sample_glb()
        with TemporaryDirectory() as folder:
            root=Path(folder)
            first=write_texture_folder(blob,root/"Body.glb")
            second=write_texture_folder(blob,root/"Weapon.glb")
            self.assertNotEqual(first[0],second[0])
            self.assertEqual(len(list((root/"Textures").iterdir())),2)

    def test_character_material_prefers_streamed_same_path(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            material=root/"body.mtl"
            material.write_text('<Material><Textures><Texture Map="Diffuse" '
                                'File="body_diff.tif"/></Textures></Material>',
                                encoding="utf-8")
            model="characters/hunters/merc_abc/body.skinm"
            relative="characters/hunters/merc_abc/body_diff"
            ordinary={"file":root/"ordinary.dds","workspace":None,
                      "relative":relative+".dds"}
            streamed={"file":root/"stream.dds.0","workspace":None,
                      "relative":relative+".dds.0"}
            index={ordinary["relative"]: [ordinary],
                   streamed["relative"]:[streamed]}
            index={key.casefold():value for key,value in index.items()}
            def decoder(record,**kwargs):
                return Image.new("RGB",(2048,2048) if record is streamed
                                 else (128,128))
            with patch("model_material_resolve.find_model_material",
                       return_value=({"file":material,"relative":
                                      "characters/hunters/merc_abc/body.mtl",
                                      "workspace":None},"matched",())), \
                 patch("material_preview._decode_texture",side_effect=decoder):
                preview=load_preview_materials(root,model,index=index)
            self.assertEqual(preview.textures[0].size,(2048,2048))
            self.assertIn("body_diff.dds.0",preview.diffuse_sources[0][0])

