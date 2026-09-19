"""Deterministic feature and model workflows built on top of PatientModel.

This module intentionally keeps the first model workbench dependency-light. It
turns persisted PatientModels into a stable feature table, trains auditable
classification and regression baselines with NumPy, and stores the feature
schema, validation metrics, and provenance alongside every run. Clinical
labels are always supplied by the caller; the workbench never invents them
from imaging.
"""

from __future__ import annotations

import uuid
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from .models import PatientModel


FEATURE_SCHEMA = [
    {"name": "object_count", "label": "Objects", "unit": "count"},
    {"name": "anatomy_count", "label": "Anatomy", "unit": "count"},
    {"name": "finding_count", "label": "Findings", "unit": "count"},
    {"name": "region_count", "label": "Regions", "unit": "count"},
    {"name": "relationship_count", "label": "Relationships", "unit": "count"},
    {"name": "context_binding_count", "label": "Context bindings", "unit": "count"},
    {"name": "temporal_link_count", "label": "Temporal links", "unit": "count"},
    {"name": "mesh_count", "label": "Meshes", "unit": "count"},
    {"name": "confirmed_object_count", "label": "Confirmed objects", "unit": "count"},
    {"name": "total_volume_mm3", "label": "Total object volume", "unit": "mm3"},
    {"name": "largest_object_volume_mm3", "label": "Largest object volume", "unit": "mm3"},
    {"name": "mean_temporal_volume_change_percent", "label": "Mean temporal volume change", "unit": "%"},
    {"name": "source_series_count", "label": "Source series", "unit": "count"},
    {"name": "source_instance_count", "label": "Source instances", "unit": "count"},
    {"name": "source_mean_intensity", "label": "Mean source intensity", "unit": "modality"},
    {"name": "source_min_intensity", "label": "Minimum source intensity", "unit": "modality"},
    {"name": "source_max_intensity", "label": "Maximum source intensity", "unit": "modality"},
    {"name": "source_frame_mean_variability", "label": "Frame intensity variability", "unit": "modality"},
    {"name": "finding_volume_mm3", "label": "Finding volume", "unit": "mm3"},
]
FEATURE_NAMES = [item["name"] for item in FEATURE_SCHEMA]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_features(model: PatientModel) -> dict[str, float]:
    """Create a reproducible numeric vector from one persisted PatientModel."""
    counts = Counter(item.type for item in model.objects)
    volumes = [float(item.geometry.volume_mm3) for item in model.objects if item.geometry and item.geometry.volume_mm3 is not None]
    changes = [
        float(link.changes["volume_change_percent"])
        for link in model.temporal_links
        if "volume_change_percent" in link.changes
        and isinstance(link.changes["volume_change_percent"], (int, float))
    ]
    source_objects = [item for item in model.objects if item.type == "volume"]
    pixel_stats = [stat for item in source_objects for stat in item.metadata.get("pixel_statistics", []) if isinstance(stat, dict) and "mean" in stat]
    frame_means = [float(stat["mean"]) for stat in pixel_stats]
    frame_mins = [float(stat["min"]) for stat in pixel_stats if isinstance(stat.get("min"), (int, float))]
    frame_maxes = [float(stat["max"]) for stat in pixel_stats if isinstance(stat.get("max"), (int, float))]
    finding_volumes = [float(item.geometry.volume_mm3) for item in model.objects if item.type in {"finding", "lesion"} and item.geometry and item.geometry.volume_mm3 is not None]
    values = {
        "object_count": float(len(model.objects)),
        "anatomy_count": float(counts.get("anatomy", 0)),
        "finding_count": float(counts.get("finding", 0) + counts.get("lesion", 0)),
        "region_count": float(counts.get("region", 0)),
        "relationship_count": float(len(model.relationships)),
        "context_binding_count": float(len(model.context_bindings)),
        "temporal_link_count": float(len(model.temporal_links)),
        "mesh_count": float(sum(1 for item in model.objects if item.geometry and item.geometry.mesh_id)),
        "confirmed_object_count": float(sum(1 for item in model.objects if item.review_status == "confirmed")),
        "total_volume_mm3": float(sum(volumes)),
        "largest_object_volume_mm3": float(max(volumes, default=0.0)),
        "mean_temporal_volume_change_percent": float(sum(changes) / len(changes)) if changes else 0.0,
        "source_series_count": float(model.metadata.get("source_series_count") or len(source_objects)),
        "source_instance_count": float(model.metadata.get("source_instance_count") or len(pixel_stats)),
        "source_mean_intensity": float(sum(frame_means) / len(frame_means)) if frame_means else 0.0,
        "source_min_intensity": float(min(frame_mins, default=0.0)),
        "source_max_intensity": float(max(frame_maxes, default=0.0)),
        "source_frame_mean_variability": float(np.std(frame_means)) if frame_means else 0.0,
        "finding_volume_mm3": float(sum(finding_volumes)),
    }
    return {name: round(values.get(name, 0.0), 6) for name in FEATURE_NAMES}


def dataset_rows(models: Iterable[PatientModel], labels: dict[str, Any], *, task: str = "binary") -> list[dict[str, Any]]:
    if task not in {"binary", "regression"}:
        raise ValueError("task must be binary or regression")
    rows = []
    for model in models:
        if model.id not in labels:
            continue
        label = labels[model.id]
        if task == "binary" and isinstance(label, bool):
            normalized = int(label)
        elif task == "binary" and isinstance(label, (int, float)) and float(label) in (0.0, 1.0):
            normalized = int(label)
        elif task == "regression" and isinstance(label, (int, float)) and math.isfinite(float(label)):
            normalized = round(float(label), 8)
        else:
            raise ValueError(f"Label for {model.id} must be {'binary 0 or 1' if task == 'binary' else 'a finite number'}")
        rows.append({"model_id": model.id, "patient_id": model.patient_id, "study_id": model.study_id, "label": normalized, "features": extract_features(model)})
    if not rows:
        raise ValueError("At least one labeled PatientModel is required")
    return rows


def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([[float(row["features"].get(name, 0.0)) for name in FEATURE_NAMES] for row in rows], dtype=np.float64)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _split_rows(rows: list[dict[str, Any]], *, task: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Use a stable tail holdout when the cohort is large enough to justify it."""
    if len(rows) < 5:
        return rows, None
    holdout_count = max(1, len(rows) // 5)
    training_rows = rows[:-holdout_count]
    validation_rows = rows[-holdout_count:]
    if task == "binary" and len({int(row["label"]) for row in training_rows}) < 2:
        return rows, None
    return training_rows, validation_rows


def _classification_metrics(truth: np.ndarray, predictions: np.ndarray) -> dict[str, float | int]:
    truth = truth.astype(int)
    predictions = predictions.astype(int)
    tp = int(np.sum((predictions == 1) & (truth == 1)))
    tn = int(np.sum((predictions == 0) & (truth == 0)))
    fp = int(np.sum((predictions == 1) & (truth == 0)))
    fn = int(np.sum((predictions == 0) & (truth == 1)))
    return {
        "accuracy": round(float(np.mean(predictions == truth)), 4),
        "precision": round(tp / max(1, tp + fp), 4),
        "recall": round(tp / max(1, tp + fn), 4),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
    }


def train_binary(rows: list[dict[str, Any]], *, name: str, iterations: int = 600, learning_rate: float = 0.08, l2: float = 0.001) -> dict[str, Any]:
    """Fit a small, transparent logistic model and return its complete artifact."""
    if len(rows) < 2:
        raise ValueError("At least two labeled PatientModels are required for training")
    if iterations < 20 or iterations > 5000:
        raise ValueError("iterations must be between 20 and 5000")
    if learning_rate <= 0 or learning_rate > 2:
        raise ValueError("learning_rate must be greater than 0 and no more than 2")
    if l2 < 0 or l2 > 10:
        raise ValueError("l2 must be between 0 and 10")
    fit_rows, validation_rows = _split_rows(rows, task="binary")
    x = _matrix(fit_rows)
    y = np.asarray([float(row["label"]) for row in fit_rows], dtype=np.float64)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales < 1e-9] = 1.0
    normalized = (x - means) / scales
    weights = np.zeros(normalized.shape[1], dtype=np.float64)
    bias = 0.0
    for _ in range(iterations):
        probabilities = _sigmoid(normalized @ weights + bias)
        error = probabilities - y
        weights -= learning_rate * ((normalized.T @ error) / len(fit_rows) + l2 * weights)
        bias -= learning_rate * float(error.mean())
    probabilities = _sigmoid(normalized @ weights + bias)
    predictions = (probabilities >= 0.5).astype(int)
    truth = y.astype(int)
    metrics = _classification_metrics(truth, predictions)
    validation_metrics = None
    if validation_rows:
        validation_matrix = _matrix(validation_rows)
        validation_truth = np.asarray([float(row["label"]) for row in validation_rows], dtype=np.float64)
        validation_probabilities = _sigmoid(((validation_matrix - means) / scales) @ weights + bias)
        validation_metrics = _classification_metrics(validation_truth, (validation_probabilities >= 0.5).astype(int))
    return {
        "id": f"algorithm-{uuid.uuid4().hex[:12]}",
        "name": name.strip() or "Phasemed binary model",
        "type": "binary-logistic-regression",
        "status": "ready",
        "created_at": _now(),
        "feature_schema": FEATURE_SCHEMA,
        "parameters": {"iterations": iterations, "learning_rate": learning_rate, "l2": l2},
        "normalization": {"means": means.round(8).tolist(), "scales": scales.round(8).tolist()},
        "weights": weights.round(8).tolist(),
        "bias": round(float(bias), 8),
        "labels": {"negative": 0, "positive": 1},
        "training": {"row_count": len(fit_rows), "model_ids": [row["model_id"] for row in fit_rows], "metrics": metrics, **({"validation": {"row_count": len(validation_rows), "model_ids": [row["model_id"] for row in validation_rows], "metrics": validation_metrics}} if validation_metrics else {})},
        "provenance": {"method": "patient-model-feature-vector", "feature_engine": "phasemed-model-lab-0.1", "clinical_label_source": "caller-supplied"},
    }


def _regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residuals = predicted - actual
    mse = float(np.mean(residuals ** 2))
    variance = float(np.sum((actual - actual.mean()) ** 2))
    return {"mse": round(mse, 6), "rmse": round(float(np.sqrt(mse)), 6), "mae": round(float(np.mean(np.abs(residuals))), 6), "r2": round(1.0 - float(np.sum(residuals ** 2)) / variance, 6) if variance > 1e-12 else 0.0}


def train_regression(rows: list[dict[str, Any]], *, name: str, iterations: int = 600, learning_rate: float = 0.03, l2: float = 0.001) -> dict[str, Any]:
    """Fit an auditable linear regression for continuous outcomes."""
    if len(rows) < 2:
        raise ValueError("At least two labeled PatientModels are required for training")
    if iterations < 20 or iterations > 5000:
        raise ValueError("iterations must be between 20 and 5000")
    if learning_rate <= 0 or learning_rate > 2:
        raise ValueError("learning_rate must be greater than 0 and no more than 2")
    if l2 < 0 or l2 > 10:
        raise ValueError("l2 must be between 0 and 10")
    fit_rows, validation_rows = _split_rows(rows, task="regression")
    x = _matrix(fit_rows)
    y = np.asarray([float(row["label"]) for row in fit_rows], dtype=np.float64)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales < 1e-9] = 1.0
    normalized = (x - means) / scales
    weights = np.zeros(normalized.shape[1], dtype=np.float64)
    bias = float(y.mean())
    for _ in range(iterations):
        predicted = normalized @ weights + bias
        error = predicted - y
        weights -= learning_rate * ((normalized.T @ error) / len(fit_rows) + l2 * weights)
        bias -= learning_rate * float(error.mean())
    predicted = normalized @ weights + bias
    validation_metrics = None
    if validation_rows:
        validation_matrix = _matrix(validation_rows)
        validation_truth = np.asarray([float(row["label"]) for row in validation_rows], dtype=np.float64)
        validation_predicted = ((validation_matrix - means) / scales) @ weights + bias
        validation_metrics = _regression_metrics(validation_truth, validation_predicted)
    return {
        "id": f"algorithm-{uuid.uuid4().hex[:12]}",
        "name": name.strip() or "Phasemed linear model",
        "type": "linear-regression",
        "status": "ready",
        "created_at": _now(),
        "feature_schema": FEATURE_SCHEMA,
        "parameters": {"iterations": iterations, "learning_rate": learning_rate, "l2": l2},
        "normalization": {"means": means.round(8).tolist(), "scales": scales.round(8).tolist()},
        "weights": weights.round(8).tolist(),
        "bias": round(float(bias), 8),
        "training": {"row_count": len(fit_rows), "model_ids": [row["model_id"] for row in fit_rows], "metrics": _regression_metrics(y, predicted), **({"validation": {"row_count": len(validation_rows), "model_ids": [row["model_id"] for row in validation_rows], "metrics": validation_metrics}} if validation_metrics else {})},
        "provenance": {"method": "patient-model-feature-vector", "feature_engine": "phasemed-model-lab-0.2", "clinical_label_source": "caller-supplied"},
    }


def predict(artifact: dict[str, Any], model: PatientModel) -> dict[str, Any]:
    features = extract_features(model)
    vector = np.asarray([features[name] for name in FEATURE_NAMES], dtype=np.float64)
    means = np.asarray(artifact["normalization"]["means"], dtype=np.float64)
    scales = np.asarray(artifact["normalization"]["scales"], dtype=np.float64)
    weights = np.asarray(artifact["weights"], dtype=np.float64)
    contributions = {name: round(float(((vector[i] - means[i]) / scales[i]) * weights[i]), 6) for i, name in enumerate(FEATURE_NAMES)}
    raw = float(((vector - means) / scales) @ weights + float(artifact["bias"]))
    if artifact.get("type") == "linear-regression":
        return {"model_id": model.id, "prediction": round(raw, 6), "features": features, "contributions": contributions, "provenance": {"algorithm_id": artifact["id"], "algorithm_type": artifact["type"], "feature_engine": "phasemed-model-lab-0.2"}}
    probability = float(_sigmoid(np.asarray([raw]))[0])
    return {
        "model_id": model.id,
        "prediction": 1 if probability >= 0.5 else 0,
        "probability_positive": round(probability, 6),
        "features": features,
        "contributions": contributions,
        "provenance": {"algorithm_id": artifact["id"], "algorithm_type": artifact["type"], "feature_engine": "phasemed-model-lab-0.1"},
    }
