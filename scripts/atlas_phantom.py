"""BodyParts3D-derived thorax label volume for the synthetic demo phantom.

Organ surfaces come from BodyParts3D, (c) The Database Center for Life Science,
licensed under CC Attribution-Share Alike 2.1 Japan. The meshes are downloaded
on first use into the local runtime cache and are never committed to this
repository. They are voxelized once into a label volume that the demo seeder
resamples per synthetic patient.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import numpy as np

ATTRIBUTION = "BodyParts3D, (c) The Database Center for Life Science licensed under CC Attribution-Share Alike 2.1 Japan"
SOURCE_URL = "https://raw.githubusercontent.com/Kevin-Mattheus-Moerman/BodyParts3D/main/assets/BodyParts3D_data/stl/FMA{fma}.stl"
ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.getenv("PHASEMED_ATLAS_DIR") or Path(os.getenv("PHASEMED_RUNTIME_DIR", str(ROOT / ".runtime"))).expanduser() / "atlas" / "bodyparts3d")
LABEL_CACHE = "thorax_labels_v2.npz"
SPACING = 2.0  # mm, isotropic reference grid in atlas space
Z_RANGE = (1125.0, 1425.0)  # atlas z (mm above the soles) covering the thorax

# Label value -> (name, FMA parts). Composite organs only exist as atomic parts.
# Later entries win where voxels overlap.
STRUCTURES: dict[int, tuple[str, tuple[int, ...]]] = {
    1: ("Right lung", (7333, 7383, 7337)),
    2: ("Left lung", (7370, 7371)),
    3: ("Heart", (7274,)),
    4: ("Thoracic aorta", (3736, 3768, 3784)),
    5: ("Trachea", (7394,)),
    6: ("Thoracic spine", (9165, 9187, 9209, 9248, 9922, 9945, 9968, 9991, 10014, 10037, 10059, 10081)),
    7: ("Rib cage", (7857, 7882, 7909, 7957, 8066, 8175, 8229, 8283, 8364, 8445, 8531, 8533,
                     7987, 8012, 8039, 8148, 8093, 8202, 8256, 8310, 8391, 8472, 8532, 8534, 7486, 7487, 7488)),
}
HOLLOW = {3}  # surfaces that describe a wall; the enclosed cavity is filled


class AtlasUnavailable(RuntimeError):
    pass


def atlas_part(fma: int, offline: bool = False) -> np.ndarray:
    """Return the triangles of one BodyParts3D part as an (n, 3, 3) float array in mm."""
    path = CACHE / f"FMA{fma}.stl"
    if not path.exists():
        if offline:
            raise AtlasUnavailable(f"FMA{fma}.stl is not cached in {CACHE}")
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / "LICENSE.txt").write_text(f"{ATTRIBUTION}\nhttps://creativecommons.org/licenses/by-sa/2.1/jp/\nSource mirror: https://github.com/Kevin-Mattheus-Moerman/BodyParts3D\n", encoding="utf-8")
        try:
            response = httpx.get(SOURCE_URL.format(fma=fma), timeout=120, follow_redirects=True).raise_for_status()
        except httpx.HTTPError as exc:
            raise AtlasUnavailable(f"FMA{fma}.stl could not be downloaded: {exc}") from exc
        partial = path.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.replace(path)
    data = path.read_bytes()
    count = int(np.frombuffer(data, dtype="<u4", count=1, offset=80)[0])
    record = np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    if len(data) < 84 + count * record.itemsize:
        raise AtlasUnavailable(f"{path.name} is truncated")
    return np.frombuffer(data, dtype=record, count=count, offset=84)["vertices"].astype(np.float64)


def _parity_fill(triangles: np.ndarray, origin: np.ndarray, shape: tuple[int, int, int], spacing: float) -> np.ndarray:
    """Fill a closed surface by casting one ray per (x, y) column along z.

    ``shape`` is (nz, ny, nx); voxel centres sit at origin + index * spacing.
    """
    nz, ny, nx = shape
    jitter = np.array([3.7e-4, 5.3e-4])  # keeps column centres off shared triangle edges
    a, b, c = (triangles[:, k, :2] - origin[:2] - jitter for k in range(3))
    za, zb, zc = (triangles[:, k, 2] for k in range(3))
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.abs(area) > 1e-9
    a, b, c, za, zb, zc, area = a[keep], b[keep], c[keep], za[keep], zb[keep], zc[keep], area[keep]
    low = np.ceil(np.minimum(np.minimum(a, b), c) / spacing).astype(np.int64)
    high = np.floor(np.maximum(np.maximum(a, b), c) / spacing).astype(np.int64)
    span = high - low + 1
    toggles = np.zeros((nz + 1, ny, nx), dtype=np.int32)
    for dx in range(int(span[:, 0].max(initial=0))):
        in_x = span[:, 0] > dx
        for dy in range(int(span[in_x, 1].max(initial=0))):
            rows = np.flatnonzero(in_x & (span[:, 1] > dy))
            ix, iy = low[rows, 0] + dx, low[rows, 1] + dy
            px, py = ix * spacing, iy * spacing
            w0 = ((b[rows, 0] - px) * (c[rows, 1] - py) - (b[rows, 1] - py) * (c[rows, 0] - px)) / area[rows]
            w1 = ((c[rows, 0] - px) * (a[rows, 1] - py) - (c[rows, 1] - py) * (a[rows, 0] - px)) / area[rows]
            w2 = 1.0 - w0 - w1
            hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0) & (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
            rows, ix, iy, w0, w1, w2 = rows[hit], ix[hit], iy[hit], w0[hit], w1[hit], w2[hit]
            z = w0 * za[rows] + w1 * zb[rows] + w2 * zc[rows]
            first_above = np.clip(np.ceil((z - origin[2]) / spacing), 0, nz).astype(np.int64)
            np.add.at(toggles, (first_above, iy, ix), 1)
    return (np.cumsum(toggles[:nz], axis=0) % 2).astype(bool)


def voxelize(triangles: np.ndarray, origin: np.ndarray, shape: tuple[int, int, int], spacing: float) -> np.ndarray:
    """Voxelize a closed triangle surface; majority vote of rays along z, y and x.

    Voting along three axes keeps small mesh defects from leaving streaks.
    """
    nz, ny, nx = shape
    along_z = _parity_fill(triangles, origin, (nz, ny, nx), spacing)
    # Rays along y: relabel axes (x, z, y) so that y plays the role of z.
    along_y = _parity_fill(triangles[:, :, [0, 2, 1]], origin[[0, 2, 1]], (ny, nz, nx), spacing).transpose(1, 0, 2)
    # Rays along x: relabel axes (z, y, x).
    along_x = _parity_fill(triangles[:, :, [2, 1, 0]], origin[[2, 1, 0]], (nx, ny, nz), spacing).transpose(2, 1, 0)
    return (along_z.astype(np.uint8) + along_y + along_x) >= 2


def dilate(mask: np.ndarray, iterations: int) -> np.ndarray:
    """6-neighbour binary dilation; erosion is ``~dilate(~mask, n)``."""
    grown = mask.copy()
    for _ in range(iterations):
        step = grown.copy()
        for axis in range(3):
            forward = [slice(None)] * 3
            backward = [slice(None)] * 3
            forward[axis], backward[axis] = slice(1, None), slice(None, -1)
            step[tuple(forward)] |= grown[tuple(backward)]
            step[tuple(backward)] |= grown[tuple(forward)]
        grown = step
    return grown


def _fill_enclosed(mask: np.ndarray) -> np.ndarray:
    """Fill cavities slice by slice along each axis (wall surfaces are open at the vessels)."""
    filled = mask.copy()
    for axis in range(3):
        solid = np.moveaxis(mask, axis, 0)
        outside = np.zeros_like(solid)
        outside[:, 0, :] = outside[:, -1, :] = outside[:, :, 0] = outside[:, :, -1] = True
        outside &= ~solid
        while True:
            grown = outside.copy()
            grown[:, 1:, :] |= outside[:, :-1, :]
            grown[:, :-1, :] |= outside[:, 1:, :]
            grown[:, :, 1:] |= outside[:, :, :-1]
            grown[:, :, :-1] |= outside[:, :, 1:]
            grown &= ~solid
            if (grown == outside).all():
                break
            outside = grown
        filled |= np.moveaxis(~outside, 0, axis)
    return filled


def thorax_labels(offline: bool = False, log=lambda message: None) -> dict:
    """Return {"labels": uint8[z, y, x], "origin": xyz mm, "spacing": mm, "names": {label: name}}."""
    cached = CACHE / LABEL_CACHE
    names = {label: name for label, (name, _) in STRUCTURES.items()}
    if cached.exists():
        with np.load(cached) as data:
            return {"labels": data["labels"], "origin": data["origin"], "spacing": float(data["spacing"]), "names": names}
    parts: dict[int, list[np.ndarray]] = {}
    for label, (name, fmas) in STRUCTURES.items():
        log(f"fetching {name} ({len(fmas)} parts)")
        parts[label] = [atlas_part(fma, offline) for fma in fmas]
    points = np.concatenate([triangles.reshape(-1, 3) for group in parts.values() for triangles in group])
    minimum = np.array([points[:, 0].min() - 8, points[:, 1].min() - 8, Z_RANGE[0]])
    maximum = np.array([points[:, 0].max() + 8, points[:, 1].max() + 8, Z_RANGE[1]])
    nx, ny, nz = (int(np.ceil(extent / SPACING)) for extent in (maximum - minimum))
    labels = np.zeros((nz, ny, nx), dtype=np.uint8)
    for label, group in parts.items():
        log(f"voxelizing {names[label]}")
        mask = np.zeros(labels.shape, dtype=bool)
        for triangles in group:
            # The whole surface is always used: crossings outside the grid still count toward parity.
            if triangles[:, :, 2].max() >= Z_RANGE[0] and triangles[:, :, 2].min() <= Z_RANGE[1]:
                mask |= voxelize(triangles, minimum, labels.shape, SPACING)
        if label in HOLLOW:
            mask = _fill_enclosed(mask)
            mask = _fill_enclosed(~dilate(~dilate(mask, 3), 3) | mask)  # close the vessel openings, then fill again
        labels[mask] = label
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cached, labels=labels, origin=minimum, spacing=SPACING)
    return {"labels": labels, "origin": minimum, "spacing": SPACING, "names": names}


def resample(atlas: dict, axes_mm: tuple[np.ndarray, np.ndarray, np.ndarray], scale: float) -> np.ndarray:
    """Sample the label volume on a patient grid given per-axis (z, y, x) coordinates in mm.

    The patient grid is centred on the atlas thorax and isotropically scaled.
    """
    labels, origin, spacing = atlas["labels"], atlas["origin"], atlas["spacing"]
    out_shape = tuple(len(axis) for axis in axes_mm)
    index, valid = [], []
    for axis_mm, size, start in zip(axes_mm, labels.shape, origin[::-1]):
        centre_atlas = start + (size - 1) * spacing / 2
        centre_patient = (axis_mm[0] + axis_mm[-1]) / 2
        position = np.rint((centre_atlas + (axis_mm - centre_patient) / scale - start) / spacing).astype(np.int64)
        valid.append((position >= 0) & (position < size))
        index.append(np.clip(position, 0, size - 1))
    sampled = labels[np.ix_(*index)]
    sampled[~np.logical_and.outer(np.logical_and.outer(valid[0], valid[1]), valid[2]).reshape(out_shape)] = 0
    return sampled
