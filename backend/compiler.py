from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Callable

import pydicom
import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.measure import marching_cubes, mesh_surface_area

from .dicom import extract_pixels, index_directory, scaled_pixel_array
from .dicomweb import capability_status as dicomweb_capability_status
from .geometry import SpatialIndex, calculate_relationships
from .adapters import run_segmentation, status as adapter_status
from .models import BoundingBox, Geometry, JobState, PatientModel, PatientObject, SourceReference, TimelineEntry, now_iso


STAGES = [
    "Validate DICOM study",
    "Reconstruct source volume",
    "Extract image statistics",
    "Generate object candidates",
    "Build spatial index",
    "Calculate relationships",
    "Bind available context",
    "Register compatible prior models",
    "Persist PatientModel",
]


def _source(study_id: str, title: str = "Imported DICOM study") -> SourceReference:
    return SourceReference(id=f"source:{study_id}", type="dicom-study", title=title, date=now_iso(), uri=f"study://{study_id}")


def _candidate_from_series(study_id: str, row: dict, index: int, stats: list[dict] | None = None) -> PatientObject:
    # A source-volume object is real, derived from the DICOM series metadata.
    # It is deliberately not labeled as anatomy until a validated segmentation
    # model is configured.
    rows = float(row.get("rows") or 1) * float((row.get("pixel_spacing") or (1.0, 1.0))[0])
    cols = float(row.get("columns") or 1) * float((row.get("pixel_spacing") or (1.0, 1.0))[1])
    instances = row.get("instances", [])
    orientation = next((item.get("orientation") for item in instances if item.get("orientation")), None) or (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    row_axis, column_axis = orientation[:3], orientation[3:6]
    spacing = row.get("pixel_spacing") or (1.0, 1.0)
    points = []
    for instance in instances or [{}]:
        origin = instance.get("position") or (0.0, 0.0, float(instance.get("z") or 0.0))
        for column, row_index in ((0.0, 0.0), (float(row.get("columns") or 1), 0.0), (0.0, float(row.get("rows") or 1)), (float(row.get("columns") or 1), float(row.get("rows") or 1))):
            points.append(tuple(origin[axis] + row_axis[axis] * column * spacing[1] + column_axis[axis] * row_index * spacing[0] for axis in range(3)))
    box = BoundingBox(min=tuple(min(point[i] for point in points) for i in range(3)), max=tuple(max(point[i] for point in points) for i in range(3)))
    geometry = Geometry(centroid=tuple((box.min[i] + box.max[i]) / 2 for i in range(3)), bounding_box=box, volume_mm3=box_volume(box), surface_area_mm2=box_surface_area(box), source_frame="patient")
    source = _source(study_id)
    return PatientObject(
        id=f"series:{row['series_instance_uid']}",
        type="volume",
        label=row.get("description") or f"Series {index + 1}",
        geometry=geometry,
        sources=[source],
        review_status="unreviewed",
        metadata={"series_instance_uid": row["series_instance_uid"], "modality": row.get("modality"), "instance_count": row.get("instance_count"), "series_number": row.get("series_number"), "pixel_spacing_mm": row.get("pixel_spacing"), "pixel_statistics": stats or []},
    )


def _intensity_region_candidates(study_id: str, root: Path, row: dict) -> list[PatientObject]:
    """Extract unlabeled high-intensity regions from readable source pixels.

    This is a deterministic geometry stage, not an anatomical segmentation
    model. The output is intentionally typed as ``region`` and stays
    unreviewed until a validated clinical adapter replaces it.
    """
    voxels: set[tuple[int, int, int]] = set()
    positions: dict[int, tuple[float, float, float]] = {}
    orientation = next((item.get("orientation") for item in row.get("instances", []) if item.get("orientation")), None) or (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    row_axis, column_axis = orientation[:3], orientation[3:6]
    for z_index, instance in enumerate(row.get("instances", [])):
        try:
            ds = pydicom.dcmread(str(root / instance["path"]), force=False)
            array = scaled_pixel_array(ds)
            if array.ndim > 2:
                array = array[0]
            threshold = float(np.percentile(array, 97))
            ys, xs = np.where(array >= threshold)
            if len(xs) < 2:
                continue
            positions[z_index] = tuple(instance.get("position") or (0.0, 0.0, float(instance.get("z") or 0.0)))
            spacing = row.get("pixel_spacing") or (1.0, 1.0)
            stride = max(1, len(xs) // 1000)
            voxels.update((int(x), int(y), z_index) for x, y in zip(xs[::stride], ys[::stride]))
        except Exception:
            continue
    if len(voxels) < 2:
        return []
    components: list[set[tuple[int, int, int]]] = []
    remaining = set(voxels)
    while remaining and len(components) < 12:
        seed = remaining.pop()
        component = {seed}
        stack = [seed]
        while stack:
            x, y, z_index = stack.pop()
            for neighbor in ((x + 1, y, z_index), (x - 1, y, z_index), (x, y + 1, z_index), (x, y - 1, z_index), (x, y, z_index + 1), (x, y, z_index - 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        if len(component) >= 3:
            components.append(component)
    spacing = row.get("pixel_spacing") or (1.0, 1.0)
    sx, sy = float(spacing[0]), float(spacing[1])
    sz = float(row.get("slice_thickness") or 1.0)
    source = _source(study_id)
    result: list[PatientObject] = []
    for component_index, component in enumerate(sorted(components, key=len, reverse=True)[:8], start=1):
        points = [tuple(positions.get(z, (0.0, 0.0, float(z) * sz))[axis] + row_axis[axis] * x * sy + column_axis[axis] * y * sx for axis in range(3)) for x, y, z in component]
        minimum = tuple(min(point[i] for point in points) for i in range(3))
        maximum = tuple(max(point[i] for point in points) for i in range(3))
        if maximum[2] == minimum[2]:
            maximum = (maximum[0] + sy, maximum[1] + sx, minimum[2] + sz)
        box = BoundingBox(min=minimum, max=maximum)
        object_id = f"region:{row['series_instance_uid']}:high:{component_index}"
        mesh_id, mesh_path, vertex_count, face_count, surface_area = _write_voxel_mesh(
            root,
            object_id,
            component,
            sx,
            sy,
            sz,
            origin=positions.get(0, (0.0, 0.0, 0.0)),
            row_axis=row_axis,
            column_axis=column_axis,
            frame_origins=positions,
        )
        geometry = Geometry(centroid=tuple((box.min[i] + box.max[i]) / 2 for i in range(3)), bounding_box=box, volume_mm3=round(len(component) * sx * sy * sz, 3), surface_area_mm2=surface_area, mesh_id=mesh_id, segmentation_id=f"derived:{object_id}", source_frame="patient", voxel_count=len(component))
        result.append(PatientObject(id=object_id, type="region", label=f"High-intensity region · unlabeled {component_index:02d}", geometry=geometry, sources=[source], review_status="unreviewed", metadata={"series_instance_uid": row["series_instance_uid"], "derived_from": "pixel-percentile-97-connected-component", "voxel_count": len(component), "mesh_path": mesh_path, "mesh_vertex_count": vertex_count, "mesh_face_count": face_count, "mesh_smoothing": "taubin" if mesh_path else None}))
    return result


def _segmentation_objects(study_id: str, root: Path, row: dict) -> list[PatientObject]:
    """Turn a DICOM SEG labelmap into persisted PatientObjects.

    This consumes an explicitly supplied segmentation object. It does not
    infer anatomy from pixels and therefore keeps provenance and review state
    visible until a clinician confirms the derived object.
    """
    if str(row.get("modality") or "").upper() != "SEG":
        return []
    segment_labels: dict[int, str] = {}
    first_path = next((instance.get("path") for instance in row.get("instances", [])), None)
    if not first_path:
        return []
    try:
        ds = pydicom.dcmread(str(root / first_path), force=False)
        for segment in getattr(ds, "SegmentSequence", []):
            segment_labels[int(getattr(segment, "SegmentNumber"))] = str(getattr(segment, "SegmentLabel", f"Segment {getattr(segment, 'SegmentNumber')}"))
        array = ds.pixel_array
    except Exception:
        return []
    if array.ndim == 2:
        array = array[None, ...]
    spacing = row.get("pixel_spacing") or (1.0, 1.0)
    sx, sy = float(spacing[0]), float(spacing[1])
    sz = float(row.get("slice_thickness") or 1.0)
    points: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    voxels: dict[int, set[tuple[int, int, int]]] = defaultdict(set)
    voxel_counts: dict[int, int] = defaultdict(int)
    frame_groups = getattr(ds, "PerFrameFunctionalGroupsSequence", [])
    shared_groups = getattr(ds, "SharedFunctionalGroupsSequence", [])

    def sequence_item(group, name: str):
        sequence = getattr(group, name, None) if group is not None else None
        return sequence[0] if sequence else None

    shared = shared_groups[0] if shared_groups else None
    shared_orientation = sequence_item(shared, "PlaneOrientationSequence")
    shared_measures = sequence_item(shared, "PixelMeasuresSequence")
    shared_row_axis = tuple(float(value) for value in getattr(shared_orientation, "ImageOrientationPatient", (1, 0, 0, 0, 1, 0))[:3]) if shared_orientation else (1.0, 0.0, 0.0)
    shared_column_axis = tuple(float(value) for value in getattr(shared_orientation, "ImageOrientationPatient", (1, 0, 0, 0, 1, 0))[3:6]) if shared_orientation else (0.0, 1.0, 0.0)
    if shared_measures and getattr(shared_measures, "PixelSpacing", None):
        sx, sy = (float(value) for value in shared_measures.PixelSpacing[:2])
    if shared_measures and getattr(shared_measures, "SliceThickness", None):
        sz = float(shared_measures.SliceThickness)
    frame_origins: dict[int, tuple[float, float, float]] = {}
    frame_axes: dict[int, tuple[tuple[float, float, float], tuple[float, float, float], float, float]] = {}
    for frame_index, frame in enumerate(array):
        segment_number = 1
        if frame_index < len(frame_groups):
            identification = getattr(frame_groups[frame_index], "SegmentIdentificationSequence", [])
            if identification:
                segment_number = int(getattr(identification[0], "ReferencedSegmentNumber", 1))
        frame_group = frame_groups[frame_index] if frame_index < len(frame_groups) else None
        plane_position = sequence_item(frame_group, "PlanePositionSequence")
        plane_orientation = sequence_item(frame_group, "PlaneOrientationSequence") or shared_orientation
        measures = sequence_item(frame_group, "PixelMeasuresSequence") or shared_measures
        orientation = getattr(plane_orientation, "ImageOrientationPatient", None) if plane_orientation else None
        row_axis = tuple(float(value) for value in orientation[:3]) if orientation and len(orientation) >= 6 else shared_row_axis
        column_axis = tuple(float(value) for value in orientation[3:6]) if orientation and len(orientation) >= 6 else shared_column_axis
        position = getattr(plane_position, "ImagePositionPatient", None) if plane_position else getattr(ds, "ImagePositionPatient", None)
        if position is not None and len(position) >= 3:
            origin = tuple(float(value) for value in position[:3])
        else:
            origin = (0.0, 0.0, float(frame_index) * sz)
        frame_sx, frame_sy = sx, sy
        if measures and getattr(measures, "PixelSpacing", None):
            frame_sx, frame_sy = (float(value) for value in measures.PixelSpacing[:2])
        frame_origins[frame_index] = origin
        frame_axes[frame_index] = (row_axis, column_axis, frame_sx, frame_sy)
        ys, xs = np.where(frame > 0)
        if not len(xs):
            continue
        for x, y in zip(xs.tolist(), ys.tolist()):
            points[segment_number].append(tuple(origin[axis] + row_axis[axis] * float(x) * frame_sy + column_axis[axis] * float(y) * frame_sx for axis in range(3)))
            voxels[segment_number].add((int(x), int(y), frame_index))
        voxel_counts[segment_number] += len(xs)
    source = SourceReference(id=f"source:{study_id}:seg:{row['series_instance_uid']}", type="dicom-segmentation", title=row.get("description") or "DICOM segmentation", date=now_iso(), uri=f"dicomseg://{row['series_instance_uid']}")
    result = []
    for segment_number, values in points.items():
        minimum = tuple(min(point[i] for point in values) for i in range(3))
        maximum = tuple(max(point[i] for point in values) for i in range(3))
        if maximum[2] == minimum[2]:
            maximum = (maximum[0], maximum[1], minimum[2] + sz)
        box = BoundingBox(min=minimum, max=maximum)
        label = segment_labels.get(segment_number, f"Segment {segment_number}")
        lower = label.lower()
        object_type = "finding" if any(term in lower for term in ("lesion", "nodule", "tumor", "finding")) else "anatomy"
        object_id = f"seg:{row['series_instance_uid']}:{segment_number}"
        segment_frame_indices = [frame for _, _, frame in voxels[segment_number]]
        first_frame = min(segment_frame_indices) if segment_frame_indices else 0
        segment_row_axis, segment_column_axis, segment_sx, segment_sy = frame_axes.get(first_frame, (shared_row_axis, shared_column_axis, sx, sy))
        mesh_id, mesh_path, vertex_count, face_count, surface_area = _write_voxel_mesh(
            root,
            object_id,
            voxels[segment_number],
            segment_sx,
            segment_sy,
            sz,
            origin=frame_origins.get(first_frame, (0.0, 0.0, 0.0)),
            row_axis=segment_row_axis,
            column_axis=segment_column_axis,
            frame_origins=frame_origins,
        )
        geometry = Geometry(centroid=tuple((box.min[i] + box.max[i]) / 2 for i in range(3)), bounding_box=box, volume_mm3=round(voxel_counts[segment_number] * sx * sy * sz, 3), surface_area_mm2=surface_area, mesh_id=mesh_id, segmentation_id=f"seg:{row['series_instance_uid']}:{segment_number}", source_frame="patient")
        result.append(PatientObject(id=object_id, type=object_type, label=label, geometry=geometry, sources=[source], review_status="unreviewed", metadata={"segment_number": segment_number, "series_instance_uid": row["series_instance_uid"], "derived_from": "DICOM SEG labelmap", "mesh_path": mesh_path, "mesh_vertex_count": vertex_count, "mesh_face_count": face_count, "mesh_smoothing": "taubin" if mesh_path else None}))
    return result


def _taubin_smooth(vertices: list[tuple[float, float, float]], faces: list[tuple[int, ...]], iterations: int = 25, shrink: float = 0.5, inflate: float = -0.53) -> list[tuple[float, float, float]]:
    """Relax the voxel staircase without shrinking the surface (Taubin lambda/mu smoothing).

    Topology, vertex order and face count are unchanged; measurements stay voxel-derived.
    """
    if len(vertices) < 4 or not faces:
        return vertices
    points = np.asarray(vertices, dtype=np.float64)
    quads = np.asarray(faces, dtype=np.int64) - 1
    edges = np.concatenate([np.stack([quads[:, corner], quads[:, (corner + 1) % quads.shape[1]]], axis=1) for corner in range(quads.shape[1])])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    edges = edges[edges[:, 0] != edges[:, 1]]
    degree = np.bincount(edges.reshape(-1), minlength=len(points)).astype(np.float64)[:, None]
    connected = degree[:, 0] > 0
    for _ in range(iterations):
        for factor in (shrink, inflate):
            total = np.zeros_like(points)
            np.add.at(total, edges[:, 0], points[edges[:, 1]])
            np.add.at(total, edges[:, 1], points[edges[:, 0]])
            points[connected] += factor * (total[connected] / degree[connected] - points[connected])
    return [tuple(point) for point in points.tolist()]


def _write_voxel_mesh(
    root: Path,
    object_id: str,
    voxels: set[tuple[int, int, int]],
    sx: float,
    sy: float,
    sz: float,
    *,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    row_axis: tuple[float, float, float] = (1.0, 0.0, 0.0),
    column_axis: tuple[float, float, float] = (0.0, 1.0, 0.0),
    frame_origins: dict[int, tuple[float, float, float]] | None = None,
) -> tuple[str | None, str | None, int, int, float | None]:
    """Write an OBJ surface mesh for a supplied binary labelmap.

    Tiny regions retain the exact exposed-voxel surface used by the local
    fallback compiler. Larger clinical masks use marching cubes with an
    adaptive step size, so whole organs are represented without creating
    multi-million-triangle browser payloads.
    """
    if not voxels:
        return None, None, 0, 0, None
    if len(voxels) > 64:
        return _write_marching_mesh(
            root,
            object_id,
            voxels,
            sx,
            sy,
            sz,
            origin=origin,
            row_axis=row_axis,
            column_axis=column_axis,
            frame_origins=frame_origins,
        )
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", object_id)
    path = root / "derived" / f"{safe}.obj"
    path.parent.mkdir(parents=True, exist_ok=True)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    lookup: dict[tuple[float, float, float], int] = {}
    surface_area = 0.0
    face_defs = [
        ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
        ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
        ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
        ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
        ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
        ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    ]
    normal_axis = (
        row_axis[1] * column_axis[2] - row_axis[2] * column_axis[1],
        row_axis[2] * column_axis[0] - row_axis[0] * column_axis[2],
        row_axis[0] * column_axis[1] - row_axis[1] * column_axis[0],
    )

    def transformed_vertex(x: int, y: int, z: int, cx: int, cy: int, cz: int) -> tuple[float, float, float]:
        base = (frame_origins or {}).get(z, origin)
        local = (
            row_axis[0] * (x + cx) * sy + column_axis[0] * (y + cy) * sx + normal_axis[0] * cz * sz,
            row_axis[1] * (x + cx) * sy + column_axis[1] * (y + cy) * sx + normal_axis[1] * cz * sz,
            row_axis[2] * (x + cx) * sy + column_axis[2] * (y + cy) * sx + normal_axis[2] * cz * sz,
        )
        return tuple(base[axis] + local[axis] for axis in range(3))

    for x, y, z in voxels:
        for normal, corners in face_defs:
            neighbor = (x + normal[0], y + normal[1], z + normal[2])
            if neighbor in voxels:
                continue
            surface_area += (sx * sz) if normal[0] else (sy * sz) if normal[1] else (sx * sy)
            indices = []
            for cx, cy, cz in corners:
                vertex = transformed_vertex(x, y, z, cx, cy, cz)
                if vertex not in lookup:
                    lookup[vertex] = len(vertices) + 1
                    vertices.append(vertex)
                indices.append(lookup[vertex])
            faces.append(tuple(indices))
    vertices = _taubin_smooth(vertices, faces)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Phasmed surface mesh for {object_id}\n")
        for vertex in vertices:
            handle.write(f"v {vertex[0]:.5f} {vertex[1]:.5f} {vertex[2]:.5f}\n")
        for face in faces:
            handle.write(f"f {' '.join(str(index) for index in face)}\n")
    return f"mesh:{object_id}", path.relative_to(root).as_posix(), len(vertices), len(faces), round(surface_area, 3)


def _write_marching_mesh(
    root: Path,
    object_id: str,
    voxels: set[tuple[int, int, int]],
    sx: float,
    sy: float,
    sz: float,
    *,
    origin: tuple[float, float, float],
    row_axis: tuple[float, float, float],
    column_axis: tuple[float, float, float],
    frame_origins: dict[int, tuple[float, float, float]] | None,
) -> tuple[str | None, str | None, int, int, float | None]:
    """Extract a smooth, bounded-complexity surface from a binary mask."""
    xs = [voxel[0] for voxel in voxels]
    ys = [voxel[1] for voxel in voxels]
    normal_axis = (
        row_axis[1] * column_axis[2] - row_axis[2] * column_axis[1],
        row_axis[2] * column_axis[0] - row_axis[0] * column_axis[2],
        row_axis[0] * column_axis[1] - row_axis[1] * column_axis[0],
    )
    z_keys = sorted(
        {voxel[2] for voxel in voxels},
        key=lambda value: sum((frame_origins or {}).get(value, (origin[0], origin[1], origin[2] + value * sz))[axis] * normal_axis[axis] for axis in range(3)),
    )
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    z_lookup = {value: index for index, value in enumerate(z_keys)}
    mask = np.zeros((len(z_keys), max_y - min_y + 1, max_x - min_x + 1), dtype=np.uint8)
    for x, y, z in voxels:
        mask[z_lookup[z], y - min_y, x - min_x] = 1
    mask = np.pad(mask, 1)
    # The fast TotalSegmentator model operates on a coarser volume than the
    # source CT. Smooth in labelmap space before extracting the isosurface so
    # slice spacing does not read as regular ribs/bands on curved organs.
    field = gaussian_filter(mask.astype(np.float32), sigma=(1.2, 1.05, 1.05), mode="constant") if len(voxels) > 1_000 else mask
    vertices, faces, _, _ = marching_cubes(field, level=0.5, step_size=1, allow_degenerate=False)
    vertices -= 1.0
    origins = [(frame_origins or {}).get(key, tuple(origin[i] + normal_axis[i] * key * sz for i in range(3))) for key in z_keys]

    def slice_origin(position: float) -> tuple[float, float, float]:
        if len(origins) == 1:
            return tuple(origins[0][i] + normal_axis[i] * position * sz for i in range(3))
        if position <= 0:
            return tuple(origins[0][i] + (origins[1][i] - origins[0][i]) * position for i in range(3))
        if position >= len(origins) - 1:
            amount = position - (len(origins) - 1)
            return tuple(origins[-1][i] + (origins[-1][i] - origins[-2][i]) * amount for i in range(3))
        lower = int(math.floor(position))
        amount = position - lower
        return tuple(origins[lower][i] + (origins[lower + 1][i] - origins[lower][i]) * amount for i in range(3))

    patient_vertices: list[tuple[float, float, float]] = []
    for z_value, y_value, x_value in vertices:
        base = slice_origin(float(z_value))
        x = min_x + float(x_value)
        y = min_y + float(y_value)
        patient_vertices.append(tuple(base[i] + row_axis[i] * x * sy + column_axis[i] * y * sx for i in range(3)))
    patient_points = np.asarray(patient_vertices, dtype=np.float64)
    if len(faces) > 120_000:
        # Vertex clustering reduces transfer/GPU cost after full-resolution
        # extraction. Unlike marching with a coarse step, this keeps curved
        # organ silhouettes free of regular sampling bands.
        cell = max(1.25, min(sx, sy, sz) * 1.5)
        keys = np.floor(patient_points / cell).astype(np.int64)
        _, inverse = np.unique(keys, axis=0, return_inverse=True)
        counts = np.bincount(inverse)
        patient_points = np.column_stack([np.bincount(inverse, weights=patient_points[:, axis]) / counts for axis in range(3)])
        faces = inverse[faces]
        valid = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
        faces = faces[valid]
        _, unique_indices = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
        faces = faces[np.sort(unique_indices)]
    triangle_faces = [tuple(int(index) + 1 for index in face) for face in faces]
    patient_vertices = _taubin_smooth([tuple(point) for point in patient_points], triangle_faces, iterations=28)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", object_id)
    path = root / "derived" / f"{safe}.obj"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Phasmed marching-cubes surface for {object_id}\n")
        for vertex in patient_vertices:
            handle.write(f"v {vertex[0]:.5f} {vertex[1]:.5f} {vertex[2]:.5f}\n")
        for face in triangle_faces:
            handle.write(f"f {' '.join(str(index) for index in face)}\n")
    area = mesh_surface_area(np.asarray(patient_vertices, dtype=np.float64), faces)
    return f"mesh:{object_id}", path.relative_to(root).as_posix(), len(patient_vertices), len(triangle_faces), round(float(area), 3)


def box_volume(box: BoundingBox) -> float:
    return math.prod(max(0.0, box.max[i] - box.min[i]) for i in range(3))


def box_surface_area(box: BoundingBox) -> float:
    x, y, z = (max(0.0, box.max[i] - box.min[i]) for i in range(3))
    return 2.0 * (x * y + x * z + y * z)


def compile_study(study_id: str, root: Path, update: Callable[[str, int, str], None]) -> PatientModel:
    update("Validate DICOM study", 8, "Indexing readable instances")
    study, indexed_series = index_directory(root, study_id)
    # Adapter outputs live beneath the immutable source study. Never feed
    # those derived instances back into a later compiler run as raw input.
    series = []
    for row in indexed_series:
        instances = [item for item in row.get("instances", []) if not Path(item.get("path", "")).parts or Path(item.get("path", "")).parts[0] != "derived"]
        if instances:
            series.append({**row, "instances": instances, "instance_count": len(instances)})
    study.image_count = sum(int(row.get("instance_count") or 0) for row in series)
    study.series_count = len(series)
    update("Reconstruct source volume", 22, f"Found {study.image_count} instances across {study.series_count} series")
    primary = max(series, key=lambda item: int(item.get("instance_count") or 0))
    stats = extract_pixels(root, primary.get("instances", []))
    update("Extract image statistics", 34, f"Read {len(stats)} source frames")
    stats_by_path = {item.get("path"): item for item in stats}
    objects = []
    segmentation_objects = []
    adapter_result = run_segmentation(root, study_id)
    if adapter_result.get("method") == "configured-command" and adapter_result.get("status") == "completed":
        update("Generate object candidates", 42, "Configured anatomy segmentation adapter completed")
    elif adapter_result.get("status") == "failed":
        update("Generate object candidates", 42, "Segmentation adapter failed; retaining source-derived objects")
    adapter_root = root / "derived" / "adapter-segmentation"
    if adapter_result.get("status") == "completed" and adapter_root.exists():
        try:
            _, adapter_series = index_directory(adapter_root, study_id)
            for row in adapter_series:
                if str(row.get("modality") or "").upper() == "SEG":
                    segmentation_objects.extend(_segmentation_objects(study_id, adapter_root, row))
        except Exception as exc:
            adapter_result["status"] = "failed"
            adapter_result["error"] = f"Could not index adapter DICOM SEG output: {exc}"
    for index, row in enumerate(series):
        if str(row.get("modality") or "").upper() == "SEG":
            segmentation_objects.extend(_segmentation_objects(study_id, root, row))
        else:
            objects.append(_candidate_from_series(study_id, row, index, [stats_by_path.get(instance.get("path"), {}) for instance in row.get("instances", [])]))
    region_candidates = _intensity_region_candidates(study_id, root, primary) if str(primary.get("modality") or "").upper() != "SEG" else []
    objects.extend(segmentation_objects)
    objects.extend(region_candidates)
    update("Generate object candidates", 48, f"Created {len(objects)} source-volume, segmentation, and intensity objects")
    spatial_index = SpatialIndex(objects)
    update("Build spatial index", 61, f"Indexed {len(spatial_index.objects)} objects for proximity queries")
    relationships = calculate_relationships(objects, study_id)
    relationship_ids = {obj.id: [] for obj in objects}
    for relationship in relationships:
        relationship_ids.setdefault(relationship.source_object_id, []).append(relationship.id)
    for obj in objects:
        obj.relationships = relationship_ids.get(obj.id, [])
    update("Calculate relationships", 76, f"Calculated {len(relationships)} deterministic spatial relationships")
    update("Bind available context", 84, "No external context source configured; imaging provenance retained")
    update("Register compatible prior models", 92, "Prior matching runs after persistence when a compatible patient model exists")
    model = PatientModel(
        id=f"model:{study_id}:v1",
        patient_id=study.patient_id or f"local:{study_id}",
        study_id=study_id,
        version=1,
        objects=objects,
        relationships=relationships,
        timeline=[TimelineEntry(id=f"timeline:{study_id}", study_id=study_id, date=study.study_date or "unknown", label=study.description or study.modality or "Imported study", model_id=f"model:{study_id}:v1")],
        sources=[_source(study_id)],
        capabilities={
            "dicom_ingestion": "available",
            "volume_metadata": "available",
            "pixel_statistics": "available" if stats else "partial",
            "spatial_index": "available",
            "validated_anatomy_segmentation": "available" if segmentation_objects else "not_configured",
            "mesh_generation": "available" if any(item.geometry and item.geometry.mesh_id for item in objects) else "not_configured",
            "unlabeled_intensity_regions": "available" if region_candidates else "partial",
            "clinical_context": "not_configured",
            "temporal_registration": "not_configured",
            **adapter_status(),
            **dicomweb_capability_status(),
        },
        metadata={"source_series_count": len(series), "source_instance_count": study.image_count, "spatial_index": {"type": "uniform-grid", "cell_size_mm": 64.0, "object_count": len(spatial_index.objects)}, "segmentation_adapter": adapter_result},
        pipeline_version="local-compiler-0.1",
    )
    update("Persist PatientModel", 100, "PatientModel ready")
    return model
