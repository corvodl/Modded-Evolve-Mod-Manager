"""GPU preview quality regression checks for detail-preserving texture upload."""
from __future__ import annotations
import ast
import re
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parent

class GPUTextureQualityTests(unittest.TestCase):
    def test_qt_renderer_preserves_full_image_upload_and_mipmaps(self):
        src=(SOURCE/"qt_gpu_viewer.py").read_text(encoding="utf-8")
        self.assertIn("glGenerateMipmap(GL_TEXTURE_2D)",src)
        self.assertIn("GL_LINEAR_MIPMAP_LINEAR",src)
        self.assertIn("GL_TEXTURE_MAX_ANISOTROPY_EXT",src)
        # The staged uploader may format the call differently, but must
        # submit the original decoded image dimensions, never a thumbnail.
        self.assertRegex(src, r"glTexImage2D\(GL_TEXTURE_2D,\s*0,\s*fmt,\s*im\.width,\s*im\.height")
        self.assertNotIn("im.thumbnail(",src)
        ast.parse(src)

    def test_legacy_detailed_quality_preserves_4096_images(self):
        src=(SOURCE/"gpu_model_preview.py").read_text(encoding="utf-8")
        self.assertIn("self.texture_limit = 4096",src)
        self.assertIn("'Detailed':4096",src)
        ast.parse(src)

    def test_model_panel_exposes_decoded_source_dimensions(self):
        src=(SOURCE/"qt_workspace_editor.py").read_text(encoding="utf-8")
        self.assertIn("decoded image sizes:",src)
        ast.parse(src)

if __name__=="__main__":
    unittest.main()
