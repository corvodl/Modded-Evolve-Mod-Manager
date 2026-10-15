"""Static and executable bounds for optional Qt-compatible SSAO."""
import unittest
from ssao_postprocess import QUALITY, VERTEX, FRAGMENT, SsaoEffect


class SsaoPostprocessTests(unittest.TestCase):
    def test_quality_is_bounded_and_opt_in(self):
        self.assertEqual(QUALITY['Off'],(0,0.))
        self.assertEqual(tuple(QUALITY),('Off','Low','Medium','High'))
        self.assertLess(QUALITY['Low'][0],QUALITY['Medium'][0])
        self.assertLessEqual(QUALITY['High'][0],16)

    def test_depth_sampling_does_not_darkening_background(self):
        self.assertIn('depth >= 0.99999',FRAGMENT)
        self.assertIn('float eye_depth(float d)',FRAGMENT)
        self.assertIn('if (i >= sample_count) break',FRAGMENT)
        self.assertIn('screen_uv + offsets[i]',FRAGMENT)
        self.assertIn('gl_Position = gl_Vertex',VERTEX)

    def test_gl_context_code_never_depends_on_crytengine(self):
        self.assertIn('GL_DEPTH_ATTACHMENT',str(SsaoEffect._resize.__code__.co_consts))
        self.assertIn('GL_ZERO',str(SsaoEffect.render.__code__.co_consts))
        self.assertNotIn('glReadPixels',SsaoEffect.render.__code__.co_names)


if __name__=='__main__':
    unittest.main()
