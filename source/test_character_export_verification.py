"""Emet source-geometry verification and consistent full-character preparation."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from model_blender_export import glb_bytes
from character_export_verification import (
    verify_emet_glb, prepare_emet_full_character)


BASE = 'characters/hunters/merc_emet/'
CDF = BASE + 'emet_3p.cdf'
BODY = BASE + 'emet_3p.skinm'
FACE = BASE + 'emet_3p_face_normal.skinm'
CANNON = BASE + 'emetremotecannon_3p.skinm'
CANNON_SKIN = CANNON[:-1]


def mesh(*, body=True, cannon=True):
    # Three adjacent but genuinely drawable mesh portions.
    triangles = []
    if body:
        triangles.append((0, 1, 2))
    triangles.append((3, 4, 5))
    if cannon:
        triangles.append((6, 7, 8))
    return glb_bytes(SimpleNamespace(
        vertices=tuple((float(j // 3)*3 + (1. if j % 3 == 1 else 0.),
                        1. if j % 3 == 2 else 0., 0.) for j in range(9)),
        triangles=tuple(triangles), uvs=(), subsets=()))


def character(*, cannon=True, face=FACE):
    equipment = ((CANNON, None, None, 3),) if cannon else ()
    return SimpleNamespace(
        cdf=CDF,
        rig_parts=((BODY, None, None, 3), (face, None, None, 3)) + equipment,
        selected_faceplate=face[:-1],
        equipment_options=(('remotecannon', CANNON_SKIN),),
        omitted=(), included=('body', 'normal', 'remotecannon'))


class EmetExportVerificationTests(unittest.TestCase):
    def test_body_and_cannon_drawable_from_verified_glb(self):
        report = verify_emet_glb(
            mesh(), character(), require_default_faceplate=True)
        self.assertEqual(report['status'], 'verified_body_and_remote_cannon')
        self.assertEqual(report['part_drawn_triangles'], [1, 1, 1])
        self.assertEqual(report['total_drawn_triangles'], 3)
        self.assertTrue(report['default_faceplate_verified'])
        self.assertEqual(len(report['source_parts']), 3)

    def test_arm_only_geometry_rejected_even_if_metadata_lists_body(self):
        with self.assertRaisesRegex(ValueError, 'no drawable triangles for emet_3p.skinm'):
            verify_emet_glb(mesh(body=False), character())
        with self.assertRaisesRegex(ValueError, 'no drawable triangles for emetremotecannon_3p.skinm'):
            verify_emet_glb(mesh(cannon=False), character())

    def test_no_bare_cannon_or_wrong_default_face(self):
        with self.assertRaisesRegex(ValueError, 'exactly one body'):
            verify_emet_glb(mesh(), character(cannon=False))
        variant = character(face=BASE + 'emet_3p_face_evil.skinm')
        with self.assertRaisesRegex(ValueError, 'Normal faceplate'):
            verify_emet_glb(mesh(), variant, require_default_faceplate=True)
        self.assertEqual(
            verify_emet_glb(mesh(), variant)['status'],
            'verified_body_and_remote_cannon')

    def test_preparation_chooses_cdf_cannon_and_keeps_faceplate(self):
        initial = character(cannon=False)
        finished = character(cannon=True)
        with patch('character_assembly.assemble_character',
                   return_value=finished) as assemble:
            selected = prepare_emet_full_character(
                'workspace', CDF, {}, character=initial)
        self.assertIs(selected, finished)
        self.assertEqual(assemble.call_args.kwargs['equipment'], CANNON_SKIN)
        self.assertEqual(assemble.call_args.kwargs['faceplate'], FACE[:-1])

    def test_cannon_absent_from_verified_cdf_rejected(self):
        no_cannon = character(cannon=False)
        no_cannon.equipment_options = ()
        with self.assertRaisesRegex(ValueError, 'remote cannon attachment'):
            prepare_emet_full_character('workspace', CDF, {}, character=no_cannon)


if __name__ == '__main__':
    unittest.main()
