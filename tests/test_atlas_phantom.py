import numpy as np

from backend.compiler import _taubin_smooth
from scripts.atlas_phantom import dilate, voxelize


def cube_triangles(low: float, high: float) -> np.ndarray:
    corners = np.array([[x, y, z] for x in (low, high) for y in (low, high) for z in (low, high)], dtype=np.float64)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return np.array([[corners[a], corners[b], corners[c]] for q in quads for a, b, c in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))])


def test_voxelize_fills_closed_surface_exactly():
    mask = voxelize(cube_triangles(2.5, 7.5), np.zeros(3), (10, 10, 10), 1.0)
    assert mask.sum() == 125
    assert mask[3:8, 3:8, 3:8].all()


def test_voxelize_keeps_parity_when_surface_extends_below_grid():
    mask = voxelize(cube_triangles(-4.5, 4.5), np.zeros(3), (8, 8, 8), 1.0)
    assert mask[:5, :5, :5].all() and not mask[5:, :, :].any()


def test_dilate_grows_by_one_voxel_per_iteration():
    seed = np.zeros((5, 5, 5), dtype=bool)
    seed[2, 2, 2] = True
    assert dilate(seed, 1).sum() == 7


def test_taubin_smoothing_preserves_topology_and_extent():
    vertices = [(float(x), float(y), float(z)) for x in (0, 1, 2) for y in (0, 1, 2) for z in (0, 1)]
    index = {vertex: number + 1 for number, vertex in enumerate(vertices)}
    faces = [tuple(index[(float(x + dx), float(y + dy), 0.0)] for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))) for x in (0, 1) for y in (0, 1)]
    smoothed = _taubin_smooth(vertices, faces)
    assert len(smoothed) == len(vertices)
    assert all(abs(a - b) < 1.0 for before, after in zip(vertices, smoothed) for a, b in zip(before, after))
