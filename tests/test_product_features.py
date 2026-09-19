from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

import backend.main as main


def make_series(root: Path) -> None:
    study_uid = generate_uid()
    series_uid = generate_uid()
    for index in range(3):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        path = root / f"slice-{index}.dcm"
        ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.PatientName = "Feature^Patient"
        ds.PatientID = "FEATURE-001"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = SecondaryCaptureImageStorage
        ds.Modality = "CT"
        ds.StudyDate = "20260919"
        ds.StudyDescription = "Feature study"
        ds.Rows = 16
        ds.Columns = 16
        ds.PixelSpacing = [1.0, 1.0]
        ds.SliceThickness = 2.0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        ds.InstanceNumber = index + 1
        ds.ImagePositionPatient = [0, 0, float(index * 2)]
        ds.PixelData = (np.arange(256, dtype=np.uint16) + index).tobytes()
        ds.save_as(path)


def test_full_local_model_surface(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    source = tmp_path / "source"
    source.mkdir()
    make_series(source)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True)
    main.MODEL_ROOT.mkdir(parents=True)
    client = TestClient(main.app)

    files = [("files", (path.name, path.read_bytes(), "application/dicom")) for path in sorted(source.glob("*.dcm"))]
    imported = client.post("/api/studies/import", files=files)
    assert imported.status_code == 200
    study_id = imported.json()["study"]["id"]
    compiled = client.post(f"/api/studies/{study_id}/compile")
    assert compiled.status_code == 200
    job_id = compiled.json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    study = client.get(f"/api/studies/{study_id}").json()
    model_id = study["model_id"]
    model = client.get(f"/api/patient-models/{model_id}").json()
    assert model["capabilities"]["spatial_index"] == "available"
    assert all("relationships" in item for item in model["objects"])

    volume = client.get(f"/api/studies/{study_id}/volume")
    assert volume.status_code == 200 and volume.json()["shape"] == [3, 16, 16]
    series_uid = volume.json()["series_instance_uid"]
    mpr = client.get(f"/api/studies/{study_id}/series/{series_uid}/mpr", params={"plane": "coronal", "index": 5})
    assert mpr.status_code == 200 and mpr.headers["content-type"].startswith("image/png")
    windowed = client.get(f"/api/studies/{study_id}/series/{series_uid}/mpr", params={"plane": "axial", "index": 1, "window_center": 128, "window_width": 2})
    assert windowed.status_code == 200 and windowed.headers["content-type"].startswith("image/png")
    assert windowed.content != mpr.content

    object_id = model["objects"][0]["id"]
    assert client.get(f"/api/patient-models/{model_id}/objects/{object_id}").status_code == 200
    assert client.get(f"/api/patient-models/{model_id}/objects/{object_id}/relationships").status_code == 200
    assert client.get(f"/api/patient-models/{model_id}/sources").status_code == 200
    region_id = next(item["id"] for item in model["objects"] if item["type"] == "region")
    region_detail = client.get(f"/api/patient-models/{model_id}/objects/{region_id}").json()
    assert region_detail["geometry"]["surface_area_mm2"] is not None
    mesh = client.get(f"/api/patient-models/{model_id}/objects/{region_id}/mesh")
    assert mesh.status_code == 200 and mesh.headers["content-type"].startswith("text/plain")
    graph = client.get(f"/api/patient-models/{model_id}/objects/{region_id}/graph")
    assert graph.status_code == 200 and graph.json()["provenance"]["model_id"] == model_id
    assert client.post(f"/api/patient-models/{model_id}/spatial-query", json={"operation": "surface_area", "object_id": region_id}).json()["value"] > 0
    tool = client.post(f"/api/patient-models/{model_id}/tools", json={"tool": "get_measurements", "arguments": {"object_id": object_id}})
    assert tool.status_code == 200 and tool.json()["provenance"]["model_id"] == model_id
    context = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "DiagnosticReport", "id": "feature-report", "code": {"text": "Follow-up"}, "text": "Source-volume follow-up."}}]}
    imported_context = client.post(f"/api/models/{model_id}/context", json=context)
    assert imported_context.status_code == 200
    binding_id = imported_context.json()["bindings"][0]["id"]
    reviewed = client.post(f"/api/models/{model_id}/context/{binding_id}/review", json={"status": "confirmed"})
    assert reviewed.status_code == 200 and reviewed.json()["review_status"] == "confirmed"
    context_query = client.post(f"/api/patient-models/{model_id}/context-query", json={"query": "follow-up"})
    assert context_query.status_code == 200 and context_query.json()["results"]
    path = client.post(f"/api/patient-models/{model_id}/procedure-paths", json={"start": [0, 0, 0], "end": [10, 10, 10]})
    assert path.status_code == 200 and path.json()["result"]["method"] == "segment-aabb"
    assert client.get(f"/api/patient-models/{model_id}").json()["procedure_paths"]
    assert client.get(f"/api/graph/{model_id}").json()["contextBindings"]
    assert client.get("/api/dicomweb/capabilities").status_code == 200
    cstore = client.get("/api/dicomweb/cstore/status")
    assert cstore.status_code == 200 and cstore.json()["running"] is False
    adapters = client.get("/api/adapters/status")
    assert adapters.status_code == 200 and adapters.json()["segmentation_adapter"] == "not_configured"


def test_same_patient_models_link_without_fabricating_registration(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    first_source.mkdir(); second_source.mkdir()
    make_series(first_source); make_series(second_source)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True); main.MODEL_ROOT.mkdir(parents=True)
    client = TestClient(main.app)

    model_ids = []
    for source in (first_source, second_source):
        files = [("files", (path.name, path.read_bytes(), "application/dicom")) for path in sorted(source.glob("*.dcm"))]
        study_id = client.post("/api/studies/import", files=files).json()["study"]["id"]
        job_id = client.post(f"/api/studies/{study_id}/compile").json()["job"]["id"]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
        model_ids.append(client.get(f"/api/studies/{study_id}").json()["model_id"])

    current = client.get(f"/api/models/{model_ids[-1]}").json()
    assert current["metadata"]["prior_model_id"] == model_ids[0]
    assert current["capabilities"]["temporal_registration"] == "partial"
    assert current["timeline"] and current["temporal_links"]
    assert any(item["temporal_links"] for item in current["objects"])


def test_model_lab_assembles_trains_and_predicts_from_persisted_models(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    first_source.mkdir(); second_source.mkdir()
    make_series(first_source); make_series(second_source)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True); main.MODEL_ROOT.mkdir(parents=True)
    client = TestClient(main.app)

    model_ids = []
    for source in (first_source, second_source):
        files = [("files", (path.name, path.read_bytes(), "application/dicom")) for path in sorted(source.glob("*.dcm"))]
        study_id = client.post("/api/studies/import", files=files).json()["study"]["id"]
        job_id = client.post(f"/api/studies/{study_id}/compile").json()["job"]["id"]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
        model_ids.append(client.get(f"/api/studies/{study_id}").json()["model_id"])

    dataset = client.post("/api/model-lab/datasets", json={"name": "Local validation", "model_ids": model_ids, "labels": {model_ids[0]: 0, model_ids[1]: 1}})
    assert dataset.status_code == 200 and dataset.json()["provenance"]["label_source"] == "caller-supplied"
    uploaded = client.post("/api/model-lab/datasets/import", data={"task": "binary", "name": "Uploaded labels"}, files={"file": ("labels.csv", f"model_id,label\n{model_ids[0]},0\n{model_ids[1]},1\n".encode(), "text/csv")})
    assert uploaded.status_code == 200 and uploaded.json()["provenance"]["label_source"] == "caller-supplied-upload"
    algorithm = client.post("/api/model-lab/train", json={"dataset_id": dataset.json()["id"], "name": "Change screen", "iterations": 40})
    assert algorithm.status_code == 200 and algorithm.json()["type"] == "binary-logistic-regression"
    searched = client.post("/api/model-lab/search", json={"dataset_id": dataset.json()["id"], "name": "Searched change screen", "candidates": [{"iterations": 40, "learning_rate": 0.04, "l2": 0.001}, {"iterations": 50, "learning_rate": 0.08, "l2": 0.01}]})
    assert searched.status_code == 200 and searched.json()["search"]["candidate_count"] == 2
    forest = client.post("/api/model-lab/train", json={"dataset_id": dataset.json()["id"], "algorithm": "random-forest", "name": "Finding forest", "n_estimators": 8, "max_depth": 4, "seed": 17})
    assert forest.status_code == 200 and forest.json()["type"] == "random-forest-classifier"
    forest_prediction = client.post(f"/api/model-lab/algorithms/{forest.json()['id']}/predict", json={"model_id": model_ids[1]})
    assert forest_prediction.status_code == 200 and len(forest_prediction.json()["tree_predictions"]) == 8
    batch_prediction = client.post(f"/api/model-lab/algorithms/{forest.json()['id']}/batch-predict", json={"model_ids": model_ids})
    assert batch_prediction.status_code == 200 and len(batch_prediction.json()["results"]) == len(model_ids)
    listed_forest = next(item for item in client.get("/api/model-lab/algorithms").json() if item["id"] == forest.json()["id"])
    assert "trees" not in listed_forest and "feature_importance" in listed_forest
    assert len(client.get(f"/api/model-lab/algorithms/{forest.json()['id']}").json()["trees"]) == 8
    prediction = client.post(f"/api/model-lab/algorithms/{algorithm.json()['id']}/predict", json={"model_id": model_ids[1]})
    assert prediction.status_code == 200 and "contributions" in prediction.json()
    features = client.get("/api/model-lab/features", params={"model_ids": ",".join(model_ids)})
    assert features.status_code == 200 and len(features.json()["rows"]) == 2
    csv_export = client.get(f"/api/model-lab/datasets/{dataset.json()['id']}/csv")
    assert csv_export.status_code == 200 and "model_id" in csv_export.text
    evaluation = client.post(f"/api/model-lab/algorithms/{algorithm.json()['id']}/evaluate", json={"model_ids": model_ids, "labels": {model_ids[0]: 0, model_ids[1]: 1}})
    assert evaluation.status_code == 200 and evaluation.json()["provenance"]["label_source"] == "caller-supplied"
    regression_dataset = client.post("/api/model-lab/datasets", json={"task": "regression", "name": "Volume outcome", "model_ids": model_ids, "labels": {model_ids[0]: 10.0, model_ids[1]: 20.0}})
    assert regression_dataset.status_code == 200 and regression_dataset.json()["task"] == "regression"
    regression_algorithm = client.post("/api/model-lab/train", json={"dataset_id": regression_dataset.json()["id"], "iterations": 40})
    assert regression_algorithm.status_code == 200 and regression_algorithm.json()["type"] == "linear-regression"
    regression_evaluation = client.post(f"/api/model-lab/algorithms/{regression_algorithm.json()['id']}/evaluate", json={"model_ids": model_ids, "labels": {model_ids[0]: 10.0, model_ids[1]: 20.0}})
    assert regression_evaluation.status_code == 200 and "rmse" in regression_evaluation.json()["metrics"]
