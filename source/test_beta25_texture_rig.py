"""Regression for decoded DDS upload orientation and monster rig integrity.

Uses synthetic images, meshes and bones only; no Evolve assets.
"""
from __future__ import annotations

from io import BytesIO
import inspect
import struct
import unittest
from dataclasses import replace

import numpy as np
from PIL import Image

from gl_texture_upload import gl_upload_pixels
from model_preview import read_preview_mesh
from model_blender_export import glb_bytes
from model_rigging import skin_glb, skin_glb_composite, validate_skin_rest_pose
from model_textured_export import _unpack_glb, _pack_glb
from test_model_rigging import tiny_mesh, tiny_skeleton


class GPUTextureOrientationTests(unittest.TestCase):
    def test_open_gl_row_zero_matches_bottom_of_pillow_source(self):
        # A CryEngine UV v=0 samples top DDS row in the existing CPU renderer.
        # glTexImage2D treats its FIRST byte row as the bottom texture row.
        source=Image.new('RGB',(2,2))
        source.putpixel((0,0),(255,0,0))
        source.putpixel((1,0),(0,255,0))
        source.putpixel((0,1),(0,0,255))
        source.putpixel((1,1),(255,255,0))
        gl_bytes=gl_upload_pixels(source,'RGB')
        self.assertEqual(list(gl_bytes[:6]),[0,0,255,255,255,0])
        self.assertEqual(list(gl_bytes[6:]),[255,0,0,0,255,0])
        # Neither the model's source DDS pixels nor its UVs are mutated.
        self.assertEqual(source.getpixel((0,0)),(255,0,0))

    def test_alpha_channel_is_retained_when_flipping(self):
        im=Image.new('RGBA',(1,2))
        im.putpixel((0,0),(10,20,30,40))
        im.putpixel((0,1),(50,60,70,80))
        self.assertEqual(gl_upload_pixels(im,'RGBA'),
                         bytes((50,60,70,80,10,20,30,40)))
        with self.assertRaises(ValueError):
            gl_upload_pixels(im,'L')

    def test_qt_and_legacy_win32_uploads_share_same_orientation(self):
        from qt_gpu_viewer import QtGLModelCanvas
        from gpu_model_preview import Win32GPUPreview
        a=inspect.getsource(QtGLModelCanvas._upload_textures)
        b=inspect.getsource(Win32GPUPreview._upload_materials)
        self.assertIn('gl_upload_pixels(im, mode)',a)
        self.assertIn('gl_upload_pixels(rgb,',b)


class RigRestPoseTests(unittest.TestCase):
    def test_supported_single_skin_and_composite_preserve_rest_mesh(self):
        raw=tiny_mesh()
        mesh=read_preview_mesh(raw,'stage1.skinm')
        skeleton=tiny_skeleton()
        single,_=skin_glb(glb_bytes(mesh),raw,skeleton)
        self.assertGreaterEqual(validate_skin_rest_pose(single),1)
        from character_assembly import combine_meshes
        combined,_=combine_meshes([('body',mesh,None),('head',mesh,None)])
        composite,_=skin_glb_composite(
            glb_bytes(combined),
            [(raw,skeleton,len(mesh.vertices)),(raw,skeleton,len(mesh.vertices))],
            skeleton)
        self.assertGreaterEqual(validate_skin_rest_pose(composite),1)

    def test_stage_mismatched_inverse_bind_pose_is_rejected(self):
        raw=tiny_mesh()
        mesh=read_preview_mesh(raw,'stage1.skinm')
        single,_=skin_glb(glb_bytes(mesh),raw,tiny_skeleton())
        doc,binary=_unpack_glb(single)
        inverse=doc['accessors'][doc['skins'][0]['inverseBindMatrices']]
        view=doc['bufferViews'][inverse['bufferView']]
        byte_offset=view.get('byteOffset',0)+inverse.get('byteOffset',0)
        corrupted=bytearray(binary)
        # 4x4 column-major: translation X is the 13th float.
        old=struct.unpack_from('<f',corrupted,byte_offset+12*4)[0]
        struct.pack_into('<f',corrupted,byte_offset+12*4,old+100.)
        with self.assertRaisesRegex(ValueError,'bind/rest pose'):
            validate_skin_rest_pose(_pack_glb(doc,corrupted))


if __name__=='__main__':
    unittest.main()
