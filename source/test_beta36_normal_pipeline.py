"""Beta.36 synthetic streamed DDS, tangent-space normal and GLB integration tests."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import struct
import unittest

from PIL import Image
from normal_map_utils import decode_normal_dds_image, inspect_dds_header
from material_preview import PreviewMaterials, _decode_texture, load_preview_materials
from model_blender_export import glb_bytes
from model_textured_export import embed_diffuse_textures, _unpack_glb
from blender_texture_folder import write_texture_folder


def dds(fourcc, width, height, payload, mips=1):
    result = bytearray(128)
    result[:4] = b"DDS "
    struct.pack_into("<I", result, 4, 124)
    struct.pack_into("<I", result, 8, 0x2100f if mips > 1 else 0x100f)
    struct.pack_into("<II", result, 12, height, width)
    struct.pack_into("<I", result, 28, mips)
    struct.pack_into("<I", result, 76, 32)
    struct.pack_into("<I", result, 80, 4)
    result[84:88] = fourcc
    return bytes(result) + payload


def bc5_block(x=128, y=128):
    return bytes((x, x)) + bytes(6) + bytes((y, y)) + bytes(6)


class Beta36NormalPipelineTests(unittest.TestCase):
    def test_bc5_flat_normal_returns_normalized_rgb(self):
        raw = dds(b"ATI2", 4, 4, bc5_block())
        image = decode_normal_dds_image(raw, source_name="skin_ddn.dds")
        self.assertEqual(image.size, (4, 4))
        self.assertGreater(image.getpixel((0, 0))[2], 250)

    def test_dxt5nm_uses_alpha_as_x_and_green_as_y(self):
        # BC3 alpha endpoint 128; RGB565 green endpoint approximately 128.
        block = bytes((128, 128)) + bytes(6) + struct.pack("<HHI", 32 << 5, 32 << 5, 0)
        image = decode_normal_dds_image(dds(b"DXT5", 4, 4, block),
                                        source_name="body_ddn.dds")
        self.assertEqual(image.size, (4, 4))
        self.assertGreater(image.getpixel((0, 0))[2], 250)

    def test_incomplete_top_mip_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "highest-resolution"):
            decode_normal_dds_image(dds(b"ATI2", 8, 8, bc5_block()),
                                    source_name="broken_ddn.dds")

    def test_split_stream_across_archive_roots_uses_full_top_mip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stem = "characters/hunters/test/body_ddn.dds"
            fragments = {
                0: dds(b"ATI2", 8, 8, bc5_block(255, 128), mips=4),
                1: bc5_block(),
                2: bc5_block(),
                3: bc5_block() * 4,
            }
            index = {}
            for number, raw in fragments.items():
                folder = root / ("archive%d" % number)
                folder.mkdir()
                path = folder / ("body_ddn.dds.%d" % number)
                path.write_bytes(raw)
                virtual = stem + "." + str(number)
                index[virtual] = [{"file": path, "relative": virtual,
                                   "workspace": None}]
            image = _decode_texture(index[stem+".0"][0], index=index, normal_map=True)
            self.assertEqual(image.size, (8, 8))
            self.assertGreater(image.getpixel((0, 0))[2], 250)

    def test_material_discovers_scoped_ddn_from_asset_index(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            virtual = "characters/hunters/merc_test/"
            material = root / "body.mtl"
            material.write_text(
                '<Material><Textures><Texture Map="Diffuse" File="'
                + virtual + 'body_diff.tif"/></Textures></Material>', encoding="utf-8")
            diffuse = root / "body_diff.dds"
            Image.new("RGB", (4, 4), (100, 120, 140)).save(diffuse, format="DDS")
            normal = root / "body_ddn.dds"
            normal.write_bytes(dds(b"ATI2", 4, 4, bc5_block()))
            index = {}
            for tail, file in (("body.mtl", material), ("body_diff.dds", diffuse),
                               ("body_ddn.dds", normal)):
                path = virtual + tail
                index[path] = [{"file": file, "relative": path,
                                 "workspace": None}]
            mats = load_preview_materials(root, virtual+"body.skinm", index=index)
            self.assertEqual(mats.normal_maps[0].size, (4, 4))
            self.assertGreater(mats.normal_maps[0].getpixel((0, 0))[2], 250)

    def test_glb_binds_normals_and_exports_all_maps(self):
        mesh = SimpleNamespace(vertices=[(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)],
            triangles=[(0, 1, 2)], uvs=[(0., 0.), (1., 0.), (0., 1.)],
            subsets=())
        diffuse = Image.new("RGB", (16, 16), (120, 90, 60))
        normal = Image.new("RGB", (16, 16), (128, 128, 255))
        specular = Image.new("RGB", (16, 16), (80, 80, 80))
        mats = PreviewMaterials((diffuse,), ("body",), "body.mtl",
                                normal_maps=(normal,), specular_maps=(specular,))
        blob, count = embed_diffuse_textures(glb_bytes(mesh), mats)
        self.assertEqual(count, 1)
        doc, data = _unpack_glb(blob)
        self.assertEqual(len(doc["images"]), 3)
        bind = doc["materials"][0]["normalTexture"]
        self.assertEqual(doc["images"][doc["textures"][bind["index"]]["source"]]["name"],
                         "Normal slot 0")
        self.assertEqual(bind["scale"], 1.0)
        self.assertEqual(doc["buffers"][0]["byteLength"], len(data))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "Body.glb"
            saved = write_texture_folder(blob, path)
            self.assertEqual(len(saved), 3)
            self.assertTrue(any("Normal" in file.name for file in saved))

    def test_gpu_shader_uses_transformed_uv_for_normals(self):
        from renderer_3 import FRAGMENT
        self.assertIn("texture2D(normal_map,diffuse_coord)", FRAGMENT)
        self.assertIn("dFdx(diffuse_coord)", FRAGMENT)
        self.assertNotIn("texture2D(normal_map,texcoord)", FRAGMENT)


if __name__ == "__main__":
    unittest.main()
