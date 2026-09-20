import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid


pytest.importorskip("SimpleITK")


def test_totalsegmentator_task_tracks_source_modality():
    from scripts.run_totalsegmentator import fast_mode_for_task, task_for_modality

    assert task_for_modality("CT") == "total"
    assert task_for_modality("MR") == "total_mr"
    assert task_for_modality("mr") == "total_mr"
    assert task_for_modality("MR", "tissue_types_mr") == "tissue_types_mr"
    assert fast_mode_for_task("total", "1") is True
    assert fast_mode_for_task("total_mr", "1", "0") is False


def write_series(root: Path, shift: float) -> None:
    study_uid = generate_uid()
    series_uid = generate_uid()
    for index in range(4):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        path = root / f"slice-{index}.dcm"
        ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.PatientName = "Registration^Patient"
        ds.PatientID = "REG-001"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = SecondaryCaptureImageStorage
        ds.Modality = "CT"
        ds.Rows = 32
        ds.Columns = 32
        ds.PixelSpacing = [1.0, 1.0]
        ds.SliceThickness = 2.0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        ds.InstanceNumber = index + 1
        ds.ImagePositionPatient = [shift, 0, float(index * 2)]
        yy, xx = np.indices((32, 32))
        ds.PixelData = ((xx * 3 + yy * 5 + index * 11) % 4096).astype(np.uint16).tobytes()
        ds.save_as(path)


def test_sitk_registration_runner_writes_transform_json(tmp_path: Path):
    current = tmp_path / "current"
    prior = tmp_path / "prior"
    current.mkdir()
    prior.mkdir()
    write_series(current, 0.0)
    write_series(prior, 2.0)
    output = tmp_path / "registration.json"
    environment = os.environ.copy()
    environment["PHASEMED_REGISTRATION_MODE"] = "rigid"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_sitk_registration.py",
            "--current-dir",
            str(current),
            "--prior-dir",
            str(prior),
            "--output-json",
            str(output),
            "--current-model-id",
            "model:current",
            "--prior-model-id",
            "model:prior",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text())
    assert payload["status"] == "completed"
    assert payload["method"] == "SimpleITK-Euler3D"
    assert payload["current"]["shape"] == [4, 32, 32]
