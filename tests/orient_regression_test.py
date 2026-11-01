"""Regression tests for app/orient.py.

Needs trimesh (present in the slicer image). Run from the repository root:
    python3 -m unittest tests/orient_regression_test.py
or against the deployed runtime:
    docker run --rm -v "$PWD:/src:ro" --entrypoint python3 <image> -m unittest discover -s /src/tests -p 'orient_*'
"""

import os
import sys
import tempfile
import time
import unittest

import trimesh

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import orient  # noqa: E402


def _box(extents, center):
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(center)
    return box


class OrientRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, mesh):
        src = os.path.join(self.tmp.name, 'in.stl')
        dst = os.path.join(self.tmp.name, 'out.stl')
        mesh.export(src)
        rotated = orient.optimize_orientation(src, dst, 'FDM')
        return rotated, dst

    def test_flat_multi_part_upload_is_left_untouched(self):
        # Shape of the 2026-10-19 incident: three separate solids already lying
        # on the bed. The old optimizer spun them ~149 deg around Z and the
        # re-exported file crashed PrusaSlicer; the upload itself sliced fine.
        parts = trimesh.util.concatenate([
            _box([100, 33, 22], [36, -60.5, 11]),
            _box([40, 81, 13], [61, 20.5, 6.5]),
            _box([100, 108, 58], [-38, 29, 29]),
        ])
        rotated, dst = self._run(parts)
        self.assertFalse(rotated)
        self.assertFalse(os.path.exists(dst), 'kept pose must not write a re-exported copy')

    def test_spun_around_z_is_left_untouched(self):
        box = _box([80, 20, 10], [0, 0, 5])
        box.apply_transform(trimesh.transformations.rotation_matrix(2.6, [0, 0, 1]))
        rotated, _ = self._run(box)
        self.assertFalse(rotated)

    def test_standing_bar_is_laid_down_without_spin(self):
        rotated, dst = self._run(_box([10, 20, 100], [0, 0, 50]))
        self.assertTrue(rotated)
        out = trimesh.load(dst)
        self.assertAlmostEqual(out.extents[2], 10, places=3)
        self.assertAlmostEqual(out.bounds[0][2], 0, places=6)
        self.assertEqual(sorted(round(v, 3) for v in out.extents), [10, 20, 100])

    def test_dense_mesh_is_fast(self):
        # 160k triangles took ~110 s with trimesh.compute_stable_poses.
        torus = trimesh.creation.torus(major_radius=40, minor_radius=12, major_sections=400, minor_sections=200)
        started = time.monotonic()
        self._run(torus)
        self.assertLess(time.monotonic() - started, 15)

    def test_unstable_overhang_face_is_not_chosen(self):
        # An L whose long arm hangs over: lying on the arm's end face would tip.
        shape = trimesh.util.concatenate([
            _box([10, 10, 60], [0, 0, 30]),
            _box([80, 10, 10], [45, 0, 55]),
        ])
        for pose in orient.find_resting_poses(shape):
            hull_normal = pose['normal']
            # Resting on the far end of the arm (+X face) puts the center of mass outside it.
            self.assertFalse(hull_normal[0] > 0.999 and pose['contact_area'] < 150)


if __name__ == '__main__':
    unittest.main()
