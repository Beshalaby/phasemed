from __future__ import annotations

import math
import re

from .models import PatientModel, TemporalLink


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


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
        link_type = "same_as_prior"
        if abs(float(changes.get("volume_change_percent", 0.0))) >= 20.0 or position_distance >= 50.0:
            link_type = "changed_from"
        links.append(TemporalLink(id=f"temporal:{current_obj.id}:{prior_obj.id}", source_object_id=current_obj.id, target_object_id=prior_obj.id, type=link_type, confidence=round(score, 3), changes=changes))
    for prior_obj in prior.objects:
        if prior_obj.geometry and prior_obj.id not in used:
            links.append(TemporalLink(id=f"temporal:{prior_obj.id}:resolved", source_object_id=prior_obj.id, target_object_id=prior_obj.id, type="resolved", confidence=1.0, changes={"reason": "not matched in current study"}))
    return links


def compare_models(current: PatientModel, prior: PatientModel) -> dict:
    links = match_objects(current, prior)
    current.temporal_links = links
    links_by_object: dict[str, list[TemporalLink]] = {}
    for link in links:
        links_by_object.setdefault(link.source_object_id, []).append(link)
    for obj in current.objects:
        obj.temporal_links = links_by_object.get(obj.id, [])
    return {"current_model_id": current.id, "prior_model_id": prior.id, "links": [link.model_dump() for link in links]}
