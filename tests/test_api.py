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
    assert 'id="heroTitle"' in landing.text
    assert 'id="lab"' in landing.text
    assert 'href="/trial-studio"' in landing.text
    workspace = client.get("/workspace")
    assert workspace.status_code == 200
    assert "Start with a study" in workspace.text
    assert 'id="emptyDemo"' in workspace.text
    assert 'id="emptyImport"' in workspace.text
    # The old rail header duplicated "Studies" and "Import a study" above
    # the library tabs, collapsing the left rail when mixed with the current UI.
    assert 'class="rail-patient"' not in workspace.text
    assert 'id="newStudy"' not in workspace.text
    assert 'data-temporal-mode="overlay"' in workspace.text
    assert 'data-temporal-mode="difference"' in workspace.text
    assert 'data-temporal-mode="morph"' in workspace.text
    assert 'data-tool="select"' not in workspace.text
    assert 'data-tool="rotate"' not in workspace.text
    assert 'data-tool="pan"' not in workspace.text
    assert 'data-tool="zoom"' not in workspace.text
    assert 'class="tool-control model-only-control" data-tool="measure"' in workspace.text
    assert 'class="tool-control model-only-control" id="sceneNeighbors"' in workspace.text
    assert 'id="sceneFit"' not in workspace.text
    assert 'id="sceneZoomOut"' not in workspace.text
    assert 'id="sceneAnatomy"' in workspace.text
    assert 'id="sceneAnatomyLabel"' in workspace.text
    assert 'id="sliceInput"' in workspace.text
    assert 'data-tool="path"' not in workspace.text
    assert 'id="hologramExit"' in workspace.text
    assert "Pepper's Ghost hologram preview" in workspace.text
    assert 'id="gestureButton"' in workspace.text
    assert 'id="gestureVideo"' in workspace.text
    assert 'id="gestureOverlay"' in workspace.text
    assert 'id="gesturePreviewToggle"' in workspace.text
    assert 'id="gestureScanPreview"' in workspace.text
    assert 'id="gestureScanImage"' in workspace.text
    assert "Gesture guide" in workspace.text
    assert "Two fists + drag" in workspace.text
    assert "Open palm + hold" in workspace.text
    assert '/assets/gesture-camera.js' in workspace.text
    assert 'id="displayHologramButton"' not in workspace.text

    gesture_camera = client.get("/assets/gesture-camera.js")
    assert gesture_camera.status_code == 200
    assert 'const VISION_VERSION = "0.10.21"' in gesture_camera.text
    assert "${VISION_CDN}/vision_bundle.mjs" in gesture_camera.text
    assert "${VISION_CDN}/wasm" in gesture_camera.text

    app_script = client.get("/assets/app.js")
    assert app_script.status_code == 200
    assert 'window.open(`${location.origin}${route}?display=hologram`, "phasemed-hologram")' in app_script.text
    assert 'function ensureHologramDisplayTab()' in app_script.text
    assert 'ensureHologramDisplayTab(); state.study = await api' in app_script.text
    assert 'async function startCompile(studyId) { ensureHologramDisplayTab();' in app_script.text
    assert 'if (!isHologramDisplay || !state.model) return;' in app_script.text
    assert 'if (isHologramDisplay) { startGestureCamera(); hologramChannel?.postMessage({ type: "ready" }); }' in app_script.text
    assert 'localStorage.getItem("phasemed-gesture-preview")' in app_script.text
    assert 'Two pinches · move apart / together to zoom' in app_script.text
    assert 'type: "scan-preview"' in app_script.text
    assert 'function handleTwoFistSliceGesture' in app_script.text
    assert 'function resetGestureContact' in app_script.text
    assert 'endGestureOrbit(null, true)' in app_script.text


def test_demo_seed_is_idempotent_when_samples_exist(monkeypatch):
    monkeypatch.setattr(main, "_demo_studies", lambda: [{"patient_id": "DEMO-001"}, {"patient_id": "DEMO-002"}])
    response = TestClient(main.app).post("/api/demo/seed")
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "completed"
    assert response.json()["job"]["study_count"] == 2


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
    assert client.post(f"/api/studies/{study_id}/compile").status_code == 409
    rebuild_response = client.post(f"/api/studies/{study_id}/compile", params={"rebuild": "true"})
    assert rebuild_response.status_code == 200
    rebuild_job = client.get(f"/api/jobs/{rebuild_response.json()['job']['id']}").json()
    assert rebuild_job["status"] == "completed"
    assert any(event["type"] == "study.imported" for event in client.get("/api/audit-events").json()["events"])

    stored_study = client.get(f"/api/studies/{study_id}").json()
    model_id = stored_study["model_id"]
    model = client.get(f"/api/models/{model_id}").json()
    assert model["objects"]
    series_uid = stored_study["series"][0]["series_instance_uid"]
    assert client.get(f"/api/studies/{study_id}/volume", params={"series_uid": series_uid}).json()["series_instance_uid"] == series_uid
    assert client.get(f"/api/models/{model_id}/relationships").status_code == 200
    object_id = model["objects"][0]["id"]
    reviewed = client.post(f"/api/models/{model_id}/objects/{object_id}/review", json={"status": "confirmed"})
    assert reviewed.status_code == 200 and reviewed.json()["review_status"] == "confirmed"
    history_after_review = client.get(f"/api/models/{model_id}/history")
    assert history_after_review.status_code == 200
    assert history_after_review.json()["current_version"] == 3
    assert [item["version"] for item in history_after_review.json()["revisions"]] == [1, 2, 3]
    assert client.get(f"/api/models/{model_id}/revisions/1").json()["snapshot"]["version"] == 1
    assert client.post(f"/api/models/{model_id}/spatial", json={"operation": "trajectory", "start": [0, 0, 0], "end": [4, 0, 0]}).json()["length_mm"] == 4
    assert client.get(f"/api/graph/{model_id}").json()["@id"] == model_id

    bundle = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "DiagnosticReport", "id": "r1", "code": {"text": "Follow-up"}, "text": "No acute finding."}}]}
    context_response = client.post(f"/api/models/{model_id}/context", json=bundle)
    assert context_response.status_code == 200
    assert client.get(f"/api/models/{model_id}").json()["capabilities"]["clinical_context"] == "available"
    assert client.post(f"/api/models/{model_id}/context-query", json={"query": "follow-up"}).json()["results"]
    assert client.get(f"/api/patient-models/{model_id}/history").json()["current_version"] == 4
    audit = client.get(f"/api/models/{model_id}/audit")
    assert audit.status_code == 200 and any(event["type"] == "context.imported" for event in audit.json()["events"])
    assert client.get("/api/audit-events", params={"subject": model_id}).json()["events"]

    with TestClient(main.app) as restarted:
        assert restarted.get(f"/api/studies/{study_id}").json()["status"] == "ready"
        assert restarted.get(f"/api/models/{model_id}").json()["version"] == 4
        assert restarted.get(f"/api/models/{model_id}/history").json()["current_version"] == 4


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


def test_restart_marks_interrupted_compile_as_failed(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "STUDY_ROOT", runtime / "studies")
    monkeypatch.setattr(main, "MODEL_ROOT", runtime / "models")
    monkeypatch.setattr(main, "DB_PATH", runtime / "phasemed.sqlite3")
    main.STUDY_ROOT.mkdir(parents=True)
    main.MODEL_ROOT.mkdir(parents=True)

    study = main.StudySummary(id="study-interrupted", study_instance_uid="1.2.3", status="compiling")
    main.save_study(study, [])
    main.save_job(main.JobState(id="job-interrupted", study_id=study.id, status="running", stage="Reconstruct source volume"))

    with TestClient(main.app):
        pass

    assert main.get_study(study.id)["status"] == "failed"
    assert main.get_job("job-interrupted").status == "failed"
    assert "restarted" in (main.get_job("job-interrupted").error or "")
