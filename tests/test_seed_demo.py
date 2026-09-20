import io

import numpy as np
import pydicom
import pytest

import backend.main as main
import scripts.seed_demo_data as seed


@pytest.mark.parametrize("spec", seed.ALL_STUDIES, ids=lambda spec: spec["key"])
def test_every_demo_study_produces_a_segmentation(spec):
    """The guided workspace seeds every spec; an empty SEG for any of them breaks the one-click demo."""
    spec = {**spec, "origin": "test phantom"}
    uids = {"study": pydicom.uid.generate_uid(), "frame": pydicom.uid.generate_uid()}
    labels = seed.phantom_labels(spec, None, seed.SEG_SIZE, seed.SEG_MM, None)  # atlas=None: the offline fallback path
    assert np.unique(labels).size > 1
    name, data = seed.seg_file(spec, uids, labels)
    dataset = pydicom.dcmread(io.BytesIO(data))
    assert name.endswith("-seg.dcm") and dataset.Modality == "SEG"
    assert len(dataset.SegmentSequence) >= 1 and int(dataset.NumberOfFrames) >= 1
    assert all(str(segment.SegmentLabel).strip() for segment in dataset.SegmentSequence)


def test_chest_studies_keep_their_anatomy_names():
    chest = {**seed.DEMO_STUDIES[0], "origin": "test phantom"}
    labels = seed.phantom_labels(chest, None, seed.SEG_SIZE, seed.SEG_MM, None)
    _, data = seed.seg_file(chest, {"study": pydicom.uid.generate_uid(), "frame": pydicom.uid.generate_uid()}, labels)
    names = {str(segment.SegmentLabel) for segment in pydicom.dcmread(io.BytesIO(data)).SegmentSequence}
    assert {"Right lung", "Left lung", "Heart"} <= names


def test_demo_seed_progress_lines_become_job_fields():
    step = main._demo_seed_progress("[3/7] CT Chest · screening (synthetic phantom)")
    assert step == {"study_count": 2, "study_total": 7, "message": "Building study 3 of 7 · CT Chest · screening"}
    assert "70 MB" in main._demo_seed_progress("atlas: fetching Heart (1 parts)")["message"]
    assert main._demo_seed_progress("Anatomy: BodyParts3D, (c) …") is None  # attribution is not progress and never the error


def test_a_failed_seed_reports_the_exception_not_the_last_stdout_line(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "seed_demo_data.py").write_text(
        "import sys\nprint('Seeding 7 studies')\nprint('[1/7] CT Chest · baseline (synthetic phantom)')\nprint('Anatomy: BodyParts3D, (c) attribution line')\n"
        "sys.stderr.write('Traceback (most recent call last):\\n  File \"x.py\", line 1\\nValueError: need at least one array to stack\\n')\nsys.exit(1)\n", encoding="utf-8")
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "_demo_studies", lambda: [])
    main.DEMO_SEED_JOBS["demo-test"] = {"id": "demo-test", "status": "running", "message": "", "study_count": 0, "study_total": 0}
    main._run_demo_seed("demo-test", "http://127.0.0.1:9")
    job = main.DEMO_SEED_JOBS.pop("demo-test")
    assert job["status"] == "failed" and job["message"] == "ValueError: need at least one array to stack"
    assert job["study_total"] == 7 and "BodyParts3D" not in job["message"]


def test_a_successful_seed_is_marked_ready(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "seed_demo_data.py").write_text("print('Seeding 2 studies')\nprint('[1/2] A (synthetic phantom)')\nprint('[2/2] B (synthetic phantom)')\n", encoding="utf-8")
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "_demo_studies", lambda: [{}, {}])
    main.DEMO_SEED_JOBS["demo-ok"] = {"id": "demo-ok", "status": "running", "message": "", "study_count": 0, "study_total": 0}
    main._run_demo_seed("demo-ok", "http://127.0.0.1:9")
    job = main.DEMO_SEED_JOBS.pop("demo-ok")
    assert job["status"] == "completed" and job["study_count"] == 2 and job["study_total"] == 2 and job["message"] == "Workspace ready"
