"""CPU decoders never start new expensive work after cancellation."""
import unittest
from unittest.mock import patch

from character_assembly import assemble_character
from character_animation_catalog import discover_animations
from material_preview import load_preview_materials


class DeepModelCancellationTests(unittest.TestCase):
    def test_assembly_cancels_before_searching_cdf(self):
        with patch('character_assembly._character_definition') as definition:
            with self.assertRaises(InterruptedError):
                assemble_character('/unused', 'hero.skinm', {},
                                   cancel=lambda: True)
            definition.assert_not_called()

    def test_materials_cancel_before_building_asset_index(self):
        with patch('material_preview._workspace_index') as build:
            with self.assertRaises(InterruptedError):
                load_preview_materials('/unused', 'hero.skinm',
                                       cancel=lambda: True)
            build.assert_not_called()

    def test_animations_cancel_before_library_merge(self):
        with patch('asset_library.extracted_index') as indexed:
            with self.assertRaises(InterruptedError):
                discover_animations('hero.chr', {},
                                    cancel=lambda: True)
            indexed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
