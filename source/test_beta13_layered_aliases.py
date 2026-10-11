"""Beta 13: verified base+layered CAF preview and original folder aliases."""
import unittest
from pathlib import Path

import numpy as np
from model_animation import AnimatedGLB
from model_blender_export import glb_bytes
from model_preview import read_preview_mesh
from model_rigging import skin_glb
from native_caf import append_caf_animations
from test_native_caf import synthetic_caf
from test_model_rigging import tiny_mesh, tiny_skeleton
from character_aliases import CHARACTER_ALIASES, character_alias, pretty_folder


class LayeredAnimationTests(unittest.TestCase):
    def _rig(self):
        raw = tiny_mesh()
        mesh = read_preview_mesh(raw, 'synthetic.skinm')
        return skin_glb(glb_bytes(mesh), raw, tiny_skeleton())[0]

    def test_layer_is_not_playable_without_verified_base(self):
        with self.assertRaisesRegex(ValueError, 'No standalone'):
            append_caf_animations(
                self._rig(), [('stand_aimposes_forward', synthetic_caf())])

    def test_base_and_layer_blend_and_weight(self):
        base = synthetic_caf('root', .2)
        overlay = synthetic_caf('root', .6)
        blob, details = append_caf_animations(self._rig(), [
            ('stand_idle', base), ('stand_aimposes_forward', overlay),
        ], allow_layered=True)
        self.assertEqual(details['clips'], 2)
        glb = AnimatedGLB(blob)
        self.assertFalse(glb.clip_modes['stand_idle']['layered'])
        self.assertTrue(glb.clip_modes['stand_aimposes_forward']['layered'])
        zero = glb.sample_layered('stand_idle','stand_aimposes_forward',.5,0.)
        normal = glb.sample('stand_idle', .5)
        np.testing.assert_allclose(zero, normal, atol=2e-5)
        weighted = glb.sample_layered(
            'stand_idle','stand_aimposes_forward',.5,.75)
        self.assertFalse(np.allclose(weighted, normal))
        self.assertTrue(np.isfinite(weighted).all())
        with self.assertRaisesRegex(ValueError, 'layer weight|Layer weight'):
            glb.sample_layered('stand_idle','stand_aimposes_forward',.5,1.5)
        with self.assertRaisesRegex(ValueError, 'base clip'):
            glb.sample_layered('stand_aimposes_forward','stand_idle',.5,.3)
        with self.assertRaisesRegex(ValueError, 'overlay'):
            glb.sample_layered('stand_idle','stand_idle',.5,.3)


class AliasMappingTests(unittest.TestCase):
    def test_all_seven_internal_names_resolve(self):
        expected = {
            'lurker': 'Gorgon', 'tech': 'Kala', 'scrap': 'Jack',
            'grounder': 'Kraken', 'rocky': 'Behemoth',
            'rockybobcicle': 'Glacial Behemoth', 'teleporter': 'Wraith',
        }
        self.assertEqual(CHARACTER_ALIASES, expected)
        for internal, friendly in expected.items():
            kind = 'hunters' if internal in ('tech','scrap') else 'monsters'
            folder = ('merc_' if kind == 'hunters' else 'mon_') + internal
            path = 'characters/%s/%s/model_3p.skinm' % (kind, folder)
            self.assertEqual(character_alias(path), friendly)
            self.assertEqual(
                pretty_folder(folder, 'characters/'+kind),
                friendly + ' (' + folder + ')')

    def test_aliases_do_not_change_exact_game_paths(self):
        self.assertEqual(
            pretty_folder('merc_lennox', 'characters/hunters'),
            'merc_lennox')
        self.assertIsNone(character_alias('characters/hunters/merc_lennox/file.mtl'))


if __name__ == '__main__':
    unittest.main()
