#!/usr/bin/env python3
"""Run TotalSegmentator against one imported DICOM series.

The Phasemed adapter contract passes the complete study directory and a
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TotalSegmentator and emit DICOM SEG for Phasemed")
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

        task = os.getenv("PHASEMED_TS_TASK", "total")
        device = os.getenv("PHASEMED_TS_DEVICE", "cpu")
        fast = os.getenv("PHASEMED_TS_FAST", "0").lower() in {"1", "true", "yes"}
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
