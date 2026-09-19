from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

import backend.main as main


def test_landing_page_and_workstation_routes():
    client = TestClient(main.app)
    landing = client.get("/")
    assert landing.status_code == 200
    assert "Imaging, <em>compiled.</em>" in landing.text
    assert "data-slide=\"4\"" in landing.text
    workspace = client.get("/workspace")
    assert workspace.status_code == 200
    assert "Open a study" in workspace.text
    assert 'data-temporal-mode="overlay"' in workspace.text
    assert 'data-temporal-mode="difference"' in workspace.text
    assert 'data-temporal-mode="morph"' in workspace.text


def dicom_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "slice.dcm"
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = "API^Patient"
    ds.PatientID = "API-001"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.Modality = "CT"
    ds.StudyDate = "20260919"
    ds.StudyDescription = "API chest"
    ds.Rows = 8
    ds.Columns = 8
    ds.PixelSpacing = [1.0, 1.0]
    ds.SliceThickness = 1.0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.InstanceNumber = 1
    ds.ImagePositionPatient = [0, 0, 0]
    ds.PixelData = (np.arange(64, dtype=np.uint16)).tobytes()
    ds.save_as(path)
    return path.read_bytes()


def test_api_import_compile_and_query(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True)
    main.MODEL_ROOT.mkdir(parents=True)
    client = TestClient(main.app)

    response = client.post("/api/studies/import", files={"files": ("slice.dcm", dicom_bytes(tmp_path), "application/dicom")})
    assert response.status_code == 200
    study = response.json()["study"]
    study_id = study["id"]

    compile_response = client.post(f"/api/studies/{study_id}/compile")
    assert compile_response.status_code == 200
    job = client.get(f"/api/jobs/{compile_response.json()['job']['id']}").json()
    assert job["status"] == "completed"

    stored_study = client.get(f"/api/studies/{study_id}").json()
    model_id = stored_study["model_id"]
    model = client.get(f"/api/models/{model_id}").json()
    assert model["objects"]
    assert client.get(f"/api/models/{model_id}/relationships").status_code == 200
    object_id = model["objects"][0]["id"]
    reviewed = client.post(f"/api/models/{model_id}/objects/{object_id}/review", json={"status": "confirmed"})
    assert reviewed.status_code == 200 and reviewed.json()["review_status"] == "confirmed"
    assert client.post(f"/api/models/{model_id}/spatial", json={"operation": "trajectory", "start": [0, 0, 0], "end": [4, 0, 0]}).json()["length_mm"] == 4
    assert client.get(f"/api/graph/{model_id}").json()["@id"] == model_id

    bundle = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "DiagnosticReport", "id": "r1", "code": {"text": "Follow-up"}, "text": "No acute finding."}}]}
    context_response = client.post(f"/api/models/{model_id}/context", json=bundle)
    assert context_response.status_code == 200
    assert client.post(f"/api/models/{model_id}/context-query", json={"query": "follow-up"}).json()["results"]


def test_cstore_staging_can_be_promoted_to_local_study(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True)
    main.MODEL_ROOT.mkdir(parents=True)
    staged = runtime / "incoming" / "1.2.3.4"
    staged.mkdir(parents=True)
    (staged / "slice.dcm").write_bytes(dicom_bytes(tmp_path))
    client = TestClient(main.app)

    listed = client.get("/api/dicomweb/cstore/studies")
    assert listed.status_code == 200 and listed.json()[0]["study_instance_uid"]
    promoted = client.post("/api/dicomweb/cstore/studies/1.2.3.4/import")
    assert promoted.status_code == 200
    assert promoted.json()["study"]["id"].startswith("cstore-")
    assert client.get("/api/studies").json()[0]["id"] == promoted.json()["study"]["id"]
