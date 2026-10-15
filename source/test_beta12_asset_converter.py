"""Beta 12 regressions: cross-PAK streamed DDS, safe DBA fallback,
animation disclosure panels, bundled converter location.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from animation_converter_bridge import bundled_converter, extracted_asset_root
from converted_motion import clip_key, converted_motion
from character_animation_catalog import AnimationReference, native_clip_playability
from material_preview import load_preview_materials
from test_asset_streaming_models import split_fixture


class SharedTextureRegressionTests(TestCase):
    def test_stream_fragments_in_two_different_imported_paks(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / 'pak_data' / 'files'
            second = root / 'pak_ts' / 'files'
            source = root / 'source'
            source.mkdir()
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            split_fixture(source, 'armor.dds')
            virtual = 'characters/hunters/merc_maggie/textures/armor.dds'
            parts = {}
            for number in range(7):
                source_file = source / ('armor.dds.' + str(number))
                folder = first if number == 0 else second
                target = folder / (virtual + '.' + str(number))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source_file.read_bytes())
                parts[(virtual + '.' + str(number)).casefold()] = [{
                    'file': target, 'relative': virtual + '.' + str(number),
                    'workspace': None,
                }]
            material_rel = 'characters/hunters/merc_maggie/maggie_3p.mtl'
            material = first / material_rel
            material.parent.mkdir(parents=True, exist_ok=True)
            material.write_bytes(
                b'<Material><SubMaterials><Material Name="gauntlet" Shader="Illum">'
                b'<Textures><Texture Map="Diffuse" File="textures/armor.tif"/>'
                b'</Textures></Material></SubMaterials></Material>')
            parts[material_rel] = [{
                'file': material, 'relative': material_rel, 'workspace': None,
            }]
            loaded = load_preview_materials(
                root, 'characters/hunters/merc_maggie/maggie_3p.skinm',
                index=parts)
            self.assertEqual(loaded.count, 1)
            self.assertEqual(loaded.textures[0].size, (256, 256))
            (second / (virtual + '.3')).unlink()
            with self.assertRaisesRegex(ValueError, 'Texture folder is missing DDS streaming parts'):
                load_preview_materials(
                    root, 'characters/hunters/merc_maggie/maggie_3p.skinm',
                    index=parts)


class BundledConverterTests(TestCase):
    def test_bundled_path_in_app_not_on_system_path(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / 'third_party' / 'Cryengine-Converter' / 'cgf-converter.exe'
            with self.assertRaises(FileNotFoundError):
                bundled_converter(root)
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'binary-placeholder-for-path-test')
            self.assertEqual(bundled_converter(root), binary.resolve())

    def test_asset_root_detected_from_imported_manifest(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'AssetLibrary' / ('b' * 20)
            content = archive / 'files' / 'animations' / 'tech.dba'
            content.parent.mkdir(parents=True)
            content.write_bytes(b'fixture')
            (archive / 'asset-library.json').write_text('{}')
            self.assertEqual(extracted_asset_root(content), archive / 'files')

    def test_missing_caf_has_verifiable_dba_fallback(self):
        database = Path('database.dba')
        missing = AnimationReference(
            'nw_alert_stand_idle_forward', 'characters/merc/idle.caf',
            'caf', False, dba_file=database,
            dba_virtual_path='characters/merc/tech.dba')
        self.assertEqual(native_clip_playability(missing)[0], 'converter')
        unlinked = AnimationReference('idle', 'characters/merc/idle.caf', 'caf', False)
        self.assertEqual(native_clip_playability(unlinked)[0], 'missing')


class ConvertedMotionTests(TestCase):
    def test_exact_clip_only(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'run.glb').write_bytes(b'x')
            (root / 'idle.glb').write_bytes(b'y')
            result = converted_motion(
                root, 'stand_idle',
                parser=lambda file: SimpleNamespace(
                    clips={'stand_idle': (1,)} if file.name == 'idle.glb'
                    else {'combat_run': (1,)}))
            self.assertEqual(result[0].name, 'idle.glb')
            self.assertEqual(result[1], 'stand_idle')
            with self.assertRaisesRegex(ValueError, '0 exact match'):
                converted_motion(
                    root, 'missing_motion',
                    parser=lambda file: SimpleNamespace(clips={'idle': (1,)}))

    def test_match_does_not_fuzzily_substitute_names(self):
        self.assertNotEqual(clip_key('idle_side'), clip_key('idle'))
