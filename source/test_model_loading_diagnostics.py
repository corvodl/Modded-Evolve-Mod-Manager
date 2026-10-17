"""No-asset diagnostic and GPU cleanup regressions for PR #97."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PIL import Image

from model_loading_diagnostics import (
    decoded_texture_megabytes, format_stage_times, process_memory_mib)
from qt_gpu_viewer import QtGLModelCanvas


class ModelMemoryTests(TestCase):
    def test_bounded_memory_estimates_include_every_map_without_copying_images(self):
        original = Image.new('RGBA', (1024,1024), 'blue')
        normal = Image.new('RGB', (1024,1024), 'purple')
        mats = SimpleNamespace(textures=(original,),
                               normal_maps=(normal,),
                               emissive_maps=(None,),
                               specular_maps=())
        self.assertEqual(decoded_texture_megabytes(mats), 7.)
        self.assertEqual(original.size, (1024,1024))
        self.assertEqual(decoded_texture_megabytes(None), 0.)

    def test_stage_timings_do_not_expose_asset_paths(self):
        status = format_stage_times({
            'reading_geometry': 24.2, 'material_dds': 1300.3,
            'animation_references': 16.})
        self.assertIn('Geometry / skeleton: 24.2 ms',status)
        self.assertIn('Materials / DDS: 1300.3 ms',status)
        self.assertIn('Animation references: 16.0 ms',status)
        self.assertIn('No stage',format_stage_times({}))

    def test_process_memory_uses_available_os_source(self):
        sample = SimpleNamespace(rss=104857600,peak_wset=230686720)
        fake = SimpleNamespace(Process=lambda pid: SimpleNamespace(
            memory_info=lambda:sample))
        with patch.dict(sys.modules, {'psutil':fake}):
            current,peak=process_memory_mib()
        self.assertEqual(current,100.)
        self.assertEqual(peak,220.)

    def test_cleanup_abandoned_gpu_view_releases_all_pending_texture_descriptors(self):
        timer = Mock()
        fake=SimpleNamespace(
            _uv_motion_timer=timer,
            _pending_texture_uploads=[('textures',0,Image.new('RGB',(4,4)))],
            _batch_sort_cache=('old',[]), isValid=lambda:False,
            textures=[1], normal_textures=[2], specular_textures=[3],
            gloss_textures=[4], detail_textures=[5],
            detail_normal_textures=[6], ao_textures=[7],
            emissive_textures=[8], environment_textures=[9],
        )
        QtGLModelCanvas._cleanup_gpu_context(fake)
        timer.stop.assert_called_once()
        self.assertEqual(fake._pending_texture_uploads,[])
        self.assertIsNone(fake._batch_sort_cache)
        for role in ('textures','normal_textures','specular_textures',
                     'gloss_textures','detail_textures',
                     'detail_normal_textures','ao_textures',
                     'emissive_textures','environment_textures'):
            self.assertEqual(getattr(fake,role),[])
