"""Deterministic feature and model workflows built on top of PatientModel.

This module intentionally keeps the model workbench dependency-light. It turns
persisted PatientModels into a stable feature table, trains auditable linear
baselines or deterministic bootstrap forests with NumPy, and stores the
feature schema, validation metrics, feature importance, and provenance
alongside every run. Clinical labels are always supplied by the caller; the
workbench never invents them from imaging.
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


def summarize_dataset(dataset: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic cohort quality and optional feature-drift statistics."""
    rows = list(dataset.get("rows") or [])
    matrix = _matrix(rows) if rows else np.empty((0, len(FEATURE_NAMES)), dtype=np.float64)
    feature_stats = {}
    for index, name in enumerate(FEATURE_NAMES):
        values = matrix[:, index] if len(matrix) else np.asarray([], dtype=np.float64)
        feature_stats[name] = {
            "mean": round(float(np.mean(values)) if len(values) else 0.0, 6),
            "std": round(float(np.std(values)) if len(values) else 0.0, 6),
            "min": round(float(np.min(values)) if len(values) else 0.0, 6),
            "max": round(float(np.max(values)) if len(values) else 0.0, 6),
            "zero_count": int(np.sum(np.isclose(values, 0.0))) if len(values) else 0,
        }
    labels = [row.get("label") for row in rows]
    label_summary: dict[str, Any]
    if dataset.get("task") == "binary":
        counts = Counter(str(int(label)) for label in labels)
        label_summary = {"task": "binary", "counts": {key: counts.get(key, 0) for key in ("0", "1")}, "positive_rate": round(sum(int(label) for label in labels) / max(1, len(labels)), 6)}
    else:
        numeric = np.asarray([float(label) for label in labels], dtype=np.float64)
        label_summary = {"task": "regression", "mean": round(float(np.mean(numeric)) if len(numeric) else 0.0, 6), "std": round(float(np.std(numeric)) if len(numeric) else 0.0, 6), "min": round(float(np.min(numeric)) if len(numeric) else 0.0, 6), "max": round(float(np.max(numeric)) if len(numeric) else 0.0, 6)}
    result: dict[str, Any] = {
        "dataset_id": dataset.get("id"),
        "task": dataset.get("task"),
        "row_count": len(rows),
        "feature_count": len(FEATURE_NAMES),
        "label_summary": label_summary,
        "features": feature_stats,
        "provenance": {"method": "deterministic cohort quality summary", "feature_schema": FEATURE_NAMES},
    }
    if baseline:
        baseline_rows = list(baseline.get("rows") or [])
        baseline_matrix = _matrix(baseline_rows) if baseline_rows else np.empty((0, len(FEATURE_NAMES)), dtype=np.float64)
        drift = {}
        for index, name in enumerate(FEATURE_NAMES):
            current_values = matrix[:, index] if len(matrix) else np.asarray([], dtype=np.float64)
            baseline_values = baseline_matrix[:, index] if len(baseline_matrix) else np.asarray([], dtype=np.float64)
            current_mean = float(np.mean(current_values)) if len(current_values) else 0.0
            baseline_mean = float(np.mean(baseline_values)) if len(baseline_values) else 0.0
            pooled_std = float(np.sqrt((np.var(current_values) + np.var(baseline_values)) / 2.0)) if len(current_values) and len(baseline_values) else 0.0
            standardized_difference = (current_mean - baseline_mean) / pooled_std if pooled_std > 1e-12 else (0.0 if abs(current_mean - baseline_mean) <= 1e-12 else 1.0)
            drift[name] = {"baseline_mean": round(baseline_mean, 6), "current_mean": round(current_mean, 6), "standardized_mean_difference": round(float(standardized_difference), 6), "flagged": abs(standardized_difference) >= 0.5}
        result["baseline_dataset_id"] = baseline.get("id")
        result["drift"] = drift
        result["provenance"]["drift_method"] = "absolute standardized mean difference; flagged at |SMD| >= 0.5"
    return result


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


def _tree_impurity(values: np.ndarray, task: str) -> float:
    if not len(values):
        return 0.0
    if task == "binary":
        positive = float(np.mean(values))
        return 2.0 * positive * (1.0 - positive)
    mean = float(np.mean(values))
    return float(np.mean((values - mean) ** 2))


def _leaf_node(values: np.ndarray, task: str) -> dict[str, Any]:
    if task == "binary":
        positive = int(np.sum(values >= 0.5))
        negative = int(len(values) - positive)
        return {
            "kind": "leaf",
            "probability": round(positive / max(1, len(values)), 8),
            "class_counts": [negative, positive],
            "samples": int(len(values)),
        }
    return {"kind": "leaf", "value": round(float(np.mean(values)), 8), "samples": int(len(values))}


def _best_tree_split(
    matrix: np.ndarray,
    values: np.ndarray,
    feature_indices: np.ndarray,
    task: str,
    min_samples_leaf: int,
) -> dict[str, Any] | None:
    parent_impurity = _tree_impurity(values, task)
    if parent_impurity <= 1e-12:
        return None
    best: dict[str, Any] | None = None
    for feature_index in feature_indices:
        column = matrix[:, int(feature_index)]
        unique = np.unique(column)
        if len(unique) < 2:
            continue
        if len(unique) > 65:
            positions = np.unique(np.linspace(1, len(unique) - 1, 64, dtype=int))
            thresholds = (unique[positions - 1] + unique[positions]) / 2.0
        else:
            thresholds = (unique[:-1] + unique[1:]) / 2.0
        for threshold in thresholds:
            left_mask = column <= threshold
            left_count = int(np.sum(left_mask))
            right_count = len(values) - left_count
            if left_count < min_samples_leaf or right_count < min_samples_leaf:
                continue
            left_values = values[left_mask]
            right_values = values[~left_mask]
            weighted_impurity = (
                left_count * _tree_impurity(left_values, task)
                + right_count * _tree_impurity(right_values, task)
            ) / len(values)
            gain = parent_impurity - weighted_impurity
            if best is None or gain > float(best["gain"]) + 1e-12:
                best = {
                    "feature": int(feature_index),
                    "threshold": float(threshold),
                    "gain": float(gain),
                    "left_mask": left_mask,
                }
    return best


def _build_tree(
    matrix: np.ndarray,
    values: np.ndarray,
    *,
    task: str,
    depth: int,
    max_depth: int,
    min_samples_leaf: int,
    max_features: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    if (
        depth >= max_depth
        or len(values) < 2 * min_samples_leaf
        or (task == "binary" and len(np.unique(values)) < 2)
        or (task == "regression" and float(np.ptp(values)) <= 1e-12)
    ):
        return _leaf_node(values, task)
    feature_indices = np.sort(rng.choice(matrix.shape[1], size=min(max_features, matrix.shape[1]), replace=False))
    split = _best_tree_split(matrix, values, feature_indices, task, min_samples_leaf)
    if split is None or float(split["gain"]) <= 1e-12:
        return _leaf_node(values, task)
    left_mask = split.pop("left_mask")
    return {
        "kind": "split",
        "feature": int(split["feature"]),
        "threshold": round(float(split["threshold"]), 8),
        "gain": round(float(split["gain"]), 8),
        "samples": int(len(values)),
        "left": _build_tree(
            matrix[left_mask], values[left_mask], task=task, depth=depth + 1,
            max_depth=max_depth, min_samples_leaf=min_samples_leaf,
            max_features=max_features, rng=rng,
        ),
        "right": _build_tree(
            matrix[~left_mask], values[~left_mask], task=task, depth=depth + 1,
            max_depth=max_depth, min_samples_leaf=min_samples_leaf,
            max_features=max_features, rng=rng,
        ),
    }


def _predict_tree(node: dict[str, Any], vector: np.ndarray) -> float:
    current = node
    while current.get("kind") == "split":
        current = current["left"] if vector[int(current["feature"])] <= float(current["threshold"]) else current["right"]
    return float(current.get("probability", current.get("value", 0.0)))


def _tree_importance(node: dict[str, Any], importance: np.ndarray) -> None:
    if node.get("kind") != "split":
        return
    feature = int(node["feature"])
    importance[feature] += float(node.get("gain", 0.0)) * float(node.get("samples", 1))
    _tree_importance(node["left"], importance)
    _tree_importance(node["right"], importance)


def _forest_predictions(trees: list[dict[str, Any]], matrix: np.ndarray) -> np.ndarray:
    return np.asarray([[_predict_tree(tree, vector) for tree in trees] for vector in matrix], dtype=np.float64).mean(axis=1)


def _artifact_predictions(artifact: dict[str, Any], matrix: np.ndarray) -> np.ndarray:
    """Predict directly from feature rows for validation without loading a PatientModel."""
    if artifact.get("type") in {"random-forest-classifier", "random-forest-regressor"}:
        return _forest_predictions(artifact.get("trees") or [], matrix)
    means = np.asarray(artifact["normalization"]["means"], dtype=np.float64)
    scales = np.asarray(artifact["normalization"]["scales"], dtype=np.float64)
    weights = np.asarray(artifact["weights"], dtype=np.float64)
    raw = ((matrix - means) / scales) @ weights + float(artifact["bias"])
    return _sigmoid(raw) if artifact.get("type") == "binary-logistic-regression" else raw


def cross_validate(
    rows: list[dict[str, Any]],
    *,
    task: str,
    algorithm: str,
    name: str,
    folds: int = 5,
    **parameters: Any,
) -> dict[str, Any]:
    """Run deterministic, label-aware k-fold validation over a cohort."""
    if task not in {"binary", "regression"}:
        raise ValueError("task must be binary or regression")
    if len(rows) < 4:
        raise ValueError("At least four labeled PatientModels are required for cross-validation")
    if folds < 2 or folds > 10:
        raise ValueError("folds must be between 2 and 10")
    if task == "binary":
        class_counts = Counter(int(row["label"]) for row in rows)
        if set(class_counts) != {0, 1}:
            raise ValueError("Binary cross-validation requires both classes")
        folds = min(int(folds), min(class_counts.values()))
    else:
        folds = min(int(folds), len(rows))
    assignments: list[list[dict[str, Any]]] = [[] for _ in range(folds)]
    if task == "binary":
        per_class_index: Counter[int] = Counter()
        for row in rows:
            label = int(row["label"])
            assignments[per_class_index[label] % folds].append(row)
            per_class_index[label] += 1
    else:
        for index, row in enumerate(rows):
            assignments[index % folds].append(row)
    fold_results = []
    for fold_index, validation_rows in enumerate(assignments):
        validation_ids = {row["model_id"] for row in validation_rows}
        training_rows = [row for row in rows if row["model_id"] not in validation_ids]
        artifact = train_algorithm(rows=training_rows, task=task, algorithm=algorithm, name=name, **parameters)
        matrix = _matrix(validation_rows)
        truth = np.asarray([float(row["label"]) for row in validation_rows], dtype=np.float64)
        raw_predictions = _artifact_predictions(artifact, matrix)
        metrics = _classification_metrics(truth, (raw_predictions >= 0.5).astype(int)) if task == "binary" else _regression_metrics(truth, raw_predictions)
        fold_results.append({"fold": fold_index + 1, "model_ids": [row["model_id"] for row in validation_rows], "metrics": metrics})
    metric_names = [key for key, value in fold_results[0]["metrics"].items() if isinstance(value, (int, float))]
    aggregate = {key: round(float(np.mean([fold["metrics"][key] for fold in fold_results])), 6) for key in metric_names}
    return {"task": task, "algorithm": algorithm, "fold_count": len(fold_results), "folds": fold_results, "metrics": aggregate, "parameters": parameters, "provenance": {"method": "deterministic-stratified-k-fold", "label_source": "caller-supplied", "row_count": len(rows)}}


def analyze_cohort(rows: list[dict[str, Any]], *, clusters: int = 3, seed: int = 17) -> dict[str, Any]:
    """Project, group, and rank a cohort without requiring clinical labels."""
    if len(rows) < 2:
        raise ValueError("At least two PatientModels are required for cohort analysis")
    if clusters < 2 or clusters > 10:
        raise ValueError("clusters must be between 2 and 10")
    matrix = _matrix(rows)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-9] = 1.0
    normalized = (matrix - means) / scales
    _, singular_values, components = np.linalg.svd(normalized, full_matrices=False)
    component_count = min(2, components.shape[0])
    projection = normalized @ components[:component_count].T
    if component_count == 1:
        projection = np.column_stack([projection[:, 0], np.zeros(len(rows))])
    cluster_count = min(int(clusters), len(rows))
    rng = np.random.default_rng(int(seed))
    centers = normalized[rng.choice(len(rows), size=cluster_count, replace=False)].copy()
    assignments = np.zeros(len(rows), dtype=int)
    for _ in range(60):
        distances = ((normalized[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        next_assignments = np.argmin(distances, axis=1)
        next_centers = centers.copy()
        for index in range(cluster_count):
            members = normalized[next_assignments == index]
            if len(members):
                next_centers[index] = members.mean(axis=0)
        if np.array_equal(assignments, next_assignments):
            centers = next_centers
            assignments = next_assignments
            break
        centers = next_centers
        assignments = next_assignments
    distances = np.sqrt(((normalized - centers[assignments]) ** 2).sum(axis=1))
    max_distance = float(distances.max()) if len(distances) else 1.0
    anomaly_scores = distances / max(max_distance, 1e-9)
    explained = (singular_values[:component_count] ** 2) / max(float((singular_values ** 2).sum()), 1e-9)
    result_rows = [{"model_id": row["model_id"], "cluster": int(assignments[index]), "anomaly_score": round(float(anomaly_scores[index]), 6), "projection": [round(float(value), 6) for value in projection[index]]} for index, row in enumerate(rows)]
    summaries = []
    for index in range(cluster_count):
        members = [item for item in result_rows if item["cluster"] == index]
        summaries.append({"cluster": index, "row_count": len(members), "model_ids": [item["model_id"] for item in members], "mean_anomaly_score": round(float(np.mean([item["anomaly_score"] for item in members])), 6) if members else 0.0})
    return {"id": f"cohort-analysis-{uuid.uuid4().hex[:12]}", "status": "ready", "created_at": _now(), "row_count": len(rows), "parameters": {"clusters": cluster_count, "seed": int(seed)}, "projection": {"method": "standardized-pca", "components": component_count, "explained_variance_ratio": [round(float(value), 6) for value in explained]}, "clusters": summaries, "rows": result_rows, "provenance": {"method": "patient-model-feature-vector", "feature_engine": "phasemed-model-lab-0.3", "label_source": "none", "deterministic_seed": int(seed)}}


def train_forest(
    rows: list[dict[str, Any]],
    *,
    name: str,
    task: str = "binary",
    n_estimators: int = 32,
    max_depth: int = 6,
    min_samples_leaf: int = 1,
    max_features: str | int = "sqrt",
    seed: int = 17,
) -> dict[str, Any]:
    """Fit a deterministic bootstrap forest with serializable, inspectable trees."""
    if task not in {"binary", "regression"}:
        raise ValueError("task must be binary or regression")
    if len(rows) < 2:
        raise ValueError("At least two labeled PatientModels are required for training")
    if n_estimators < 1 or n_estimators > 200:
        raise ValueError("n_estimators must be between 1 and 200")
    if max_depth < 1 or max_depth > 20:
        raise ValueError("max_depth must be between 1 and 20")
    if min_samples_leaf < 1 or min_samples_leaf > 1000:
        raise ValueError("min_samples_leaf must be between 1 and 1000")
    if isinstance(max_features, str):
        if max_features == "sqrt":
            feature_count = max(1, int(math.sqrt(len(FEATURE_NAMES))))
        elif max_features == "all":
            feature_count = len(FEATURE_NAMES)
        else:
            raise ValueError("max_features must be sqrt, all, or an integer")
    else:
        feature_count = int(max_features)
        if feature_count < 1:
            raise ValueError("max_features must be at least 1")
    fit_rows, validation_rows = _split_rows(rows, task=task)
    matrix = _matrix(fit_rows)
    values = np.asarray([float(row["label"]) for row in fit_rows], dtype=np.float64)
    rng = np.random.default_rng(int(seed))
    trees = []
    for _ in range(int(n_estimators)):
        bootstrap = rng.integers(0, len(fit_rows), size=len(fit_rows))
        tree_rng = np.random.default_rng(int(rng.integers(0, 2**32 - 1)))
        trees.append(_build_tree(matrix[bootstrap], values[bootstrap], task=task, depth=0, max_depth=int(max_depth), min_samples_leaf=int(min_samples_leaf), max_features=feature_count, rng=tree_rng))
    fit_predictions = _forest_predictions(trees, matrix)
    if task == "binary":
        fit_metrics = _classification_metrics(values, (fit_predictions >= 0.5).astype(int))
    else:
        fit_metrics = _regression_metrics(values, fit_predictions)
    validation_metrics = None
    if validation_rows:
        validation_matrix = _matrix(validation_rows)
        validation_values = np.asarray([float(row["label"]) for row in validation_rows], dtype=np.float64)
        validation_predictions = _forest_predictions(trees, validation_matrix)
        validation_metrics = (_classification_metrics(validation_values, (validation_predictions >= 0.5).astype(int)) if task == "binary" else _regression_metrics(validation_values, validation_predictions))
    importances = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
    for tree in trees:
        _tree_importance(tree, importances)
    if float(importances.sum()) > 0:
        importances /= float(importances.sum())
    algorithm_type = "random-forest-classifier" if task == "binary" else "random-forest-regressor"
    return {
        "id": f"algorithm-{uuid.uuid4().hex[:12]}",
        "name": name.strip() or ("Phasmed random forest classifier" if task == "binary" else "Phasmed random forest regressor"),
        "type": algorithm_type,
        "status": "ready",
        "created_at": _now(),
        "feature_schema": FEATURE_SCHEMA,
        "parameters": {"n_estimators": int(n_estimators), "max_depth": int(max_depth), "min_samples_leaf": int(min_samples_leaf), "max_features": max_features, "seed": int(seed)},
        "trees": trees,
        "feature_importance": {name: round(float(importances[index]), 8) for index, name in enumerate(FEATURE_NAMES)},
        "feature_baseline": {"means": matrix.mean(axis=0).round(8).tolist(), "scales": np.where(matrix.std(axis=0) < 1e-9, 1.0, matrix.std(axis=0)).round(8).tolist()},
        "labels": {"negative": 0, "positive": 1} if task == "binary" else None,
        "training": {"row_count": len(fit_rows), "model_ids": [row["model_id"] for row in fit_rows], "metrics": fit_metrics, "baseline": round(float(np.mean(values)), 8), **({"validation": {"row_count": len(validation_rows), "model_ids": [row["model_id"] for row in validation_rows], "metrics": validation_metrics}} if validation_metrics else {})},
        "provenance": {"method": "patient-model-feature-vector", "feature_engine": "phasemed-model-lab-forest-0.1", "clinical_label_source": "caller-supplied", "deterministic_seed": int(seed)},
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
        "name": name.strip() or "Phasmed binary model",
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
        "name": name.strip() or "Phasmed linear model",
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


def train_algorithm(rows: list[dict[str, Any]], *, task: str, algorithm: str, name: str, **parameters: Any) -> dict[str, Any]:
    """Dispatch a validated task/algorithm pair for API and search callers."""
    if task == "binary" and algorithm == "binary-logistic-regression":
        return train_binary(
            rows,
            name=name,
            iterations=int(parameters.get("iterations", 600)),
            learning_rate=float(parameters.get("learning_rate", 0.08)),
            l2=float(parameters.get("l2", 0.001)),
        )
    if task == "regression" and algorithm == "linear-regression":
        return train_regression(
            rows,
            name=name,
            iterations=int(parameters.get("iterations", 600)),
            learning_rate=float(parameters.get("learning_rate", 0.03)),
            l2=float(parameters.get("l2", 0.001)),
        )
    if task in {"binary", "regression"} and algorithm == "random-forest":
        return train_forest(
            rows,
            name=name,
            task=task,
            n_estimators=int(parameters.get("n_estimators", 32)),
            max_depth=int(parameters.get("max_depth", 6)),
            min_samples_leaf=int(parameters.get("min_samples_leaf", 1)),
            max_features=parameters.get("max_features", "sqrt"),
            seed=int(parameters.get("seed", 17)),
        )
    raise ValueError(f"Unsupported {task} algorithm: {algorithm}")


def predict(artifact: dict[str, Any], model: PatientModel) -> dict[str, Any]:
    features = extract_features(model)
    vector = np.asarray([features[name] for name in FEATURE_NAMES], dtype=np.float64)
    if artifact.get("type") in {"random-forest-classifier", "random-forest-regressor"}:
        trees = artifact.get("trees") or []
        if not trees:
            raise ValueError("Forest artifact contains no trees")
        tree_predictions = np.asarray([_predict_tree(tree, vector) for tree in trees], dtype=np.float64)
        raw = float(tree_predictions.mean())
        importance = artifact.get("feature_importance") or {}
        means = np.asarray((artifact.get("feature_baseline") or {}).get("means", [0.0] * len(FEATURE_NAMES)), dtype=np.float64)
        scales = np.asarray((artifact.get("feature_baseline") or {}).get("scales", [1.0] * len(FEATURE_NAMES)), dtype=np.float64)
        baseline = float((artifact.get("training") or {}).get("baseline", raw))
        normalized = (vector - means) / np.where(scales < 1e-9, 1.0, scales)
        contributions = {name: round(float(float(importance.get(name, 0.0)) * np.tanh(normalized[index]) * (raw - baseline)), 6) for index, name in enumerate(FEATURE_NAMES)}
        if artifact.get("type") == "random-forest-regressor":
            return {"model_id": model.id, "prediction": round(raw, 6), "features": features, "contributions": contributions, "tree_predictions": [round(float(value), 6) for value in tree_predictions], "provenance": {"algorithm_id": artifact["id"], "algorithm_type": artifact["type"], "feature_engine": "phasemed-model-lab-forest-0.1"}}
        return {"model_id": model.id, "prediction": 1 if raw >= 0.5 else 0, "probability_positive": round(raw, 6), "features": features, "contributions": contributions, "tree_predictions": [round(float(value), 6) for value in tree_predictions], "provenance": {"algorithm_id": artifact["id"], "algorithm_type": artifact["type"], "feature_engine": "phasemed-model-lab-forest-0.1"}}
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
