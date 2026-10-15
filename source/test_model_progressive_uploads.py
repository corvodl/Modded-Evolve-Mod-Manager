"""Progressive OpenGL uploads are bounded and keep source texture sizes."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from qt_gpu_viewer import QtGLModelCanvas


class ProgressiveTextureUploadTests(unittest.TestCase):
    def test_geometry_can_be_present_before_gl_texture_uploads(self):
        source = Image.new('RGB', (16, 8), 'red')
        normal = Image.new('RGB', (16, 8), 'blue')
        specular = Image.new('RGB', (16, 8), 'white')
        canvas = SimpleNamespace(
            materials=SimpleNamespace(
                textures=(source,), normal_maps=(normal,),
                specular_maps=(specular,), alpha_modes=()),
            textures=[], normal_textures=[], specular_textures=[],
            _pending_texture_uploads=[], _batch_sort_cache=None,
            translucent_slots=set(), _gpu_upload_total_ms=0.,
            gpu_upload_ms=None, update=Mock())

        QtGLModelCanvas._queue_texture_uploads(canvas)
        self.assertEqual(canvas.textures, [0])
        self.assertEqual(canvas.normal_textures, [0])
        self.assertEqual(canvas.specular_textures, [0])
        self.assertEqual(len(canvas._pending_texture_uploads), 3)

        from OpenGL import GL
        with patch.multiple(GL, glGenTextures=Mock(side_effect=[41, 42, 43]),
                            glBindTexture=Mock(), glTexImage2D=Mock(),
                            glTexParameteri=Mock(), glPixelStorei=Mock(),
                            glGenerateMipmap=Mock(), glGetFloatv=Mock(return_value=1.)):
            for expected in (1, 2, 3):
                self.assertEqual(QtGLModelCanvas._upload_textures(
                    canvas, max_uploads=1), 1)
                self.assertEqual(sum(bool(i) for i in (
                    canvas.textures + canvas.normal_textures +
                    canvas.specular_textures)), expected)
                self.assertEqual(len(canvas._pending_texture_uploads), 3-expected)
            self.assertEqual(GL.glTexImage2D.call_count, 3)
            for call in GL.glTexImage2D.call_args_list:
                self.assertEqual(call.args[3:5], (16, 8))

        self.assertEqual(canvas.textures, [41])
        self.assertEqual(canvas.normal_textures, [42])
        self.assertEqual(canvas.specular_textures, [43])
        # One repaint per upload, including the final one, ensures the last
        # submitted texture actually appears in a later visible frame.
        self.assertEqual(canvas.update.call_count, 3)
        self.assertEqual(QtGLModelCanvas._upload_textures(
            canvas, max_uploads=1), 0)


if __name__ == '__main__':
    unittest.main()
