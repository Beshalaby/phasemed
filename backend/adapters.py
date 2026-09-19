"""Explicit hooks for optional clinical segmentation and registration engines.

The local workstation stays useful without external models, but production
deployments can configure a validated engine through an argv-style command
template. Commands never run through a shell. Segmentation commands must write
DICOM SEG instances into ``{output_dir}``; registration commands must write a
JSON result to ``{output_json}``.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pydicom


def _run_template(template: str, values: dict[str, str], timeout: int = 1800) -> tuple[int, str, str]:
    tokens = [token.format(**values) for token in shlex.split(template)]
    if not tokens:
        raise ValueError("adapter command is empty")
    completed = subprocess.run(tokens, check=False, capture_output=True, text=True, timeout=timeout)
    return completed.returncode, completed.stdout[-4000:], completed.stderr[-4000:]


def status() -> dict[str, str]:
    return {
        "segmentation_adapter": "configured" if os.getenv("PHASEMED_SEGMENTATION_COMMAND") else "not_configured",
        "registration_adapter": "configured" if os.getenv("PHASEMED_REGISTRATION_COMMAND") else "not_configured",
    }


def run_segmentation(study_root: Path, study_id: str) -> dict[str, Any]:
    template = os.getenv("PHASEMED_SEGMENTATION_COMMAND")
    if not template:
        return {"status": "not_configured", "method": "none", "study_id": study_id}
    output_dir = study_root / "derived" / "adapter-segmentation"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    values = {"input_dir": str(study_root), "output_dir": str(output_dir), "study_id": study_id}
    try:
        code, stdout, stderr = _run_template(template, values)
    except Exception as exc:
        return {"status": "failed", "method": "configured-command", "error": str(exc), "study_id": study_id}
    result: dict[str, Any] = {"status": "completed" if code == 0 else "failed", "method": "configured-command", "study_id": study_id, "output_dir": str(output_dir), "exit_code": code}
    if stdout:
        result["stdout_tail"] = stdout
    if stderr:
        result["stderr_tail"] = stderr
    if code == 0:
        seg_instances = 0
        for path in output_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                dataset = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
            except Exception:
                continue
            if str(getattr(dataset, "Modality", "")).upper() == "SEG":
                seg_instances += 1
        result["dicom_seg_instances"] = seg_instances
        if seg_instances == 0:
            result["status"] = "failed"
            result["error"] = "segmentation command completed without producing a readable DICOM SEG instance"
    return result


def run_registration(current_root: Path, prior_root: Path, current_model_id: str, prior_model_id: str) -> dict[str, Any]:
    template = os.getenv("PHASEMED_REGISTRATION_COMMAND")
    if not template:
        return {"status": "not_configured", "method": "none", "current_model_id": current_model_id, "prior_model_id": prior_model_id}
    output_json = current_root / "derived" / f"registration-{prior_model_id.replace(':', '_')}.json"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    values = {"current_dir": str(current_root), "prior_dir": str(prior_root), "output_json": str(output_json), "current_model_id": current_model_id, "prior_model_id": prior_model_id}
    try:
        code, stdout, stderr = _run_template(template, values)
    except Exception as exc:
        return {"status": "failed", "method": "configured-command", "error": str(exc), "current_model_id": current_model_id, "prior_model_id": prior_model_id}
    result: dict[str, Any] = {"status": "completed" if code == 0 else "failed", "method": "configured-command", "current_model_id": current_model_id, "prior_model_id": prior_model_id, "output_json": str(output_json), "exit_code": code}
    if stdout:
        result["stdout_tail"] = stdout
    if stderr:
        result["stderr_tail"] = stderr
    if code == 0 and output_json.exists():
        try:
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                result["result"] = payload
        except (OSError, json.JSONDecodeError) as exc:
            result["status"] = "failed"
            result["error"] = f"registration output is not valid JSON: {exc}"
    elif code == 0:
        result["status"] = "failed"
        result["error"] = "registration command completed without writing output_json"
    return result
