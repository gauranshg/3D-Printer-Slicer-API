"""Write the synthetic orientation test models to tests/testing-files/orientation/.

tests/testing-files/ is gitignored, so the models are regenerated from this
script rather than committed. Needs trimesh (present in the slicer image):
    python3 tests/generate_orientation_models.py

Models (all slice with expected results on the 2026-11-01 image):
- three_parts.stl   Shape of the 2026-10-19 incident: three solids already
                    flat on the bed. Must keep its pose (58 mm tall).
- standing_bar.stl  10 x 20 x 100 mm bar standing up. Must be laid down (10 mm).
- dense_torus.stl   160k triangles. Took ~118 s before the orientation
                    rewrite; ~6 s after.
"""

import os

import trimesh

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testing-files', 'orientation')


def _box(extents, center):
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(center)
    return box


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    models = {
        'three_parts.stl': trimesh.util.concatenate([
            _box([100, 33, 22], [36, -60.5, 11]),
            _box([40, 81, 13], [61, 20.5, 6.5]),
            _box([100, 108, 58], [-38, 29, 29]),
        ]),
        'standing_bar.stl': _box([10, 20, 100], [0, 0, 50]),
        'dense_torus.stl': trimesh.creation.torus(
            major_radius=40, minor_radius=12, major_sections=400, minor_sections=200),
    }
    for name, mesh in models.items():
        path = os.path.join(OUT_DIR, name)
        mesh.export(path)
        print(f'{path} ({len(mesh.faces)} triangles)')


if __name__ == '__main__':
    main()
