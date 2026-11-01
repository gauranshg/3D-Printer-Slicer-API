"""Orientation optimizer for STL models.

Finds a stable orientation that minimizes print height and exports an
orientation-adjusted STL model.

Candidate poses are the flat faces of the convex hull: a model can only rest
on one of those, and it rests stably when its center of mass projects inside
that face. Scoring them directly is a few vectorized numpy passes, where
trimesh's compute_stable_poses (a randomized drop simulation) took ~110 s on
a 160k-triangle model.

When the model already rests in a pose about as good as the best one, no
output is written and the caller slices the uploaded file as-is. Re-exporting rounds
every rotated vertex to float32, and on 2026-10-19 a pure Z-spin rewritten
that way crashed PrusaSlicer 2.8.1 (std::bad_alloc) on a model that sliced
fine as uploaded. A Z-spin never lowers the print, so it is never applied.
"""

import sys
import shutil
import time

import numpy as np
import trimesh

DOWN = np.array([0.0, 0.0, -1.0])

# Normals within this cosine of each other are one flat face of the hull.
NORMAL_DECIMALS = 4
# Keep the uploaded pose unless the best one beats it by this share of its height.
KEEP_ORIGINAL_MARGIN = 0.05
# Ignore hull faces too small to stand on (mm^2).
MIN_CONTACT_AREA = 1.0
# Slack for the center-of-mass-inside-face test (barycentric units).
INSIDE_TOLERANCE = 1e-6
# Score only the largest resting faces. Curved hulls have tens of thousands of
# sliver faces, and projecting every hull vertex onto all of them ran out of
# memory (80k-face torus hull: ~25 GB).
MAX_CANDIDATE_FACES = 256


def _pose_score(height, contact_area, technology):
    """Compute orientation score; lower is better."""
    contact_scale = max(contact_area, 1.0) ** 0.5
    if technology.upper() == 'SLA':
        # SLA: keep height low, but avoid very large peel area.
        return height + (0.15 * contact_scale)
    # FDM/default: keep height low and favor a large, stable bed contact.
    return height - (0.35 * contact_scale)


def _center_of_mass(mesh):
    """Volume center for closed meshes, area-weighted centroid otherwise."""
    if mesh.is_watertight and mesh.volume > 0:
        return np.asarray(mesh.center_mass, dtype=float)
    return np.asarray(mesh.centroid, dtype=float)


def _projects_inside(triangles, normals, point):
    """Per triangle: does `point`, projected along the normal, land inside it?"""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    offset = np.einsum('ij,ij->i', point - a, normals)
    p = point - offset[:, None] * normals
    v0, v1, v2 = c - a, b - a, p - a
    d00 = np.einsum('ij,ij->i', v0, v0)
    d01 = np.einsum('ij,ij->i', v0, v1)
    d11 = np.einsum('ij,ij->i', v1, v1)
    d20 = np.einsum('ij,ij->i', v2, v0)
    d21 = np.einsum('ij,ij->i', v2, v1)
    denom = d00 * d11 - d01 * d01
    safe = np.where(np.abs(denom) > 1e-12, denom, 1.0)
    u = (d11 * d20 - d01 * d21) / safe
    v = (d00 * d21 - d01 * d20) / safe
    return (np.abs(denom) > 1e-12) & (u >= -INSIDE_TOLERANCE) & (v >= -INSIDE_TOLERANCE) & (u + v <= 1 + INSIDE_TOLERANCE)


def find_resting_poses(mesh, technology='FDM'):
    """Score every stable resting face of the convex hull.

    Returns a list of dicts (normal, height, contact_area, score), where
    `normal` is the outward hull normal that would point down on the bed.
    """
    hull = mesh.convex_hull
    com = _center_of_mass(mesh)

    rounded = np.round(hull.face_normals, NORMAL_DECIMALS)
    keys, group = np.unique(rounded, axis=0, return_inverse=True)
    group = group.reshape(-1)

    areas = np.bincount(group, weights=hull.area_faces, minlength=len(keys))
    inside = _projects_inside(hull.triangles, hull.face_normals, com)
    stable = np.bincount(group, weights=inside.astype(float), minlength=len(keys)) > 0

    candidates = np.flatnonzero(stable & (areas >= MIN_CONTACT_AREA))
    if len(candidates) > MAX_CANDIDATE_FACES:
        candidates = candidates[np.argsort(areas[candidates])[::-1][:MAX_CANDIDATE_FACES]]
    if len(candidates) == 0:
        return []

    # Area-weighted mean normal per face group, renormalized.
    normals = np.zeros((len(keys), 3))
    np.add.at(normals, group, hull.face_normals * hull.area_faces[:, None])
    normals = normals[candidates]
    lengths = np.linalg.norm(normals, axis=1)
    normals = normals / np.where(lengths > 0, lengths, 1.0)[:, None]

    projections = hull.vertices @ normals.T
    heights = projections.max(axis=0) - projections.min(axis=0)

    poses = []
    for normal, height, area in zip(normals, heights, areas[candidates]):
        poses.append({
            'normal': normal,
            'height': float(height),
            'contact_area': float(area),
            'score': _pose_score(float(height), float(area), technology),
        })
    return poses


def choose_pose(poses):
    """Pick the face to put down, or None to keep the uploaded pose."""
    if not poses:
        return None
    best = min(poses, key=lambda pose: pose['score'])
    current = [pose for pose in poses if float(np.dot(pose['normal'], DOWN)) > 0.9999]
    if current:
        original = current[0]
        if original['score'] - best['score'] <= KEEP_ORIGINAL_MARGIN * original['height']:
            return None
    if float(np.dot(best['normal'], DOWN)) > 0.9999:
        return None
    return best


def _place_on_build_plate(mesh):
    """Center XY and place the model on Z=0."""
    mesh.apply_translation(-mesh.centroid)
    min_z = mesh.bounds[0][2]
    mesh.apply_translation([0, 0, -min_z])


def optimize_orientation(input_path, output_path, technology='FDM'):
    """Optimize model orientation for printing.

    Args:
        input_path: Path to source STL file.
        output_path: Destination STL output path.
        technology: Printing technology label (FDM or SLA).

    Returns:
        True when the model was rotated and written to output_path, False
        when the uploaded pose was kept (output_path is not written).

    Raises:
        SystemExit: If optimization fails after fallback copy.
    """
    print(f"[PYTHON ORIENT] Analyzing orientation for {technology}: {input_path}")
    started = time.monotonic()

    try:
        # 1. Load the mesh
        mesh = trimesh.load(input_path)

        if isinstance(mesh, trimesh.Scene):
            print("[PYTHON ORIENT] Merging scene into single mesh...")
            mesh = trimesh.util.concatenate(mesh.dump())

        print(f"[PYTHON ORIENT] Original Z-Height: {mesh.extents[2]:.2f}mm")
        if len(mesh.faces) == 0:
            print("[PYTHON ORIENT] Empty mesh, keeping original.")
            return False

        # 2. Score the resting faces and pick one
        poses = find_resting_poses(mesh, technology)
        print(f"[PYTHON ORIENT] Found {len(poses)} stable resting faces.")
        best = choose_pose(poses)

        # 3. Keep the uploaded file untouched when it is already good enough.
        # No output is written: the caller slices the input file as uploaded.
        if best is None:
            print(f"[PYTHON ORIENT] Keeping uploaded orientation ({time.monotonic() - started:.2f}s)")
            return False

        # 4. Tilt the chosen face onto the bed (no extra spin around Z)
        mesh.apply_transform(trimesh.geometry.align_vectors(best['normal'], DOWN))
        _place_on_build_plate(mesh)

        # 5. Final export
        mesh.export(output_path)
        print(f"[PYTHON ORIENT] Applied orientation, Z-Height {best['height']:.2f}mm "
              f"({time.monotonic() - started:.2f}s). Saved to {output_path}")
        return True

    except Exception as e:
        print(f"[PYTHON ORIENT] ERROR: Could not optimize orientation from this input file. {str(e)}")
        shutil.copy2(input_path, output_path)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 orient.py input.stl output.stl [FDM/SLA]")
        sys.exit(1)

    tech = "FDM"
    if len(sys.argv) > 3:
        tech = sys.argv[3]

    optimize_orientation(sys.argv[1], sys.argv[2], tech)
