import unittest
from gltf_normal_materials import attach_normal_texture


class GltfNormalTests(unittest.TestCase):
    def test_attach_normal_texture(self):
        gltf = {"materials": [{"name": "armor"}], "textures": [{"source": 0}]}
        attach_normal_texture(gltf, 0, 0)
        self.assertEqual(gltf["materials"][0]["normalTexture"]["index"], 0)

    def test_reject_invalid_indices(self):
        gltf = {"materials": [{}], "textures": []}
        with self.assertRaises(ValueError):
            attach_normal_texture(gltf, 0, 0)

    def test_reject_invalid_scale(self):
        gltf = {"materials": [{}], "textures": [{}]}
        with self.assertRaises(ValueError):
            attach_normal_texture(gltf, 0, 0, scale=float("nan"))


if __name__ == "__main__":
    unittest.main()
