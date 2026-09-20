"""Prepare connected DICOMweb and C-STORE source directories for the workstation."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.dicom import index_directory


def prepare(runtime: Path) -> dict[str, int | str]:
    studies_root = runtime / "studies"
    source_root = runtime / "source-studies"
    incoming_root = runtime / "incoming"
    source_root.mkdir(parents=True, exist_ok=True)
    incoming_root.mkdir(parents=True, exist_ok=True)

    study_dirs = sorted(item for item in studies_root.iterdir() if item.is_dir()) if studies_root.exists() else []
    if not study_dirs:
        raise RuntimeError("No indexed DICOM studies are available to connect")

    connected = 0
    for source_index, study_dir in enumerate(study_dirs):
        destination = source_root / f"source-{source_index + 1:02d}"
        if not destination.exists():
            shutil.copytree(study_dir, destination)
        connected += 1

    source_study, _ = index_directory(study_dirs[0], "source-study")
    staged_dir = incoming_root / source_study.study_instance_uid
    if not staged_dir.exists():
        shutil.copytree(study_dirs[0], staged_dir)
    return {"dicomweb_studies": connected, "cstore_studies": 1, "cstore_uid": source_study.study_instance_uid}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime")
    args = parser.parse_args()
    result = prepare(args.runtime.expanduser().resolve())
    print(f"Imaging sources ready: {result['dicomweb_studies']} DICOMweb studies, {result['cstore_studies']} C-STORE study")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
