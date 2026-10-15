"""Shared decoded pixels must preserve quality, edits and caller isolation."""
import unittest
from unittest.mock import patch
from PIL import Image
import material_preview as materials
from test_editor_tabs_png import fixture_dds


class TexturePixelCacheTests(unittest.TestCase):
    def setUp(self):
        materials.clear_texture_cache()
        self.addCleanup(materials.clear_texture_cache)
        self.raw = fixture_dds('DXT5', (16, 16), 1)

    def decode(self, raw=None, **kwargs):
        return materials._decode_texture_pixels(
            self.raw if raw is None else raw, 'body.dds', **kwargs)

    def test_repeat_decodes_once_and_images_are_independent(self):
        with patch('material_preview.Image.open', wraps=Image.open) as opened:
            first = self.decode()
            expected = first.tobytes()
            first.paste((255, 0, 255, 255), (0, 0, 16, 16))
            second = self.decode()
            self.assertEqual(second.tobytes(), expected)
            self.assertEqual(opened.call_count, 1)

    def test_content_and_quality_are_separate(self):
        changed = self.raw[:-1] + bytes([self.raw[-1] ^ 255])
        with patch('material_preview.Image.open', wraps=Image.open) as opened:
            self.decode()
            self.decode(changed)
            self.assertEqual(self.decode(max_dimension=8).size, (8, 8))
            self.assertEqual(opened.call_count, 3)

    def test_memory_budget_evicts(self):
        with patch.object(materials, 'MAX_CACHED_TEXTURE_BYTES', 1):
            self.decode()
            self.assertFalse(materials._texture_pixels)

    def test_decode_modes_are_not_shared(self):
        self.decode()
        with patch('normal_map_utils.decode_normal_dds_image',
                   return_value=Image.new('RGB', (16, 16))) as normal:
            self.decode(normal_map=True)
            normal.assert_called_once()
        self.decode(historical_2012=True)
        self.assertEqual(len(materials._texture_pixels), 3)
