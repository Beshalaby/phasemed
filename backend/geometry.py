from __future__ import annotations

import math
from itertools import combinations
from typing import Iterable

from .models import BoundingBox, Geometry, PatientObject, Relationship, SourceReference


def distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def box_distance(a: BoundingBox, b: BoundingBox) -> float:
    gap = []
    for axis in range(3):
        if a.max[axis] < b.min[axis]:
            gap.append(b.min[axis] - a.max[axis])
        elif b.max[axis] < a.min[axis]:
            gap.append(a.min[axis] - b.max[axis])
        else:
            gap.append(0.0)
    return distance(tuple(gap), (0.0, 0.0, 0.0))


def contains(container: BoundingBox, inner: BoundingBox) -> bool:
    return all(container.min[i] <= inner.min[i] and container.max[i] >= inner.max[i] for i in range(3))


def intersects(a: BoundingBox, b: BoundingBox) -> bool:
    return all(a.min[i] <= b.max[i] and b.min[i] <= a.max[i] for i in range(3))


def minimum_surface_distance(a: BoundingBox, b: BoundingBox) -> float:
    """Return the deterministic AABB surface gap in millimetres.

    Mesh-aware implementations can replace this primitive without changing
    the PatientModel API. The compiler records that this local adapter uses
    bounding boxes so the result is never mistaken for a mesh measurement.
    """
    return box_distance(a, b)


def adjacent(a: BoundingBox, b: BoundingBox, tolerance_mm: float = 1.0) -> bool:
    return not intersects(a, b) and minimum_surface_distance(a, b) <= tolerance_mm


def box_volume(box: BoundingBox) -> float:
    return math.prod(max(0.0, box.max[i] - box.min[i]) for i in range(3))


def _source(study_id: str) -> SourceReference:
    return SourceReference(id=f"source:{study_id}", type="dicom-study", title="Imported DICOM study", uri=f"study://{study_id}")


def calculate_relationships(objects: Iterable[PatientObject], study_id: str, near_mm: float = 45.0) -> list[Relationship]:
    items = [obj for obj in objects if obj.geometry]
    result: list[Relationship] = []
    source = _source(study_id)
    for a, b in combinations(items, 2):
        assert a.geometry and b.geometry
        if contains(a.geometry.bounding_box, b.geometry.bounding_box):
            result.append(Relationship(id=f"rel:{a.id}:{b.id}:contains", source_object_id=a.id, target_object_id=b.id, type="contains", method="bbox-containment", provenance=[source]))
            result.append(Relationship(id=f"rel:{b.id}:{a.id}:inside", source_object_id=b.id, target_object_id=a.id, type="inside", method="bbox-containment", provenance=[source]))
            continue
        if contains(b.geometry.bounding_box, a.geometry.bounding_box):
            result.append(Relationship(id=f"rel:{a.id}:{b.id}:inside", source_object_id=a.id, target_object_id=b.id, type="inside", method="bbox-containment", provenance=[source]))
            result.append(Relationship(id=f"rel:{b.id}:{a.id}:contains", source_object_id=b.id, target_object_id=a.id, type="contains", method="bbox-containment", provenance=[source]))
            continue
        if intersects(a.geometry.bounding_box, b.geometry.bounding_box):
            result.append(Relationship(id=f"rel:{a.id}:{b.id}:intersects", source_object_id=a.id, target_object_id=b.id, type="intersects", method="bbox-intersection", provenance=[source]))
            result.append(Relationship(id=f"rel:{b.id}:{a.id}:intersects", source_object_id=b.id, target_object_id=a.id, type="intersects", method="bbox-intersection", provenance=[source]))
            continue
        d = box_distance(a.geometry.bounding_box, b.geometry.bounding_box)
        if d <= near_mm:
            result.append(Relationship(id=f"rel:{a.id}:{b.id}:near", source_object_id=a.id, target_object_id=b.id, type="near", value=round(d, 3), unit="mm", method="bbox-distance", provenance=[source]))
            result.append(Relationship(id=f"rel:{b.id}:{a.id}:near", source_object_id=b.id, target_object_id=a.id, type="near", value=round(d, 3), unit="mm", method="bbox-distance", provenance=[source]))
    return result


class SpatialIndex:
    """Small dependency-free broad-phase index for local model queries.

    It uses a uniform grid over object bounding boxes. The public query method
    is deliberately narrow so it can be swapped for an R-tree in a deployed
    service without changing callers.
    """

    def __init__(self, objects: Iterable[PatientObject], cell_size_mm: float = 64.0) -> None:
        self.cell_size_mm = max(float(cell_size_mm), 1.0)
        self.objects = [obj for obj in objects if obj.geometry]
        self.cells: dict[tuple[int, int, int], list[PatientObject]] = {}
        for obj in self.objects:
            assert obj.geometry
            box = obj.geometry.bounding_box
            mins = tuple(math.floor(box.min[i] / self.cell_size_mm) for i in range(3))
            maxs = tuple(math.floor(box.max[i] / self.cell_size_mm) for i in range(3))
            for x in range(mins[0], maxs[0] + 1):
                for y in range(mins[1], maxs[1] + 1):
                    for z in range(mins[2], maxs[2] + 1):
                        self.cells.setdefault((x, y, z), []).append(obj)

    def query(self, box: BoundingBox) -> list[PatientObject]:
        mins = tuple(math.floor(box.min[i] / self.cell_size_mm) for i in range(3))
        maxs = tuple(math.floor(box.max[i] / self.cell_size_mm) for i in range(3))
        found: dict[str, PatientObject] = {}
        for x in range(mins[0], maxs[0] + 1):
            for y in range(mins[1], maxs[1] + 1):
                for z in range(mins[2], maxs[2] + 1):
                    for obj in self.cells.get((x, y, z), []):
                        found[obj.id] = obj
        return list(found.values())

    def candidates_for_object(self, object_id: str, radius_mm: float | None = None) -> list[PatientObject]:
        source = next((obj for obj in self.objects if obj.id == object_id), None)
        if not source or not source.geometry:
            return []
        padding = self.cell_size_mm if radius_mm is None else max(float(radius_mm), 0.0)
        box = source.geometry.bounding_box
        expanded = BoundingBox(min=tuple(box.min[i] - padding for i in range(3)), max=tuple(box.max[i] + padding for i in range(3)))
        candidates = self.query(expanded)
        return candidates if len(candidates) > 1 else self.objects


def nearest(objects: Iterable[PatientObject], object_id: str, limit: int = 5) -> list[dict[str, float | str]]:
    items = [obj for obj in objects if obj.geometry]
    source = next((obj for obj in items if obj.id == object_id), None)
    if not source or not source.geometry:
        return []
    values = []
    for obj in items:
        if obj.id == object_id or not obj.geometry:
            continue
        # A containing parent is a graph relationship, not a useful nearest
        # surface candidate. Skip it so a lesion inside a lung resolves to the
        # closest neighboring structure rather than its enclosing anatomy.
        if contains(obj.geometry.bounding_box, source.geometry.bounding_box):
            continue
        values.append({"object_id": obj.id, "label": obj.label, "distance_mm": round(box_distance(source.geometry.bounding_box, obj.geometry.bounding_box), 3)})
    return sorted(values, key=lambda item: float(item["distance_mm"]))[:limit]


def within_radius(objects: Iterable[PatientObject], object_id: str, radius_mm: float) -> list[dict[str, float | str]]:
    return [item for item in nearest(objects, object_id, limit=100) if float(item["distance_mm"]) <= radius_mm]


def trajectory_length(start: tuple[float, float, float], end: tuple[float, float, float]) -> float:
    return distance(start, end)


def _segment_box_distance(start: tuple[float, float, float], end: tuple[float, float, float], box: BoundingBox) -> float:
    """Compute the exact Euclidean distance between a segment and an AABB."""
    if segment_intersects_box(start, end, box):
        return 0.0
    delta = tuple(end[i] - start[i] for i in range(3))
    breakpoints = {0.0, 1.0}
    for axis in range(3):
        if abs(delta[axis]) < 1e-12:
            continue
        for bound in (box.min[axis], box.max[axis]):
            t = (bound - start[axis]) / delta[axis]
            if 0.0 < t < 1.0:
                breakpoints.add(t)
    ordered = sorted(breakpoints)

    def squared_distance(t: float) -> float:
        point = tuple(start[i] + delta[i] * t for i in range(3))
        return sum(
            (box.min[i] - point[i]) ** 2 if point[i] < box.min[i]
            else (point[i] - box.max[i]) ** 2 if point[i] > box.max[i]
            else 0.0
            for i in range(3)
        )

    best = min(squared_distance(t) for t in ordered)
    for left, right in zip(ordered, ordered[1:]):
        midpoint = (left + right) / 2.0
        a = 0.0
        b = 0.0
        for axis in range(3):
            value = start[axis] + delta[axis] * midpoint
            if value < box.min[axis]:
                c = box.min[axis] - start[axis]
                a += delta[axis] ** 2
                b -= 2.0 * c * delta[axis]
            elif value > box.max[axis]:
                c = start[axis] - box.max[axis]
                a += delta[axis] ** 2
                b += 2.0 * c * delta[axis]
        if a > 1e-12:
            stationary = max(left, min(right, -b / (2.0 * a)))
            best = min(best, squared_distance(stationary))
    return math.sqrt(max(best, 0.0))


def trajectory_clearance(start: tuple[float, float, float], end: tuple[float, float, float], obj: PatientObject) -> float | None:
    if not obj.geometry:
        return None
    return round(_segment_box_distance(start, end, obj.geometry.bounding_box), 3)


def segment_intersects_box(start: tuple[float, float, float], end: tuple[float, float, float], box: BoundingBox) -> bool:
    """Exact slab test for a line segment against an axis-aligned box."""
    t_min, t_max = 0.0, 1.0
    for axis in range(3):
        delta = end[axis] - start[axis]
        if abs(delta) < 1e-12:
            if start[axis] < box.min[axis] or start[axis] > box.max[axis]:
                return False
            continue
        inv = 1.0 / delta
        near = (box.min[axis] - start[axis]) * inv
        far = (box.max[axis] - start[axis]) * inv
        if near > far:
            near, far = far, near
        t_min = max(t_min, near)
        t_max = min(t_max, far)
        if t_min > t_max:
            return False
    return True


def trajectory_intersections(start: tuple[float, float, float], end: tuple[float, float, float], objects: Iterable[PatientObject]) -> list[dict[str, str]]:
    """Return geometry intersected by an exact segment/AABB slab test."""
    result: list[dict[str, str]] = []
    for obj in objects:
        if not obj.geometry:
            continue
        if segment_intersects_box(start, end, obj.geometry.bounding_box):
            result.append({"object_id": obj.id, "label": obj.label})
    return result
