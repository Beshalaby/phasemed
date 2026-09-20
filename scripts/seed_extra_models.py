"""Add the non-chest synthetic anatomy studies to an existing workspace.

This is useful when the original chest demo set is already present. The
studies are synthetic, local-only fixtures and go through the normal DICOM
import and PatientModel compiler.

    ./.venv/bin/python scripts/seed_extra_models.py
"""
from __future__ import annotations

import argparse
import time

import httpx

try:
    from scripts.seed_demo_data import EXTRA_STUDIES, seed_study
except ModuleNotFoundError:  # run as ./scripts/seed_extra_models.py
    from seed_demo_data import EXTRA_STUDIES, seed_study


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--force", action="store_true", help="seed duplicates even when extra studies already exist")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=300) as client:
        existing = {str(study.get("patient_id", "")) for study in client.get("/api/studies").raise_for_status().json()}
        pending = [spec for spec in EXTRA_STUDIES if args.force or spec["patient_id"] not in existing]
        if not pending:
            print("Extra synthetic anatomy studies already present.")
            return 0
        for spec in pending:
            started = time.monotonic()
            model_id = seed_study(client, spec, None)
            print(f"{spec['patient_id']}  {spec['description']}  ->  {model_id}  ({time.monotonic() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
