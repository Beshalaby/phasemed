#!/usr/bin/env python3
"""Run TotalSegmentator against one imported DICOM series.

The Phasmed adapter contract passes the complete study directory and a
derived output directory. This runner selects the largest non-SEG image
series, gives TotalSegmentator a clean DICOM directory, and writes one real
DICOM SEG instance back into the adapter output directory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from totalsegmentator.python_api import totalsegmentator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.dicom import index_directory


def task_for_modality(modality: str | None, override: str | None = None) -> str:
    """Choose the TotalSegmentator model family for the source modality.

    An explicit environment override remains useful for specialist tasks, but
    the safe default must not send MR pixels through the CT ``total`` model.
    """
    if override:
        return override
    return "total_mr" if str(modality or "").upper() == "MR" else "total"


def fast_mode_for_task(task: str, default: str | None, mr_override: str | None = None) -> bool:
    """Allow high-resolution MR while retaining the faster CT default."""
    value = mr_override if task.endswith("_mr") and mr_override is not None else default
    return str(value or "0").lower() in {"1", "true", "yes"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TotalSegmentator and emit DICOM SEG for Phasmed")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--study-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _, series = index_directory(args.input_dir, args.study_id)
    candidates = [item for item in series if str(item.get("modality") or "").upper() not in {"SEG", "SR"}]
    if not candidates:
        raise RuntimeError("No non-SEG image series was found")
    primary = max(candidates, key=lambda item: int(item.get("instance_count") or 0))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phasemed-ts-source-") as temporary:
        source_dir = Path(temporary)
        for index, instance in enumerate(primary.get("instances", [])):
            source = args.input_dir / instance["path"]
            if source.is_file():
                shutil.copy2(source, source_dir / f"slice-{index:06d}.dcm")
        if not list(source_dir.glob("*.dcm")):
            raise RuntimeError("The selected image series has no readable DICOM files")

        task = task_for_modality(primary.get("modality"), os.getenv("PHASEMED_TS_TASK"))
        device = os.getenv("PHASEMED_TS_DEVICE", "cpu")
        fast = fast_mode_for_task(task, os.getenv("PHASEMED_TS_FAST"), os.getenv("PHASEMED_TS_FAST_MR"))
        model_size = os.getenv("PHASEMED_TS_MODEL_SIZE", "big")
        roi_subset = os.getenv("PHASEMED_TS_ROI_SUBSET")
        output = args.output_dir / "totalsegmentator.seg.dcm"
        totalsegmentator(
            input=source_dir,
            output=output,
            task=task,
            roi_subset=[item.strip() for item in roi_subset.split(",") if item.strip()] if roi_subset else None,
            output_type="dicom_seg",
            fast=fast,
            device=device,
            model_size=model_size,
            quiet=True,
        )
    if not output.is_file():
        raise RuntimeError("TotalSegmentator completed without writing a DICOM SEG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
