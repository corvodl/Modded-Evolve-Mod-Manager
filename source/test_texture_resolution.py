"""Regression for source-resolution DDS preview and Blender material export."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from material_preview import _decode_texture, MAX_PREVIEW_TEX_DIM


class TextureResolutionTests(TestCase):
    def test_highest_quality_keeps_2048_and_balanced_is_opt_in(self):
        with TemporaryDirectory() as directory:
            source=Path(directory)/"skin_d.dds"
            source.write_bytes(b"synthetic-dummy")
            image=Image.new("RGB",(2048,2048),(150,50,20))
            image.format="DDS"
            record={"file":source,"relative":"characters/test/skin_d.dds",
                    "workspace":None}
            with patch("material_preview.parse_dds",
                       return_value=SimpleNamespace(width=2048,height=2048)), \
                 patch("material_preview.Image.open",return_value=image):
                high=_decode_texture(record)
                low=_decode_texture(record,max_dimension=1024)
            self.assertEqual(high.size,(2048,2048))
            self.assertEqual(low.size,(1024,1024))
            self.assertEqual(MAX_PREVIEW_TEX_DIM,4096)
