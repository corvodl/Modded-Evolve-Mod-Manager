"""Dependency identity must invalidate prepared assets after same-size edits."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import os

from studio_polish_117 import (
    clear_model_cache, prepare_model_cached, MODEL_CACHE_TTL)
from model_dependency_fingerprint import dependency_watch_paths


class DependencyFingerprintTests(TestCase):
    def test_long_warm_cache_invalidates_equal_size_split_dds_edit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'files/characters').mkdir(parents=True)
            (root / 'files/textures').mkdir()
            (root / 'files/materials').mkdir()
            (root / '.evolve-pak-workspace.json').write_text('{"version":1}')
            model = root / 'files/characters/hero.skinm'
            material = root / 'files/materials/body.mtl'
            dds = root / 'files/textures/hero.dds'
            stream = root / 'files/textures/hero.dds.0'
            model.write_bytes(b'CrChF synthetic')
            material.write_bytes(b'mtl original')
            dds.write_bytes(b'DDS TEST')
            stream.write_bytes(b'X' * 64)
            index = {
                'textures/hero.dds': [
                    dict(file=dds, workspace=root, relative='textures/hero.dds')],
                'textures/hero.dds.0': [
                    dict(file=stream, workspace=root,
                         relative='textures/hero.dds.0')],
                'materials/body.mtl': [
                    dict(file=material, workspace=root,
                         relative='materials/body.mtl')]}
            mats = SimpleNamespace(
                source='materials/body.mtl',
                diffuse_sources=(('textures/hero.dds','characters.pak'),),
                textures=(), normal_maps=(), specular_maps=())
            counter = [0]
            def fake_resolve(*_args, **_kwargs):
                counter[0] += 1
                return dict(raw=b'mesh', geometry=None, materials=mats, index=index)
            clear_model_cache()
            with patch('async_model_resolve.resolve_model', side_effect=fake_resolve):
                first, hit, _ = prepare_model_cached(
                    root, 'characters/hero.skinm', index=index)
                self.assertFalse(hit)
                second, hit, _ = prepare_model_cached(
                    root, 'characters/hero.skinm', index=index)
                self.assertTrue(hit)
                self.assertGreaterEqual(MODEL_CACHE_TTL, 300)
                self.assertEqual(counter[0], 1)
                # Change a same-byte-length stream and explicitly advance mtime
                # so coarse filesystem timestamp granularity cannot hide it.
                original = stream.stat().st_mtime_ns
                stream.write_bytes(b'Y' * 64)
                os.utime(stream, ns=(original + 2_000_000_000,
                                     original + 2_000_000_000))
                _, hit, _ = prepare_model_cached(
                    root, 'characters/hero.skinm', index=index)
                self.assertFalse(hit)
                self.assertEqual(counter[0], 2)
                material.write_bytes(b'mtl changed!')
                _, hit, _ = prepare_model_cached(
                    root, 'characters/hero.skinm', index=index)
                self.assertFalse(hit)
                self.assertEqual(counter[0], 3)
            clear_model_cache()

    def test_same_basename_in_other_workspace_has_separate_cache_key(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for project in ('buildA','buildB'):
                root = base / project
                (root/'files').mkdir(parents=True)
                (root/'files/hero.skinm').write_bytes(project.encode())
                (root/'.evolve-pak-workspace.json').write_text('{}')
            hits = []
            def fake_resolve(workspace, *_args, **_kwargs):
                hits.append(Path(workspace).name)
                return {'raw': b'1', 'geometry': None, 'materials': None}
            clear_model_cache()
            with patch('async_model_resolve.resolve_model', side_effect=fake_resolve):
                for project in ('buildA','buildB','buildA'):
                    prepare_model_cached(base / project, 'hero.skinm')
            self.assertEqual(hits, ['buildA','buildB'])
            clear_model_cache()

    def test_invalid_and_external_virtual_paths_never_become_dependencies(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            refs = dependency_watch_paths(root, '../elsewhere.skinm',
                                          {'materials': None}, {})
            self.assertFalse(any('elsewhere.skinm' in v for v in refs))


if __name__ == '__main__':
    import unittest
    unittest.main()
