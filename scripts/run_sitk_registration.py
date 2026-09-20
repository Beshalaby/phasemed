#!/usr/bin/env python3
"""Register two DICOM study volumes and write a JSON transform result."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import SimpleITK as sitk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.dicom import index_directory, load_series_volume


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register two Phasmed DICOM study directories")
    parser.add_argument("--current-dir", required=True, type=Path)
    parser.add_argument("--prior-dir", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--current-model-id", required=True)
    parser.add_argument("--prior-model-id", required=True)
    return parser.parse_args()


def _series_image(root: Path, study_id: str) -> tuple[sitk.Image, dict]:
    _, series_rows = index_directory(root, study_id)
    candidates = [item for item in series_rows if str(item.get("modality") or "").upper() not in {"SEG", "SR"}]
    if not candidates:
        raise RuntimeError("No non-SEG image series was found")
    series = max(candidates, key=lambda item: int(item.get("instance_count") or 0))
    volume = load_series_volume(root, series)
    if volume is None:
        raise RuntimeError("The selected DICOM series could not be reconstructed")
    image = sitk.GetImageFromArray(np.asarray(volume, dtype=np.float32))
    spacing = series.get("pixel_spacing") or (1.0, 1.0)
    image.SetSpacing((float(spacing[1]), float(spacing[0]), float(series.get("slice_thickness") or 1.0)))
    instances = series.get("instances") or []
    first = instances[0] if instances else {}
    if first.get("position"):
        image.SetOrigin(tuple(float(value) for value in first["position"]))
    orientation = first.get("orientation")
    if orientation and len(orientation) == 6:
        row = np.asarray(orientation[:3], dtype=float)
        column = np.asarray(orientation[3:], dtype=float)
        normal = np.cross(row, column)
        direction = np.asarray([[row[0], column[0], normal[0]], [row[1], column[1], normal[1]], [row[2], column[2], normal[2]]], dtype=float)
        image.SetDirection(tuple(direction.reshape(-1).tolist()))
    return image, {"series_instance_uid": series["series_instance_uid"], "shape": list(volume.shape), "modality": series.get("modality")}


def main() -> int:
    args = parse_args()
    current, current_meta = _series_image(args.current_dir, args.current_model_id)
    prior, prior_meta = _series_image(args.prior_dir, args.prior_model_id)
    mode = os.getenv("PHASEMED_REGISTRATION_MODE", "rigid").lower()
    registration = sitk.ImageRegistrationMethod()
    registration.SetMetricAsMattesMutualInformation(numberOfHistogramBins=32)
    registration.SetMetricSamplingStrategy(registration.REGULAR)
    registration.SetMetricSamplingPercentage(0.2)
    registration.SetInterpolator(sitk.sitkLinear)
    registration.SetOptimizerAsGradientDescent(learningRate=1.0, numberOfIterations=80, convergenceMinimumValue=1e-6, convergenceWindowSize=10)
    registration.SetOptimizerScalesFromPhysicalShift()

    if mode == "deformable":
        initial = sitk.BSplineTransformInitializer(current, [4, 4, 4], order=3)
        transform_type = "BSpline"
    else:
        initial = sitk.CenteredTransformInitializer(current, prior, sitk.Euler3DTransform(), sitk.CenteredTransformInitializerFilter.GEOMETRY)
        transform_type = "Euler3D"
    registration.SetInitialTransform(initial, inPlace=False)
    final = registration.Execute(current, prior)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "completed",
        "method": f"SimpleITK-{transform_type}",
        "mode": mode,
        "current_model_id": args.current_model_id,
        "prior_model_id": args.prior_model_id,
        "current": current_meta,
        "prior": prior_meta,
        "transform_parameters": [float(value) for value in final.GetParameters()],
        "transform_fixed_parameters": [float(value) for value in final.GetFixedParameters()],
        "metric_value": float(registration.GetMetricValue()),
        "optimizer_stop_condition": registration.GetOptimizerStopConditionDescription(),
    }
    args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"registration failed: {exc}", file=sys.stderr)
        raise
