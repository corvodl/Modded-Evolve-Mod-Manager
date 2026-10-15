"""Map residency should never rehash unchanged decoded PIL image pixels."""
from dataclasses import dataclass
from threading import Event
import unittest
from unittest.mock import patch

from PIL import Image
from level_residency import SceneResidency


@dataclass(frozen=True)
class Materials:
    textures: tuple
    normal_maps: tuple = ()
    specular_maps: tuple = ()


@dataclass(frozen=True)
class Asset:
    model_file: str
    mesh: object
    geometry: object
    materials: Materials


class SceneResidencyImageCacheTests(unittest.TestCase):
    def test_same_resident_image_is_not_converted_to_bytes_again(self):
        image = Image.new('RGB', (16, 16), (30, 40, 50))
        decoded = Asset('model.cgf', None, None, Materials((image,)))
        residency = SceneResidency({'m': 'model.cgf'}, lambda *_: decoded,
                                   budget=16 * 1024 * 1024)
        first = residency.load(['m'], Event())
        self.assertIn('m', first)
        with patch.object(image, 'tobytes', wraps=image.tobytes) as tobytes:
            second = residency.load(['m'], Event())
        tobytes.assert_not_called()
        self.assertIs(second['m'].materials.textures[0], image)

    def test_distinct_decodes_deduplicate_identical_image_bytes(self):
        first = Image.new('RGB', (8, 8), 'blue')
        second = Image.new('RGB', (8, 8), 'blue')
        def decode(name, _geometry):
            return Asset(name, None, None, Materials(
                (first if name == 'a' else second,)))
        residency = SceneResidency({'a':'a','b':'b'}, decode, 16 * 1024 * 1024)
        result = residency.load(['a','b'], Event())
        self.assertIs(result['a'].materials.textures[0],
                      result['b'].materials.textures[0])

    def test_cancelled_load_still_returns_no_assets(self):
        residency = SceneResidency({'a': 'a'}, lambda *_: None, 8192)
        stopped = Event()
        stopped.set()
        self.assertIsNone(residency.load(['a'], stopped))


if __name__ == '__main__':
    unittest.main()
