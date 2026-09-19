from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


Vec3 = tuple[float, float, float]
ObjectType = Literal["anatomy", "finding", "lesion", "device", "region", "volume"]
ReviewStatus = Literal["unreviewed", "confirmed", "modified", "rejected"]


class BoundingBox(BaseModel):
    min: Vec3
    max: Vec3


class Geometry(BaseModel):
    centroid: Vec3
    bounding_box: BoundingBox
    volume_mm3: float | None = None
    surface_area_mm2: float | None = None
    mesh_id: str | None = None
    segmentation_id: str | None = None
    source_frame: str | None = None
    coordinate_system: str = "patient"
    voxel_count: int | None = None


class Observation(BaseModel):
    id: str
    label: str
    value: float | str | None = None
    unit: str | None = None
    source_id: str | None = None
    observed_at: str | None = None
    confidence: float | None = None


class SourceReference(BaseModel):
    id: str
    type: str
    title: str
    date: str | None = None
    excerpt: str | None = None
    uri: str | None = None


class ContextBinding(BaseModel):
    id: str
    context_item_id: str
    target_type: Literal["patient", "study", "object", "measurement", "timepoint"]
    target_id: str | None = None
    relevance: float = Field(ge=0, le=1)
    method: Literal["structured", "text-extraction", "semantic", "manual"]
    source: SourceReference
    evidence: str | None = None
    algorithm_version: str = "context-engine-0.1"
    review_status: ReviewStatus = "unreviewed"
    created_at: str = Field(default_factory=now_iso)


class Relationship(BaseModel):
    id: str
    source_object_id: str
    target_object_id: str
    type: str
    value: float | None = None
    unit: str | None = None
    method: str
    provenance: list[SourceReference] = Field(default_factory=list)


class TemporalLink(BaseModel):
    id: str
    source_object_id: str
    target_object_id: str
    type: Literal["same_as_prior", "changed_from", "new", "resolved"]
    confidence: float = Field(ge=0, le=1)
    changes: dict[str, float | str] = Field(default_factory=dict)


class PatientObject(BaseModel):
    id: str
    type: ObjectType
    label: str
    terminology: dict[str, str] | None = None
    geometry: Geometry | None = None
    observations: list[Observation] = Field(default_factory=list)
    context: list[ContextBinding] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    temporal_links: list[TemporalLink] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    review_status: ReviewStatus = "unreviewed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineEntry(BaseModel):
    id: str
    study_id: str
    date: str
    label: str
    model_id: str | None = None


class PatientModel(BaseModel):
    id: str
    patient_id: str
    study_id: str
    version: int = 1
    objects: list[PatientObject] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    temporal_links: list[TemporalLink] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    context_bindings: list[ContextBinding] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    capabilities: dict[str, str] = Field(default_factory=dict)
    context_items: list[dict[str, Any]] = Field(default_factory=list)
    procedure_paths: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    pipeline_version: str = "local-compiler-0.1"


class StudySummary(BaseModel):
    id: str
    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str
    study_date: str | None = None
    description: str | None = None
    modality: str | None = None
    series_count: int = 0
    image_count: int = 0
    status: Literal["imported", "compiling", "ready", "partial", "failed"] = "imported"
    model_id: str | None = None
    created_at: str = Field(default_factory=now_iso)


class JobState(BaseModel):
    id: str
    study_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"] = "queued"
    stage: str = "Queued"
    progress: int = 0
    message: str | None = None
    error: str | None = None
    updated_at: str = Field(default_factory=now_iso)


class SpatialQuery(BaseModel):
    operation: Literal["distance", "minimum_surface_distance", "nearest", "within_radius", "contains", "intersects", "adjacent", "intersections", "trajectory", "volume", "surface_area"]
    object_id: str | None = None
    target_id: str | None = None
    radius_mm: float | None = None
    start: Vec3 | None = None
    end: Vec3 | None = None
