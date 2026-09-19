from __future__ import annotations

import math
import re
from itertools import product
from typing import Any

from .models import PatientModel, TemporalLink


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _max_extent_mm(geometry) -> float:
    return max(abs(geometry.bounding_box.max[index] - geometry.bounding_box.min[index]) for index in range(3))


def _rigid_transform_point(point: tuple[float, float, float], parameters: list[float], fixed_parameters: list[float]) -> tuple[float, float, float]:
    """Apply a SimpleITK-compatible Euler3D transform without clinical extras.

    SimpleITK stores fixed parameters as the rotation center followed by a
    compute-ZYX flag. Keeping this small rigid operation dependency-free means
    persisted registration results remain usable in the base runtime, where
    SimpleITK is intentionally optional.
    """
    if len(parameters) != 6 or len(fixed_parameters) < 4:
        raise ValueError("Euler3D registration payload must contain six parameters and four fixed parameters")
    angle_x, angle_y, angle_z, tx, ty, tz = (float(value) for value in parameters)
    cx, cy, cz = (float(value) for value in fixed_parameters[:3])
    compute_zyx = bool(float(fixed_parameters[3]))
    sx, sy, sz = math.sin(angle_x), math.sin(angle_y), math.sin(angle_z)
    cos_x, cos_y, cos_z = math.cos(angle_x), math.cos(angle_y), math.cos(angle_z)
    rx = ((1.0, 0.0, 0.0), (0.0, cos_x, -sx), (0.0, sx, cos_x))
    ry = ((cos_y, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cos_y))
    rz = ((cos_z, -sz, 0.0), (sz, cos_z, 0.0), (0.0, 0.0, 1.0))

    def multiply(left: tuple[tuple[float, ...], ...], right: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(sum(left[row][inner] * right[inner][column] for inner in range(3)) for column in range(3)) for row in range(3))

    rotation = multiply(rz, multiply(ry, rx) if compute_zyx else multiply(rx, ry))
    relative = (float(point[0]) - cx, float(point[1]) - cy, float(point[2]) - cz)
    rotated = tuple(sum(rotation[row][column] * relative[column] for column in range(3)) for row in range(3))
    return (rotated[0] + cx + tx, rotated[1] + cy + ty, rotated[2] + cz + tz)


def _registered_prior_model(prior: PatientModel, registration: dict[str, Any] | None) -> PatientModel:
    """Return a comparison-only copy of the prior model in current-study space.

    The persisted prior model remains unchanged. Deformable results are left
    untouched until their displacement field is persisted by the adapter.
    """
    if not registration or registration.get("method") != "SimpleITK-Euler3D":
        return prior
    parameters = registration.get("transform_parameters")
    fixed_parameters = registration.get("transform_fixed_parameters")
    if not isinstance(parameters, list) or not isinstance(fixed_parameters, list):
        return prior
    transformed = prior.model_copy(deep=True)
    for obj in transformed.objects:
        geometry = obj.geometry
        if not geometry:
            continue
        corners = product(*[(geometry.bounding_box.min[index], geometry.bounding_box.max[index]) for index in range(3)])
        transformed_points = [_rigid_transform_point(tuple(point), parameters, fixed_parameters) for point in corners]
        geometry.centroid = _rigid_transform_point(geometry.centroid, parameters, fixed_parameters)
        geometry.bounding_box.min = tuple(min(point[index] for point in transformed_points) for index in range(3))
        geometry.bounding_box.max = tuple(max(point[index] for point in transformed_points) for index in range(3))
    return transformed


def match_objects(current: PatientModel, prior: PatientModel) -> list[TemporalLink]:
    links: list[TemporalLink] = []
    used: set[str] = set()
    for current_obj in current.objects:
        if not current_obj.geometry:
            continue
        candidates = []
        for prior_obj in prior.objects:
            if prior_obj.id in used or not prior_obj.geometry:
                continue
            label_score = 1.0 if _normalized_label(current_obj.label) == _normalized_label(prior_obj.label) else 0.0
            a = current_obj.geometry.centroid; b = prior_obj.geometry.centroid
            position_distance = math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))
            distance_score = max(0.0, 1.0 - position_distance / 1000.0)
            score = label_score * 0.8 + distance_score * 0.2
            candidates.append((score, prior_obj, position_distance))
        if not candidates:
            links.append(TemporalLink(id=f"temporal:{current_obj.id}:new", source_object_id=current_obj.id, target_object_id=current_obj.id, type="new", confidence=1.0))
            continue
        score, prior_obj, position_distance = max(candidates, key=lambda value: value[0])
        if score < 0.35:
            links.append(TemporalLink(id=f"temporal:{current_obj.id}:new", source_object_id=current_obj.id, target_object_id=current_obj.id, type="new", confidence=round(score, 3)))
            continue
        used.add(prior_obj.id)
        changes: dict[str, float | str] = {"centroid_distance_mm": round(position_distance, 3)}
        current_volume = current_obj.geometry.volume_mm3
        prior_volume = prior_obj.geometry.volume_mm3
        if current_volume is not None and prior_volume:
            changes["volume_delta_mm3"] = round(current_volume - prior_volume, 3)
            changes["volume_change_percent"] = round((current_volume - prior_volume) / prior_volume * 100, 2)
        current_diameter = _max_extent_mm(current_obj.geometry)
        prior_diameter = _max_extent_mm(prior_obj.geometry)
        changes["diameter_delta_mm"] = round(current_diameter - prior_diameter, 3)
        if prior_diameter:
            changes["diameter_change_percent"] = round((current_diameter - prior_diameter) / prior_diameter * 100, 2)
        current_surface = current_obj.geometry.surface_area_mm2
        prior_surface = prior_obj.geometry.surface_area_mm2
        if current_surface is not None and prior_surface is not None:
            changes["surface_area_delta_mm2"] = round(current_surface - prior_surface, 3)
            if prior_surface:
                changes["surface_area_change_percent"] = round((current_surface - prior_surface) / prior_surface * 100, 2)
        link_type = "same_as_prior"
        if abs(float(changes.get("volume_change_percent", 0.0))) >= 20.0 or position_distance >= 50.0:
            link_type = "changed_from"
        links.append(TemporalLink(id=f"temporal:{current_obj.id}:{prior_obj.id}", source_object_id=current_obj.id, target_object_id=prior_obj.id, type=link_type, confidence=round(score, 3), changes=changes))
    for prior_obj in prior.objects:
        if prior_obj.geometry and prior_obj.id not in used:
            links.append(TemporalLink(id=f"temporal:{prior_obj.id}:resolved", source_object_id=prior_obj.id, target_object_id=prior_obj.id, type="resolved", confidence=1.0, changes={"reason": "not matched in current study"}))
    return links


def compare_models(current: PatientModel, prior: PatientModel, registration: dict[str, Any] | None = None) -> dict:
    links = match_objects(current, _registered_prior_model(prior, registration))
    current.temporal_links = links
    links_by_object: dict[str, list[TemporalLink]] = {}
    for link in links:
        links_by_object.setdefault(link.source_object_id, []).append(link)
    for obj in current.objects:
        obj.temporal_links = links_by_object.get(obj.id, [])
    return {"current_model_id": current.id, "prior_model_id": prior.id, "links": [link.model_dump() for link in links]}
