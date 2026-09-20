from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .compiler import compile_study
from .adapters import run_registration, status as adapter_status
from .context import bind_context, normalize_context
from .cstore import CStoreReceiver, configured_receiver
from .dicomweb import capability_status, qido_instances, qido_series, qido_studies, request as dicomweb_request, stow_dicom, wado_instance, wado_metadata
from .temporal import compare_models
from .voice import MAX_AUDIO_BYTES as MAX_VOICE_AUDIO_BYTES, resolve_command, status as voice_status, transcribe as voice_transcribe, warm_local as voice_warm_local
from .dicom import grayscale_png, index_directory, load_series_volume, unpack_upload, volume_plane_png
from .geometry import (
    SpatialIndex,
    adjacent,
    contains,
    distance,
    intersects,
    minimum_surface_distance,
    nearest,
    trajectory_clearance,
    trajectory_intersections,
    trajectory_length,
    within_radius,
)
from .models import JobState, PatientModel, SpatialQuery, StudySummary, TimelineEntry, now_iso
from .model_lab import FEATURE_NAMES, FEATURE_SCHEMA, analyze_cohort, cross_validate, dataset_rows, extract_features, predict as predict_algorithm, summarize_dataset, train_algorithm
from .trial_studio import randomize as trial_randomize, simulate as trial_simulate


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.getenv("PHASEMED_RUNTIME_DIR", str(ROOT / ".runtime"))).expanduser().resolve()
STUDY_ROOT = RUNTIME / "studies"
MODEL_ROOT = RUNTIME / "models"
TRIAL_ROOT = RUNTIME / "trial-runs"
DB_PATH = RUNTIME / "phasemed.sqlite3"
WEB_ROOT = ROOT / "web"
CSTORE_RECEIVER: CStoreReceiver | None = None
DEMO_SEED_LOCK = threading.Lock()
DEMO_SEED_JOBS: dict[str, dict[str, Any]] = {}
for path in (STUDY_ROOT, MODEL_ROOT, TRIAL_ROOT):
    path.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global CSTORE_RECEIVER
    recover_interrupted_jobs()
    receiver = configured_receiver(RUNTIME)
    if receiver:
        receiver.start()
        CSTORE_RECEIVER = receiver
    try:
        yield
    finally:
        if CSTORE_RECEIVER:
            CSTORE_RECEIVER.stop()
            CSTORE_RECEIVER = None


app = FastAPI(title="Phasemed Local API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8787", "http://127.0.0.1:8787"], allow_methods=["*"], allow_headers=["*"])
if WEB_ROOT.exists():
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")


def db() -> sqlite3.Connection:
    RUNTIME.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS studies (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.commit()
    return conn


def save_study(study: StudySummary, series: list[dict]) -> None:
    payload = study.model_dump()
    payload["series"] = series
    conn = db(); conn.execute("INSERT OR REPLACE INTO studies(id,payload) VALUES(?,?)", (study.id, json.dumps(payload))); conn.commit(); conn.close()


def get_study(study_id: str) -> dict:
    conn = db(); row = conn.execute("SELECT payload FROM studies WHERE id=?", (study_id,)).fetchone(); conn.close()
    if not row:
        raise HTTPException(404, "Study not found")
    return json.loads(row["payload"])


def save_job(job: JobState) -> None:
    conn = db(); conn.execute("INSERT OR REPLACE INTO jobs(id,payload) VALUES(?,?)", (job.id, job.model_dump_json())); conn.commit(); conn.close()


def model_path(model_id: str) -> Path:
    return MODEL_ROOT / f"{model_id.replace(':', '_')}.json"


def model_revision_root(model_id: str) -> Path:
    return MODEL_ROOT / "revisions" / model_id.replace(":", "_")


def model_revision_path(model_id: str, version: int) -> Path:
    return model_revision_root(model_id) / f"{version:06d}.json"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write a local artifact without exposing a partially-written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_model_revision(model: PatientModel, reason: str) -> None:
    """Write an immutable snapshot once for each PatientModel version."""
    revision_dir = model_revision_root(model.id)
    revision_dir.mkdir(parents=True, exist_ok=True)
    path = model_revision_path(model.id, model.version)
    if path.exists():
        return
    payload = {
        "id": f"revision:{model.id}:{model.version}",
        "model_id": model.id,
        "version": model.version,
        "reason": reason,
        "created_at": now_iso(),
        "snapshot": model.model_dump(),
    }
    _atomic_write_text(path, json.dumps(payload, indent=2))


def save_model(model: PatientModel, reason: str = "persisted", bump_version: bool = False) -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    canonical = model_path(model.id)
    if canonical.exists():
        try:
            previous = PatientModel.model_validate_json(canonical.read_text(encoding="utf-8"))
        except Exception:
            previous = None
        if previous and bump_version:
            # Preserve a pre-revision canonical file before advancing it.
            _write_model_revision(previous, "baseline-recovered")
            model.version = previous.version + 1
    _atomic_write_text(model_path(model.id), model.model_dump_json(indent=2))
    _write_model_revision(model, reason)


def recover_interrupted_jobs() -> None:
    """Mark work that could not survive a process restart as failed."""
    conn = db()
    rows = conn.execute("SELECT id, payload FROM jobs").fetchall()
    for row in rows:
        try:
            job = JobState.model_validate_json(row["payload"])
        except Exception:
            continue
        if job.status not in {"queued", "running"}:
            continue
        job.status = "failed"
        job.stage = "Failed"
        job.error = "The local service restarted before this job completed"
        job.message = "Restart recovery stopped the interrupted job without fabricating derived data"
        job.updated_at = now_iso()
        conn.execute("UPDATE jobs SET payload=? WHERE id=?", (job.model_dump_json(), job.id))
        study_row = conn.execute("SELECT payload FROM studies WHERE id=?", (job.study_id,)).fetchone()
        if study_row:
            try:
                study = json.loads(study_row["payload"])
                if study.get("status") == "compiling":
                    study["status"] = "failed"
                    conn.execute("UPDATE studies SET payload=? WHERE id=?", (json.dumps(study), job.study_id))
            except json.JSONDecodeError:
                pass
    conn.commit()
    conn.close()


def _has_persisted_model(model_id: str | None) -> bool:
    return bool(model_id and model_path(model_id).exists())


def audit_event(event_type: str, subject: str, detail: dict | None = None) -> None:
    payload = {"id": f"audit:{uuid.uuid4().hex}", "type": event_type, "subject": subject, "detail": detail or {}, "created_at": now_iso()}
    conn = db(); conn.execute("INSERT INTO audit_events(id,payload) VALUES(?,?)", (payload["id"], json.dumps(payload))); conn.commit(); conn.close()


def read_audit_events(subject: str | None = None, limit: int = 100) -> list[dict]:
    conn = db()
    rows = conn.execute("SELECT payload FROM audit_events ORDER BY rowid DESC LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
    conn.close()
    events = []
    for row in rows:
        try:
            event = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if subject and event.get("subject") != subject and event.get("detail", {}).get("model_id") != subject:
            continue
        events.append(event)
    return events


@app.get("/api/audit-events")
def audit_events(subject: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"events": read_audit_events(subject, limit), "provenance": {"source": "local-audit-store", "ordered": "newest-first"}}


@app.get("/api/models/{model_id}/audit")
def model_audit(model_id: str, limit: int = Query(default=100, ge=1, le=500)) -> dict:
    load_model(model_id)
    return {"model_id": model_id, "events": read_audit_events(model_id, limit), "provenance": {"source": "local-audit-store", "ordered": "newest-first"}}


def matching_models(patient_id: str) -> list[PatientModel]:
    models: list[PatientModel] = []
    for path in MODEL_ROOT.glob("*.json"):
        try:
            model = PatientModel.model_validate_json(path.read_text())
        except Exception:
            continue
        if model.patient_id == patient_id:
            models.append(model)
    return models


def attach_temporal_history(model: PatientModel) -> PatientModel:
    """Attach persisted models for this patient using an explicit local match."""
    prior_models = [item for item in matching_models(model.patient_id) if item.id != model.id]
    if not prior_models:
        return model
    prior = sorted(prior_models, key=lambda item: item.created_at)[-1]
    registration = run_registration(STUDY_ROOT / model.study_id, STUDY_ROOT / prior.study_id, model.id, prior.id)
    registration_payload = registration.get("result") if registration.get("status") == "completed" else None
    comparison = compare_models(model, prior, registration_payload)
    model.metadata["prior_model_id"] = prior.id
    model.metadata["temporal_match_method"] = "label-and-centroid-rigid-registered" if registration_payload and registration_payload.get("method") == "SimpleITK-Euler3D" else "label-and-centroid-local-match"
    model.metadata["registration_adapter"] = registration
    model.capabilities["temporal_registration"] = "available" if registration_payload and registration_payload.get("method") == "SimpleITK-Euler3D" else "partial"
    model.timeline = [
        TimelineEntry(id=f"timeline:{candidate.id}", study_id=candidate.study_id, date=candidate.created_at, label=candidate.study_id, model_id=candidate.id)
        for candidate in sorted(prior_models + [model], key=lambda item: item.created_at)
    ]
    model.metadata["temporal_link_count"] = len(comparison.get("links", []))
    return model


def get_job(job_id: str) -> JobState:
    conn = db(); row = conn.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone(); conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    return JobState.model_validate_json(row["payload"])


def compile_in_background(job_id: str, study_id: str, rebuild: bool = False) -> None:
    job = get_job(job_id)
    job.status = "running"; job.updated_at = now_iso(); save_job(job)
    try:
        def update(stage: str, progress: int, message: str) -> None:
            current = get_job(job_id); current.stage = stage; current.progress = progress; current.message = message; current.updated_at = now_iso(); save_job(current)
        model = compile_study(study_id, STUDY_ROOT / study_id, update)
        model = attach_temporal_history(model)
        save_model(model, reason="recompiled" if rebuild else "compiled", bump_version=rebuild)
        audit_event("model.recompiled" if rebuild else "model.compiled", model.id, {"study_id": study_id, "object_count": len(model.objects), "relationship_count": len(model.relationships)})
        study = get_study(study_id); study["status"] = "ready"; study["model_id"] = model.id
        conn = db(); conn.execute("UPDATE studies SET payload=? WHERE id=?", (json.dumps(study), study_id)); conn.commit(); conn.close()
        job = get_job(job_id); job.status = "completed"; job.stage = "Ready"; job.progress = 100; job.message = "PatientModel persisted"; job.updated_at = now_iso(); save_job(job)
    except Exception as exc:
        study = get_study(study_id); study["status"] = "ready" if rebuild and _has_persisted_model(study.get("model_id")) else "failed"
        conn = db(); conn.execute("UPDATE studies SET payload=? WHERE id=?", (json.dumps(study), study_id)); conn.commit(); conn.close()
        job = get_job(job_id); job.status = "failed"; job.stage = "Failed"; job.error = str(exc); job.message = "Compilation stopped without fabricating derived data"; job.updated_at = now_iso(); save_job(job)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "phasemed-local-api", "version": app.version, "capabilities": {**capability_status(), **adapter_status(), **voice_status()}}


@app.get("/api/voice/status")
def voice_capability() -> dict:
    return voice_status()


@app.post("/api/voice/warm")
async def voice_warm() -> dict:
    """Load the local speech model before the first command so the demo never waits on it."""
    status = voice_status()
    if status.get("voice_input") != "configured" or not status.get("voice_engine", "").startswith("local-whisper"):
        return {**status, "warm": False}
    try:
        await asyncio.to_thread(voice_warm_local)
    except Exception as error:  # a missing download stays non-fatal; the command path reports it
        return {**status, "warm": False, "detail": str(error)}
    return {**status, "warm": True}


@app.post("/api/patient-models/{model_id}/voice-command")
async def patient_model_voice_command(model_id: str, file: UploadFile | None = File(None), transcript: str = Form("")) -> dict:
    """Turn one spoken clip (or a supplied transcript) into PatientObject targets.

    ElevenLabs only produces text; which structures that text selects is decided
    locally by `voice.resolve_command`, so the mapping stays deterministic and auditable.
    """
    model = load_model(model_id)
    spoken = transcript.strip()
    if file is not None:
        raw = await file.read()
        if len(raw) > MAX_VOICE_AUDIO_BYTES:
            raise HTTPException(413, "audio clip is larger than 12 MB")
        try:
            vocabulary = [obj.label for obj in model.objects if obj.type != "volume"]
            # Transcription is a second of CPU work; keep it off the event loop so mesh
            # and slice requests are not stalled behind a spoken command.
            spoken = await asyncio.to_thread(
                voice_transcribe,
                raw,
                filename=file.filename or "clip.webm",
                content_type=file.content_type or "audio/webm",
                vocabulary=vocabulary,
            )
        except RuntimeError as error:
            raise HTTPException(502, str(error)) from error
    if not spoken:
        raise HTTPException(400, "no audio clip or transcript supplied")
    result = resolve_command(spoken, model.objects)
    audit_event("voice.command", model_id, {"transcript": result["transcript"], "intent": result["intent"], "targets": result["targets"]})
    return result


@app.get("/api/adapters/status")
def adapters_status() -> dict:
    return adapter_status()


@app.get("/api/dicomweb/capabilities")
def dicomweb_capabilities() -> dict:
    return {**capability_status(), "c_store_receiver": CSTORE_RECEIVER.status() if CSTORE_RECEIVER else {"enabled": False, "running": False}}


@app.get("/api/dicomweb/cstore/status")
def cstore_status() -> dict:
    return CSTORE_RECEIVER.status() if CSTORE_RECEIVER else {"enabled": False, "running": False, "message": "Set PHASEMED_CSTORE_PORT to enable the localhost receiver"}


@app.get("/api/dicomweb/cstore/studies")
def cstore_studies() -> list[dict]:
    incoming = RUNTIME / "incoming"
    if not incoming.exists():
        return []
    results = []
    for candidate in sorted(item for item in incoming.iterdir() if item.is_dir()):
        try:
            study, series = index_directory(candidate, f"cstore-{candidate.name}")
        except ValueError:
            continue
        results.append({**study.model_dump(), "series": series, "staged_path": str(candidate)})
    return results


@app.post("/api/dicomweb/cstore/studies/{study_uid}/import")
def import_cstore_study(study_uid: str) -> dict:
    """Promote one immutable C-STORE staging folder into the local workspace."""
    incoming = (RUNTIME / "incoming").resolve()
    source = (incoming / study_uid).resolve()
    try:
        source.relative_to(incoming)
    except ValueError as exc:
        raise HTTPException(400, "Invalid staged study identifier") from exc
    if not source.is_dir():
        raise HTTPException(404, "Staged C-STORE study not found")
    study_id = f"cstore-{uuid.uuid4().hex[:12]}"
    destination = STUDY_ROOT / study_id
    try:
        shutil.copytree(source, destination)
        study, series = index_directory(destination, study_id)
        save_study(study, series)
        audit_event("study.imported.cstore", study_id, {"study_uid": study_uid, "instance_count": study.image_count})
        return {"study": {**study.model_dump(), "series": series}, "staged_study_uid": study_uid}
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise HTTPException(400, f"Staged C-STORE study could not be imported: {exc}") from exc


@app.get("/api/dicomweb/studies")
def dicomweb_study_list() -> list[dict]:
    try:
        return qido_studies()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/dicomweb/studies/{study_uid}/series")
def dicomweb_series_list(study_uid: str) -> list[dict]:
    try:
        return qido_series(study_uid)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/dicomweb/studies/{study_uid}/series/{series_uid}/instances")
def dicomweb_instance_list(study_uid: str, series_uid: str) -> list[dict]:
    try:
        return qido_instances(study_uid, series_uid)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/dicomweb/studies/{study_uid}/series/{series_uid}/metadata")
def dicomweb_series_metadata(study_uid: str, series_uid: str) -> list[dict] | dict:
    try:
        return wado_metadata(study_uid, series_uid)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/dicomweb/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}")
def dicomweb_instance(study_uid: str, series_uid: str, instance_uid: str) -> Response:
    try:
        return Response(content=wado_instance(study_uid, series_uid, instance_uid), media_type="application/dicom")
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _dicom_json_value(item: dict, tag: str) -> str | None:
    value = item.get(tag) or item.get(tag.upper()) or {}
    values = value.get("Value") if isinstance(value, dict) else None
    return str(values[0]) if values else None


@app.post("/api/dicomweb/studies/{study_uid}/import")
def import_dicomweb_study(study_uid: str) -> dict:
    """Pull one remote DICOMweb study into the immutable local workspace."""
    study_id = f"dicomweb-{uuid.uuid4().hex[:12]}"
    root = STUDY_ROOT / study_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = 0
        for series_index, series_item in enumerate(qido_series(study_uid)):
            series_uid = _dicom_json_value(series_item, "0020000E")
            if not series_uid:
                continue
            for instance_index, instance_item in enumerate(qido_instances(study_uid, series_uid)):
                instance_uid = _dicom_json_value(instance_item, "00080018")
                if not instance_uid:
                    continue
                data = wado_instance(study_uid, series_uid, instance_uid)
                if not data:
                    continue
                destination = root / f"series-{series_index:04d}" / f"instance-{instance_index:06d}.dcm"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                downloaded += 1
        if not downloaded:
            raise ValueError("DICOMweb study contained no downloadable instances")
        study, series = index_directory(root, study_id)
        save_study(study, series)
        audit_event("study.imported.dicomweb", study_id, {"remote_study_uid": study_uid, "instance_count": downloaded})
        return {"study": {**study.model_dump(), "series": series}, "remote_study_uid": study_uid, "downloaded_instances": downloaded}
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        if isinstance(exc, RuntimeError):
            raise HTTPException(503, str(exc)) from exc
        raise HTTPException(400, f"DICOMweb study could not be imported: {exc}") from exc


@app.post("/api/dicomweb/stow")
async def dicomweb_stow(file: UploadFile = File(...)) -> dict:
    try:
        data = await file.read()
        return stow_dicom(data)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/studies")
def list_studies() -> list[dict]:
    conn = db(); rows = conn.execute("SELECT payload FROM studies ORDER BY id DESC").fetchall(); conn.close(); return [json.loads(row["payload"]) for row in rows]


def _demo_studies() -> list[dict]:
    return [study for study in list_studies() if str(study.get("patient_id", "")).startswith("DEMO-")]


def _run_demo_seed(job_id: str, base_url: str) -> None:
    try:
        command = [sys.executable, str(ROOT / "scripts" / "seed_demo_data.py"), "--base-url", base_url]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=900, check=False)
        output = (result.stdout or result.stderr or "").strip().splitlines()
        with DEMO_SEED_LOCK:
            DEMO_SEED_JOBS[job_id] = {
                "id": job_id,
                "status": "completed" if result.returncode == 0 else "failed",
                "message": output[-1] if output else ("Sample workspace ready" if result.returncode == 0 else "Sample workspace could not be prepared"),
                "study_count": len(_demo_studies()),
            }
    except Exception as exc:
        with DEMO_SEED_LOCK:
            DEMO_SEED_JOBS[job_id] = {"id": job_id, "status": "failed", "message": str(exc), "study_count": len(_demo_studies())}


@app.post("/api/demo/seed")
def seed_demo_workspace(request: Request) -> dict:
    existing = _demo_studies()
    if existing:
        return {"job": {"id": "demo-existing", "status": "completed", "message": "Synthetic sample workspace already loaded", "study_count": len(existing)}}
    with DEMO_SEED_LOCK:
        running = next((job for job in DEMO_SEED_JOBS.values() if job["status"] == "running"), None)
        if running:
            return {"job": running}
        job_id = f"demo-{uuid.uuid4().hex[:12]}"
        job = {"id": job_id, "status": "running", "message": "Preparing synthetic DICOM studies…", "study_count": 0}
        DEMO_SEED_JOBS[job_id] = job
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port
    base_url = f"{request.url.scheme}://{host}{f':{port}' if port else ''}"
    threading.Thread(target=_run_demo_seed, args=(job_id, base_url), daemon=True).start()
    return {"job": job}


@app.get("/api/demo/seed/{job_id}")
def demo_seed_status(job_id: str) -> dict:
    with DEMO_SEED_LOCK:
        job = DEMO_SEED_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Demo seed job not found")
    return {"job": job}


@app.post("/api/studies/import")
async def import_study(files: list[UploadFile] = File(...)) -> dict:
    study_id = f"study-{uuid.uuid4().hex[:12]}"
    root = STUDY_ROOT / study_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        for file in files:
            data = await file.read()
            if not data:
                continue
            unpack_upload(data, file.filename or "instance.dcm", root)
        study, series = index_directory(root, study_id)
        save_study(study, series)
        audit_event("study.imported", study_id, {"instance_count": study.image_count, "series_count": study.series_count})
        return {"study": {**study.model_dump(), "series": series}, "message": "DICOM study indexed"}
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise HTTPException(400, "No readable DICOM instances found in the uploaded files")


@app.get("/api/studies/{study_id}")
def study_detail(study_id: str) -> dict:
    return get_study(study_id)


@app.post("/api/studies/{study_id}/compile")
def start_compile(study_id: str, background: BackgroundTasks, rebuild: bool = Query(default=False)) -> dict:
    study = get_study(study_id)
    if study.get("status") == "compiling":
        raise HTTPException(409, "Study compilation is already in progress")
    if not rebuild and study.get("status") == "ready" and _has_persisted_model(study.get("model_id")):
        raise HTTPException(409, "Study is already compiled; review the existing PatientModel")
    study["status"] = "compiling"
    conn = db(); conn.execute("UPDATE studies SET payload=? WHERE id=?", (json.dumps(study), study_id)); conn.commit(); conn.close()
    job = JobState(id=f"job-{uuid.uuid4().hex[:12]}", study_id=study_id)
    save_job(job); background.add_task(compile_in_background, job.id, study_id, rebuild)
    return {"job": job.model_dump()}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JobState:
    return get_job(job_id)


def load_model(model_id: str) -> PatientModel:
    path = model_path(model_id)
    if not path.exists():
        raise HTTPException(404, "PatientModel not found")
    return PatientModel.model_validate_json(path.read_text())


def lab_root() -> Path:
    root = RUNTIME / "algorithms"
    root.mkdir(parents=True, exist_ok=True)
    return root


def lab_path(kind: str, identifier: str) -> Path:
    safe = identifier.replace("/", "_").replace("\\", "_")
    return lab_root() / f"{kind}-{safe}.json"


def save_lab_artifact(kind: str, payload: dict) -> None:
    lab_path(kind, str(payload["id"])).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_lab_artifact(kind: str, identifier: str) -> dict:
    path = lab_path(kind, identifier)
    if not path.exists():
        raise HTTPException(404, f"{kind.title()} not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"Stored {kind} is invalid") from exc


@app.get("/api/model-lab/schema")
def model_lab_schema() -> dict:
    return {
        "feature_schema": FEATURE_SCHEMA,
        "label_formats": {"binary": {"values": [0, 1]}, "regression": {"values": "finite numeric outcomes"}},
        "source": "caller-supplied",
        "algorithms": [
            {"id": "binary-logistic-regression", "task": "binary", "family": "linear", "explainability": "coefficient-contributions"},
            {"id": "linear-regression", "task": "regression", "family": "linear", "explainability": "coefficient-contributions"},
            {"id": "random-forest", "task": "binary", "family": "ensemble", "explainability": "feature-importance-and-tree-votes"},
            {"id": "random-forest", "task": "regression", "family": "ensemble", "explainability": "feature-importance-and-tree-votes"},
        ],
        "capabilities": ["feature-extraction", "dataset-assembly", "cohort-label-import", "cohort-quality-summary", "cohort-drift-analysis", "cohort-clustering", "pca-cohort-projection", "anomaly-ranking", "binary-logistic-regression", "linear-regression", "random-forest-classification", "random-forest-regression", "deterministic-validation", "k-fold-cross-validation", "configuration-search", "batch-inference", "evaluation"],
    }


@app.post("/api/model-lab/datasets")
def create_model_lab_dataset(payload: dict) -> dict:
    requested_ids = payload.get("model_ids")
    labels = payload.get("labels") or {}
    task = str(payload.get("task") or "binary")
    if not isinstance(labels, dict):
        raise HTTPException(400, "labels must be an object keyed by PatientModel id")
    models = []
    if requested_ids is None:
        requested_ids = list(labels)
    if not isinstance(requested_ids, list) or not requested_ids:
        raise HTTPException(400, "model_ids must contain at least one PatientModel id")
    for model_id in requested_ids:
        models.append(load_model(str(model_id)))
    try:
        rows = dataset_rows(models, labels, task=task)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    dataset = {"id": f"dataset-{uuid.uuid4().hex[:12]}", "name": str(payload.get("name") or "Untitled PatientModel dataset"), "task": task, "created_at": now_iso(), "rows": rows, "feature_schema": FEATURE_SCHEMA, "provenance": {"method": "persisted PatientModel feature extraction", "model_count": len(rows), "label_source": "caller-supplied"}}
    save_lab_artifact("dataset", dataset)
    audit_event("model_lab.dataset.created", dataset["id"], {"row_count": len(rows)})
    return dataset


@app.post("/api/model-lab/datasets/import")
async def import_model_lab_dataset(file: UploadFile = File(...), task: str = Form("binary"), name: str = Form("")) -> dict:
    """Import a cohort label file while deriving every feature from local models."""
    if task not in {"binary", "regression"}:
        raise HTTPException(400, "task must be binary or regression")
    raw = await file.read()
    if len(raw) > 50_000_000:
        raise HTTPException(413, "label file is larger than 50 MB")
    try:
        text = raw.decode("utf-8-sig")
        if (file.filename or "").lower().endswith(".json") or text.lstrip().startswith(("[", "{")):
            payload = json.loads(text)
            if isinstance(payload, dict) and isinstance(payload.get("labels"), dict):
                records = [{"model_id": model_id, "label": label} for model_id, label in payload["labels"].items()]
            elif isinstance(payload, list):
                records = payload
            else:
                raise ValueError("JSON must be a list of {model_id, label} rows or an object with a labels map")
        else:
            records = list(csv.DictReader(io.StringIO(text)))
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(400, f"Could not parse label file: {exc}") from exc
    if not records:
        raise HTTPException(400, "label file contains no rows")
    labels: dict[str, Any] = {}
    for index, record in enumerate(records, start=2):
        if not isinstance(record, dict):
            raise HTTPException(400, f"row {index} must be an object")
        model_id = str(record.get("model_id") or record.get("id") or "").strip()
        raw_label = record.get("label", record.get("outcome"))
        if not model_id or raw_label is None or raw_label == "":
            raise HTTPException(400, f"row {index} requires model_id and label")
        if model_id in labels:
            raise HTTPException(400, f"duplicate model_id in row {index}: {model_id}")
        if isinstance(raw_label, str):
            normalized_label = raw_label.strip().lower()
            if task == "binary" and normalized_label in {"true", "yes", "positive"}:
                raw_label = 1
            elif task == "binary" and normalized_label in {"false", "no", "negative"}:
                raw_label = 0
            else:
                try:
                    raw_label = float(raw_label)
                except ValueError as exc:
                    raise HTTPException(400, f"row {index} label must be numeric") from exc
        labels[model_id] = raw_label
    try:
        models = [load_model(model_id) for model_id in labels]
        rows = dataset_rows(models, labels, task=task)
    except (HTTPException, ValueError) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, str(exc)) from exc
    dataset = {"id": f"dataset-{uuid.uuid4().hex[:12]}", "name": name.strip() or (file.filename or "Imported PatientModel cohort"), "task": task, "created_at": now_iso(), "rows": rows, "feature_schema": FEATURE_SCHEMA, "provenance": {"method": "uploaded cohort labels resolved against persisted PatientModels", "model_count": len(rows), "label_source": "caller-supplied-upload", "filename": file.filename or "uploaded-file"}}
    save_lab_artifact("dataset", dataset)
    audit_event("model_lab.dataset.imported", dataset["id"], {"row_count": len(rows), "filename": file.filename or "uploaded-file"})
    return dataset


@app.get("/api/model-lab/datasets")
def list_model_lab_datasets() -> list[dict]:
    results = []
    for path in sorted(lab_root().glob("dataset-*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        results.append({key: payload[key] for key in ("id", "name", "task", "created_at", "feature_schema", "provenance") if key in payload} | {"row_count": len(payload.get("rows", []))})
    return results


@app.get("/api/model-lab/datasets/{dataset_id}")
def model_lab_dataset(dataset_id: str) -> dict:
    return load_lab_artifact("dataset", dataset_id)


@app.get("/api/model-lab/datasets/{dataset_id}/quality")
def model_lab_dataset_quality(dataset_id: str, baseline_dataset_id: str | None = Query(default=None)) -> dict:
    dataset = load_lab_artifact("dataset", dataset_id)
    baseline = load_lab_artifact("dataset", baseline_dataset_id) if baseline_dataset_id else None
    if baseline and baseline.get("task") != dataset.get("task"):
        raise HTTPException(400, "baseline dataset task must match the selected dataset")
    return summarize_dataset(dataset, baseline)


@app.get("/api/model-lab/datasets/{dataset_id}/csv")
def model_lab_dataset_csv(dataset_id: str) -> Response:
    dataset = load_lab_artifact("dataset", dataset_id)
    stream = io.StringIO()
    fields = ["model_id", "patient_id", "study_id", "label", *FEATURE_NAMES]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in dataset.get("rows", []):
        writer.writerow({"model_id": row.get("model_id"), "patient_id": row.get("patient_id"), "study_id": row.get("study_id"), "label": row.get("label"), **(row.get("features") or {})})
    return Response(content=stream.getvalue(), media_type="text/csv", headers={"content-disposition": f'attachment; filename="{dataset_id}.csv"'})


def persisted_models() -> list[PatientModel]:
    models: list[PatientModel] = []
    for path in sorted(MODEL_ROOT.glob("*.json")):
        try:
            models.append(PatientModel.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return models


@app.get("/api/model-lab/features")
def model_lab_features(model_ids: str | None = Query(default=None)) -> dict:
    requested = [item.strip() for item in model_ids.split(",") if item.strip()] if model_ids else None
    available = persisted_models()
    if requested is not None:
        by_id = {model.id: model for model in available}
        missing = [model_id for model_id in requested if model_id not in by_id]
        if missing:
            raise HTTPException(404, f"PatientModel not found: {missing[0]}")
        available = [by_id[model_id] for model_id in requested]
    rows = [{"model_id": model.id, "patient_id": model.patient_id, "study_id": model.study_id, "features": extract_features(model)} for model in available]
    return {"feature_schema": FEATURE_SCHEMA, "rows": rows, "provenance": {"method": "persisted PatientModel feature extraction", "row_count": len(rows)}}


@app.post("/api/model-lab/cohort-analysis")
def model_lab_cohort_analysis(payload: dict) -> dict:
    requested_ids = payload.get("model_ids")
    available = persisted_models()
    if requested_ids is not None:
        if not isinstance(requested_ids, list) or not requested_ids:
            raise HTTPException(400, "model_ids must contain at least one PatientModel id")
        by_id = {model.id: model for model in available}
        missing = [str(model_id) for model_id in requested_ids if str(model_id) not in by_id]
        if missing:
            raise HTTPException(404, f"PatientModel not found: {missing[0]}")
        available = [by_id[str(model_id)] for model_id in requested_ids]
    rows = [{"model_id": model.id, "features": extract_features(model)} for model in available]
    try:
        analysis = analyze_cohort(rows, clusters=int(payload.get("clusters", 3)), seed=int(payload.get("seed", 17)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    save_lab_artifact("cohort-analysis", analysis)
    audit_event("model_lab.cohort.analyzed", analysis["id"], {"row_count": analysis["row_count"], "cluster_count": len(analysis["clusters"])})
    return analysis


@app.get("/api/model-lab/cohort-analysis")
def list_model_lab_cohort_analyses() -> list[dict]:
    results = []
    for path in sorted(lab_root().glob("cohort-analysis-*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        results.append({key: payload[key] for key in ("id", "status", "created_at", "row_count", "parameters", "projection", "clusters", "provenance") if key in payload})
    return results


@app.get("/api/model-lab/cohort-analysis/{analysis_id}")
def model_lab_cohort_analysis_detail(analysis_id: str) -> dict:
    return load_lab_artifact("cohort-analysis", analysis_id)


@app.post("/api/model-lab/train")
def train_model_lab(payload: dict) -> dict:
    dataset_id = str(payload.get("dataset_id") or "")
    if not dataset_id:
        raise HTTPException(400, "dataset_id is required")
    dataset = load_lab_artifact("dataset", dataset_id)
    task = dataset.get("task") or "binary"
    default_algorithm = "linear-regression" if task == "regression" else "binary-logistic-regression"
    algorithm = str(payload.get("algorithm") or default_algorithm)
    try:
        parameters = {key: payload[key] for key in ("iterations", "learning_rate", "l2", "n_estimators", "max_depth", "min_samples_leaf", "max_features", "seed") if key in payload}
        artifact = train_algorithm(rows=dataset.get("rows", []), task=task, algorithm=algorithm, name=str(payload.get("name") or ""), **parameters)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    artifact["training"]["dataset_id"] = dataset_id
    save_lab_artifact("algorithm", artifact)
    audit_event("model_lab.algorithm.trained", artifact["id"], {"dataset_id": dataset_id, "row_count": artifact["training"]["row_count"]})
    return artifact


@app.post("/api/model-lab/search")
def search_model_lab(payload: dict) -> dict:
    """Train a small deterministic configuration sweep and persist its winner."""
    dataset_id = str(payload.get("dataset_id") or "")
    if not dataset_id:
        raise HTTPException(400, "dataset_id is required")
    dataset = load_lab_artifact("dataset", dataset_id)
    task = dataset.get("task") or "binary"
    if task not in {"binary", "regression"}:
        raise HTTPException(400, "dataset task must be binary or regression")
    requested = payload.get("candidates")
    default_algorithm = "linear-regression" if task == "regression" else "binary-logistic-regression"
    candidates = requested if isinstance(requested, list) and requested else (
        [
            {"algorithm": default_algorithm, "iterations": 400, "learning_rate": 0.04, "l2": 0.001},
            {"algorithm": default_algorithm, "iterations": 600, "learning_rate": 0.08, "l2": 0.001},
            {"algorithm": "random-forest", "n_estimators": 24, "max_depth": 5, "min_samples_leaf": 1, "seed": 17},
        ]
        if task == "binary"
        else [
            {"algorithm": default_algorithm, "iterations": 400, "learning_rate": 0.02, "l2": 0.001},
            {"algorithm": default_algorithm, "iterations": 600, "learning_rate": 0.03, "l2": 0.001},
            {"algorithm": "random-forest", "n_estimators": 24, "max_depth": 5, "min_samples_leaf": 1, "seed": 17},
        ]
    )
    if len(candidates) > 12:
        raise HTTPException(400, "at most 12 candidate configurations are allowed")
    results = []
    winner = None
    winner_score = None
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise HTTPException(400, f"candidate {index + 1} must be an object")
        try:
            algorithm = str(candidate.get("algorithm") or default_algorithm)
            parameters = {key: candidate[key] for key in ("iterations", "learning_rate", "l2", "n_estimators", "max_depth", "min_samples_leaf", "max_features", "seed") if key in candidate}
            artifact = train_algorithm(rows=dataset.get("rows", []), task=task, algorithm=algorithm, name=str(payload.get("name") or ""), **parameters)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"candidate {index + 1}: {exc}") from exc
        split = artifact.get("training", {}).get("validation")
        metrics = (split or artifact.get("training", {})).get("metrics", {})
        score = float(metrics.get("rmse", 0.0)) if task == "regression" else float(metrics.get("accuracy", 0.0))
        comparable = -score if task == "regression" else score
        results.append({"candidate": {"algorithm": algorithm, **parameters, "index": index}, "metrics": metrics, "validation": bool(split), "score": round(score, 6), "type": artifact.get("type")})
        if winner is None or comparable > winner_score:
            winner = artifact
            winner_score = comparable
    if winner is None:
        raise HTTPException(400, "at least one candidate configuration is required")
    winner["training"]["dataset_id"] = dataset_id
    has_validation = any(item["validation"] for item in results)
    winner["search"] = {"objective": ("validation_rmse_min" if task == "regression" else "validation_accuracy_max") if has_validation else ("training_rmse_min" if task == "regression" else "training_accuracy_max"), "candidate_count": len(results), "candidates": results, "selected_algorithm_id": winner["id"]}
    save_lab_artifact("algorithm", winner)
    audit_event("model_lab.algorithm.searched", winner["id"], {"dataset_id": dataset_id, "candidate_count": len(results)})
    return winner


@app.get("/api/model-lab/algorithms")
def list_model_lab_algorithms() -> list[dict]:
    results = []
    for path in sorted(lab_root().glob("algorithm-*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        summary = {key: payload[key] for key in ("id", "name", "type", "status", "created_at", "feature_schema", "parameters", "training", "provenance") if key in payload}
        if "search" in payload:
            summary["search"] = {key: payload["search"][key] for key in ("objective", "candidate_count", "selected_algorithm_id") if key in payload["search"]}
        if "feature_importance" in payload:
            summary["feature_importance"] = payload["feature_importance"]
        results.append(summary)
    return results


@app.get("/api/model-lab/algorithms/{algorithm_id}")
def model_lab_algorithm(algorithm_id: str) -> dict:
    return load_lab_artifact("algorithm", algorithm_id)


@app.post("/api/model-lab/algorithms/{algorithm_id}/predict")
def model_lab_predict(algorithm_id: str, payload: dict) -> dict:
    artifact = load_lab_artifact("algorithm", algorithm_id)
    model_id = str(payload.get("model_id") or "")
    if not model_id:
        raise HTTPException(400, "model_id is required")
    return predict_algorithm(artifact, load_model(model_id))


@app.post("/api/model-lab/algorithms/{algorithm_id}/batch-predict")
def model_lab_batch_predict(algorithm_id: str, payload: dict) -> dict:
    artifact = load_lab_artifact("algorithm", algorithm_id)
    model_ids = payload.get("model_ids")
    if model_ids is None:
        model_ids = artifact.get("training", {}).get("model_ids", [])
    if not isinstance(model_ids, list) or not model_ids:
        raise HTTPException(400, "model_ids must contain at least one PatientModel id")
    results = [predict_algorithm(artifact, load_model(str(model_id))) for model_id in model_ids]
    return {"algorithm_id": algorithm_id, "results": results, "provenance": {"algorithm_id": algorithm_id, "row_count": len(results)}}


@app.post("/api/model-lab/algorithms/{algorithm_id}/cross-validate")
def model_lab_cross_validate(algorithm_id: str, payload: dict) -> dict:
    artifact = load_lab_artifact("algorithm", algorithm_id)
    dataset_id = str(payload.get("dataset_id") or artifact.get("training", {}).get("dataset_id") or "")
    if not dataset_id:
        raise HTTPException(400, "dataset_id is required for cross-validation")
    dataset = load_lab_artifact("dataset", dataset_id)
    task = str(dataset.get("task") or "binary")
    type_to_algorithm = {"binary-logistic-regression": "binary-logistic-regression", "linear-regression": "linear-regression", "random-forest-classifier": "random-forest", "random-forest-regressor": "random-forest"}
    algorithm = type_to_algorithm.get(str(artifact.get("type")))
    if not algorithm:
        raise HTTPException(400, "Algorithm artifact cannot be cross-validated")
    parameters = dict(artifact.get("parameters") or {})
    result = cross_validate(rows=dataset.get("rows", []), task=task, algorithm=algorithm, name=str(artifact.get("name") or ""), folds=int(payload.get("folds", 5)), **parameters)
    result["algorithm_id"] = algorithm_id
    result["dataset_id"] = dataset_id
    audit_event("model_lab.algorithm.cross_validated", algorithm_id, {"dataset_id": dataset_id, "fold_count": result["fold_count"]})
    return result


@app.post("/api/model-lab/algorithms/{algorithm_id}/evaluate")
def model_lab_evaluate(algorithm_id: str, payload: dict) -> dict:
    artifact = load_lab_artifact("algorithm", algorithm_id)
    labels = payload.get("labels") or {}
    model_ids = payload.get("model_ids") or list(labels)
    if not isinstance(labels, dict) or not isinstance(model_ids, list) or not model_ids:
        raise HTTPException(400, "model_ids and binary labels are required")
    rows = []
    regression = artifact.get("type") in {"linear-regression", "random-forest-regressor"}
    for model_id in model_ids:
        key = str(model_id)
        if key not in labels:
            raise HTTPException(400, f"Missing label for {key}")
        try:
            label = float(labels[key]) if regression else int(labels[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"Label for {key} must be a finite number" if regression else f"Label for {key} must be 0 or 1") from exc
        if not regression and label not in (0, 1):
            raise HTTPException(400, f"Label for {key} must be 0 or 1")
        if regression and not math.isfinite(label):
            raise HTTPException(400, f"Label for {key} must be a finite number")
        prediction = predict_algorithm(artifact, load_model(key))
        rows.append({"model_id": key, "label": label, "prediction": prediction["prediction"], **({"probability_positive": prediction["probability_positive"]} if not regression else {})})
    if regression:
        actual = [row["label"] for row in rows]
        predicted = [row["prediction"] for row in rows]
        mse = sum((predicted[index] - actual[index]) ** 2 for index in range(len(rows))) / len(rows)
        mean_actual = sum(actual) / len(actual)
        variance = sum((value - mean_actual) ** 2 for value in actual)
        metrics = {"mse": round(mse, 6), "rmse": round(mse ** 0.5, 6), "mae": round(sum(abs(predicted[index] - actual[index]) for index in range(len(rows))) / len(rows), 6), "r2": round(1 - sum((predicted[index] - actual[index]) ** 2 for index in range(len(rows))) / variance, 6) if variance else 0.0}
        return {"algorithm_id": algorithm_id, "metrics": metrics, "rows": rows, "provenance": {"method": "caller-supplied holdout evaluation", "label_source": "caller-supplied", "row_count": len(rows)}}
    true_positive = sum(row["label"] == 1 and row["prediction"] == 1 for row in rows)
    true_negative = sum(row["label"] == 0 and row["prediction"] == 0 for row in rows)
    false_positive = sum(row["label"] == 0 and row["prediction"] == 1 for row in rows)
    false_negative = sum(row["label"] == 1 and row["prediction"] == 0 for row in rows)
    metrics = {"accuracy": round((true_positive + true_negative) / len(rows), 4), "precision": round(true_positive / max(1, true_positive + false_positive), 4), "recall": round(true_positive / max(1, true_positive + false_negative), 4), "true_positive": true_positive, "true_negative": true_negative, "false_positive": false_positive, "false_negative": false_negative}
    return {"algorithm_id": algorithm_id, "metrics": metrics, "rows": rows, "provenance": {"method": "caller-supplied holdout evaluation", "label_source": "caller-supplied", "row_count": len(rows)}}


@app.get("/api/models/{model_id}")
def model_detail(model_id: str) -> PatientModel:
    return load_model(model_id)


def _model_revision_envelopes(model_id: str) -> list[dict]:
    revision_dir = model_revision_root(model_id)
    if not revision_dir.exists():
        return []
    revisions = []
    for path in sorted(revision_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("model_id") == model_id and isinstance(payload.get("snapshot"), dict):
            revisions.append(payload)
    return sorted(revisions, key=lambda item: int(item.get("version", 0)))


@app.get("/api/models/{model_id}/history")
def model_history(model_id: str) -> dict:
    model = load_model(model_id)
    revisions = _model_revision_envelopes(model_id)
    if not revisions:
        revisions = [{
            "id": f"revision:{model.id}:{model.version}",
            "model_id": model.id,
            "version": model.version,
            "reason": "current-canonical-model",
            "created_at": model.created_at,
            "snapshot": model.model_dump(),
        }]
    return {
        "model_id": model.id,
        "current_version": model.version,
        "revisions": [
            {key: revision[key] for key in ("id", "model_id", "version", "reason", "created_at")}
            for revision in revisions
        ],
        "provenance": {"source": "local-patient-model-revision-store", "immutable": True},
    }


@app.get("/api/models/{model_id}/revisions/{version}")
def model_revision(model_id: str, version: int) -> dict:
    load_model(model_id)
    revisions = _model_revision_envelopes(model_id)
    revision = next((item for item in revisions if item.get("version") == version), None)
    if not revision:
        raise HTTPException(404, "PatientModel revision not found")
    return revision


@app.get("/api/patient-models/{model_id}/history")
def patient_model_history(model_id: str) -> dict:
    return model_history(model_id)


@app.get("/api/patient-models/{model_id}")
def patient_model_detail(model_id: str) -> PatientModel:
    """Canonical external-model alias used by downstream applications."""
    return load_model(model_id)


@app.get("/api/models/{model_id}/objects")
def model_objects(model_id: str) -> list[dict]:
    return [obj.model_dump() for obj in load_model(model_id).objects]


@app.get("/api/models/{model_id}/objects/{object_id}")
def object_detail(model_id: str, object_id: str) -> dict:
    model = load_model(model_id); obj = next((item for item in model.objects if item.id == object_id), None)
    if not obj: raise HTTPException(404, "Object not found")
    return obj.model_dump()


@app.get("/api/models/{model_id}/objects/{object_id}/relationships")
def object_relationships(model_id: str, object_id: str) -> list[dict]:
    model = load_model(model_id)
    if not any(item.id == object_id for item in model.objects):
        raise HTTPException(404, "Object not found")
    return [item.model_dump() for item in model.relationships if item.source_object_id == object_id or item.target_object_id == object_id]


@app.get("/api/models/{model_id}/objects/{object_id}/context")
def object_context(model_id: str, object_id: str) -> list[dict]:
    model = load_model(model_id)
    obj = next((item for item in model.objects if item.id == object_id), None)
    if not obj:
        raise HTTPException(404, "Object not found")
    return [item.model_dump() for item in obj.context]


@app.get("/api/models/{model_id}/objects/{object_id}/history")
def object_history(model_id: str, object_id: str) -> list[dict]:
    model = load_model(model_id)
    if not any(item.id == object_id for item in model.objects):
        raise HTTPException(404, "Object not found")
    return [item.model_dump() for item in model.temporal_links if item.source_object_id == object_id or item.target_object_id == object_id]


@app.get("/api/models/{model_id}/objects/{object_id}/neighbors")
def object_neighbors(model_id: str, object_id: str) -> list[dict]:
    model = load_model(model_id)
    if not any(item.id == object_id for item in model.objects):
        raise HTTPException(404, "Object not found")
    index = SpatialIndex(model.objects)
    return nearest(index.candidates_for_object(object_id), object_id, limit=100)


@app.get("/api/models/{model_id}/relationships")
def model_relationships(model_id: str) -> list[dict]:
    return [item.model_dump() for item in load_model(model_id).relationships]


@app.get("/api/patient-models/{model_id}/objects")
def patient_model_objects(model_id: str) -> list[dict]:
    return model_objects(model_id)


@app.get("/api/patient-models/{model_id}/objects/{object_id}")
def patient_model_object_detail(model_id: str, object_id: str) -> dict:
    return object_detail(model_id, object_id)


@app.get("/api/patient-models/{model_id}/objects/{object_id}/relationships")
def patient_model_object_relationships(model_id: str, object_id: str) -> list[dict]:
    return object_relationships(model_id, object_id)


@app.get("/api/patient-models/{model_id}/objects/{object_id}/context")
def patient_model_object_context(model_id: str, object_id: str) -> list[dict]:
    return object_context(model_id, object_id)


@app.get("/api/patient-models/{model_id}/objects/{object_id}/history")
def patient_model_object_history(model_id: str, object_id: str) -> list[dict]:
    return object_history(model_id, object_id)


@app.get("/api/patient-models/{model_id}/objects/{object_id}/graph")
def patient_model_object_graph(model_id: str, object_id: str) -> dict:
    """Return the traversable subgraph around one PatientObject.

    This is intentionally a small, stable graph-query surface for downstream
    applications. It keeps the PatientModel as the source of truth while
    avoiding a graph-database dependency for the local workstation.
    """
    model = load_model(model_id)
    obj = _model_object(model, object_id)
    relationships = [item for item in model.relationships if item.source_object_id == object_id or item.target_object_id == object_id]
    neighbor_ids = {
        item.target_object_id if item.source_object_id == object_id else item.source_object_id
        for item in relationships
    }
    neighbors = [item for item in model.objects if item.id in neighbor_ids]
    ancestors = [
        item for item in model.objects
        if any(link.source_object_id == item.id and link.target_object_id == object_id and link.type in {"contains", "inside"} for link in model.relationships)
    ]
    descendants = [
        item for item in model.objects
        if any(link.source_object_id == object_id and link.target_object_id == item.id and link.type in {"contains", "inside"} for link in model.relationships)
    ]
    prior_ids = {link.target_object_id for link in model.temporal_links if link.source_object_id == object_id}
    prior_versions = [item for item in model.objects if item.id in prior_ids]
    return {
        "object": obj.model_dump(),
        "neighbors": [item.model_dump() for item in neighbors],
        "ancestors": [item.model_dump() for item in ancestors],
        "descendants": [item.model_dump() for item in descendants],
        "relationships": [item.model_dump() for item in relationships],
        "context": [item.model_dump() for item in obj.context],
        "prior_versions": [item.model_dump() for item in prior_versions],
        "provenance": {"model_id": model.id, "method": "patient-model-relationship-traversal"},
    }


@app.post("/api/patient-models/{model_id}/graph-query")
def patient_model_graph_query(model_id: str, payload: dict) -> dict:
    operation = str(payload.get("operation") or "neighbors")
    object_id = str(payload.get("object_id") or "")
    graph = patient_model_object_graph(model_id, object_id)
    if operation not in {"neighbors", "ancestors", "descendants", "relationships", "context", "prior_versions", "subgraph"}:
        raise HTTPException(400, "operation must be neighbors, ancestors, descendants, relationships, context, prior_versions, or subgraph")
    if operation == "subgraph":
        return graph
    return {"operation": operation, "object_id": object_id, "results": graph[operation], "provenance": graph["provenance"]}


@app.get("/api/patient-models/{model_id}/relationships")
def patient_model_relationships(model_id: str) -> list[dict]:
    return model_relationships(model_id)


@app.post("/api/models/{model_id}/objects/{object_id}/review")
def review_object(model_id: str, object_id: str, payload: dict) -> dict:
    model = load_model(model_id)
    obj = next((item for item in model.objects if item.id == object_id), None)
    if not obj:
        raise HTTPException(404, "Object not found")
    status = payload.get("status")
    if status not in {"confirmed", "modified", "rejected"}:
        raise HTTPException(400, "status must be confirmed, modified, or rejected")
    obj.review_status = status
    save_model(model, reason="object.reviewed", bump_version=True)
    audit_event("object.reviewed", object_id, {"model_id": model_id, "status": status})
    return obj.model_dump()


@app.get("/api/models/{model_id}/timeline")
def model_timeline(model_id: str) -> list[dict]:
    model = load_model(model_id)
    timeline = model.timeline
    if not timeline:
        timeline = [
            TimelineEntry(id=f"timeline:{candidate.id}", study_id=candidate.study_id, date=candidate.created_at, label=candidate.study_id, model_id=candidate.id)
            for candidate in sorted(matching_models(model.patient_id), key=lambda item: item.created_at)
        ]
    return [item.model_dump() for item in timeline]


@app.get("/api/patient-models/{model_id}/timeline")
def patient_model_timeline(model_id: str) -> list[dict]:
    return model_timeline(model_id)


@app.get("/api/patient-models/{model_id}/sources")
def patient_model_sources(model_id: str) -> list[dict]:
    return [item.model_dump() for item in load_model(model_id).sources]


@app.get("/api/patient-models/{model_id}/sources/{source_id}")
def patient_model_source_detail(model_id: str, source_id: str) -> dict:
    return source_detail(model_id, source_id)


def _mesh_path_for(model: PatientModel, obj) -> Path | None:
    relative = obj.metadata.get("mesh_path") if obj else None
    if not relative:
        return None
    study_root = STUDY_ROOT / model.study_id
    candidates = [study_root / relative, study_root / "derived" / "adapter-segmentation" / relative]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(study_root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


@app.get("/api/patient-models/{model_id}/objects/{object_id}/mesh")
def patient_model_object_mesh(model_id: str, object_id: str) -> Response:
    model = load_model(model_id)
    obj = _model_object(model, object_id)
    path = _mesh_path_for(model, obj)
    if not path:
        raise HTTPException(404, "Mesh not available for object")
    stat = path.stat()
    # Mesh files are rewritten only when the model is recompiled, so let the browser keep them and revalidate cheaply.
    headers = {
        "Content-Disposition": f'inline; filename="{path.name}"',
        "Cache-Control": "private, max-age=3600, stale-while-revalidate=86400",
        "ETag": f'"{int(stat.st_mtime_ns):x}-{stat.st_size:x}"',
    }
    return Response(content=path.read_bytes(), media_type="text/plain", headers=headers)


@app.post("/api/models/{model_id}/compare")
def compare_model(model_id: str, payload: dict) -> dict:
    current = load_model(model_id)
    prior_id = payload.get("prior_model_id")
    if not prior_id:
        raise HTTPException(400, "prior_model_id is required")
    prior = load_model(prior_id)
    result = compare_models(current, prior)
    save_model(current, reason="temporal.compare", bump_version=True)
    return result


@app.post("/api/models/{model_id}/temporal")
def temporal_query(model_id: str, payload: dict) -> dict:
    model = load_model(model_id)
    operation = payload.get("operation", "history")
    if operation in {"history", "changes"}:
        object_id = payload.get("object_id")
        links = [link.model_dump() for link in model.temporal_links if not object_id or link.source_object_id == object_id or link.target_object_id == object_id]
        return {"operation": operation, "links": links}
    if operation in {"previous", "next"}:
        ordered = sorted(model.timeline, key=lambda entry: (entry.date, entry.id))
        current_index = next((index for index, entry in enumerate(ordered) if entry.model_id == model.id), len(ordered) - 1)
        target_index = current_index + (-1 if operation == "previous" else 1)
        target = ordered[target_index] if 0 <= target_index < len(ordered) else None
        return {"operation": operation, "model": target.model_dump() if target else None}
    prior_id = payload.get("prior_model_id")
    if operation == "compare" and prior_id:
        prior = load_model(prior_id)
        result = compare_models(model, prior)
        save_model(model, reason="temporal.compare", bump_version=True)
        return result
    raise HTTPException(400, "operation must be history, changes, or compare with prior_model_id")


@app.post("/api/models/{model_id}/context-query")
def context_query(model_id: str, payload: dict) -> dict:
    model = load_model(model_id)
    query = str(payload.get("query", "")).lower().strip()
    matches = []
    target_id = payload.get("object_id")
    for binding in model.context_bindings:
        target = next((obj for obj in model.objects if obj.id == binding.target_id), None)
        haystack = f"{binding.source.title} {binding.source.excerpt or ''} {binding.evidence or ''} {target.label if target else ''}".lower()
        if not query or query in haystack:
            score = 1.0 if target_id and binding.target_id == target_id else binding.relevance
            matches.append({"binding": binding.model_dump(), "source": binding.source.model_dump(), "target": target.model_dump() if target else None, "score": round(score, 3)})
    if not matches:
        for source in model.sources:
            haystack = f"{source.title} {source.excerpt or ''}".lower()
            if not query or query in haystack:
                matches.append({"source": source.model_dump(), "target_id": target_id, "score": 0.25})
    matches.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
    return {"query": query, "results": matches}


@app.post("/api/patient-models/{model_id}/context-query")
def patient_model_context_query(model_id: str, payload: dict) -> dict:
    return context_query(model_id, payload)


@app.post("/api/models/{model_id}/context")
def import_context(model_id: str, payload: dict) -> dict:
    model = load_model(model_id)
    items = normalize_context(payload)
    bindings = bind_context(model, items)
    model.context_items.extend(item for item in items if item not in model.context_items)
    model.capabilities["clinical_context"] = "available" if model.context_bindings else "partial"
    save_model(model, reason="context.imported", bump_version=True)
    audit_event("context.imported", model_id, {"item_count": len(items), "binding_count": len(bindings)})
    return {"items": items, "bindings": [item.model_dump() for item in bindings], "model_id": model.id}


@app.post("/api/models/{model_id}/context/{binding_id}/review")
def review_context_binding(model_id: str, binding_id: str, payload: dict) -> dict:
    model = load_model(model_id)
    status = payload.get("status")
    if status not in {"confirmed", "modified", "rejected", "unreviewed"}:
        raise HTTPException(400, "status must be confirmed, modified, rejected, or unreviewed")
    binding = next((item for item in model.context_bindings if item.id == binding_id), None)
    if not binding:
        raise HTTPException(404, "Context binding not found")
    binding.review_status = status
    for obj in model.objects:
        for attached in obj.context:
            if attached.id == binding_id:
                attached.review_status = status
    save_model(model, reason="context.reviewed", bump_version=True)
    audit_event("context.reviewed", binding_id, {"model_id": model_id, "status": status})
    return binding.model_dump()


@app.get("/api/models/{model_id}/sources/{source_id}")
def source_detail(model_id: str, source_id: str) -> dict:
    model = load_model(model_id)
    source = next((item for item in model.sources if item.id == source_id), None)
    if not source:
        raise HTTPException(404, "Source not found")
    bindings = [binding.model_dump() for binding in model.context_bindings if binding.source.id == source_id]
    return {"source": source.model_dump(), "bindings": bindings}


@app.get("/api/studies/{study_id}/volume")
def volume_manifest(study_id: str, series_uid: str | None = Query(default=None)) -> dict:
    study = get_study(study_id)
    candidates = [item for item in study.get("series", []) if str(item.get("modality") or "").upper() not in {"SEG", "SR"}]
    if series_uid:
        series = next((item for item in candidates if item.get("series_instance_uid") == series_uid), None)
        if not series:
            raise HTTPException(404, "Renderable image series not found")
    else:
        series = max(candidates or study.get("series", []), key=lambda item: int(item.get("instance_count") or 0), default=None)
    if not series:
        raise HTTPException(404, "No image series found")
    volume = load_series_volume(STUDY_ROOT / study_id, series)
    shape = list(volume.shape) if volume is not None else [0, int(series.get("rows") or 0), int(series.get("columns") or 0)]
    return {
        "study_id": study_id,
        "series_instance_uid": series["series_instance_uid"],
        "modality": series.get("modality"),
        "description": series.get("description"),
        "shape": shape,
        "frame_count": shape[0] if shape else 0,
        "pixel_spacing": series.get("pixel_spacing"),
        "slice_thickness": series.get("slice_thickness"),
        "instances": series.get("instances", []),
        "renderable": volume is not None,
    }


@app.get("/api/studies/{study_id}/series/{series_uid}/manifest")
def series_manifest(study_id: str, series_uid: str) -> dict:
    study = get_study(study_id)
    series = next((item for item in study.get("series", []) if item["series_instance_uid"] == series_uid), None)
    if not series:
        raise HTTPException(404, "Series not found")
    return {"study_id": study_id, **series}


@app.get("/api/studies/{study_id}/series/{series_uid}/preview")
def series_preview(study_id: str, series_uid: str, frame: int = Query(0, ge=0), window_center: float | None = Query(None), window_width: float | None = Query(None, gt=0)) -> Response:
    study = get_study(study_id)
    series = next((item for item in study.get("series", []) if item["series_instance_uid"] == series_uid), None)
    if not series or not series.get("instances"):
        raise HTTPException(404, "Series preview not found")
    try:
        instance = series["instances"][min(frame, len(series["instances"]) - 1)]
        png = grayscale_png(STUDY_ROOT / study_id, instance, window_center, window_width)
    except Exception as exc:
        raise HTTPException(422, f"DICOM pixels could not be rendered: {exc}") from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/studies/{study_id}/series/{series_uid}/mpr")
def series_mpr(study_id: str, series_uid: str, plane: str = Query("axial"), index: int | None = Query(None, ge=0), window_center: float | None = Query(None), window_width: float | None = Query(None, gt=0)) -> Response:
    study = get_study(study_id)
    series = next((item for item in study.get("series", []) if item["series_instance_uid"] == series_uid), None)
    if not series:
        raise HTTPException(404, "Series not found")
    try:
        png, metadata = volume_plane_png(STUDY_ROOT / study_id, series, plane, index, window_center, window_width)
    except Exception as exc:
        raise HTTPException(422, f"MPR pixels could not be rendered: {exc}") from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store", "X-Phasemed-MPR": json.dumps(metadata)})


@app.post("/api/models/{model_id}/spatial")
def spatial_query(model_id: str, query: SpatialQuery) -> dict:
    model = load_model(model_id); items = model.objects
    index = SpatialIndex(items)
    if query.operation == "nearest":
        if not query.object_id: raise HTTPException(400, "object_id is required")
        return {"operation": query.operation, "results": nearest(index.candidates_for_object(query.object_id), query.object_id), "method": "uniform-grid-broad-phase+bbox-surface-distance"}
    if query.operation == "within_radius":
        if not query.object_id or query.radius_mm is None: raise HTTPException(400, "object_id and radius_mm are required")
        return {"operation": query.operation, "results": within_radius(index.candidates_for_object(query.object_id, query.radius_mm), query.object_id, query.radius_mm), "method": "uniform-grid-broad-phase+bbox-surface-distance"}
    if query.operation == "distance":
        a = next((item for item in items if item.id == query.object_id), None); b = next((item for item in items if item.id == query.target_id), None)
        if not a or not b or not a.geometry or not b.geometry: raise HTTPException(400, "Both objects must have geometry")
        return {"operation": query.operation, "distance_mm": round(distance(a.geometry.centroid, b.geometry.centroid), 3), "surface_distance_mm": round(minimum_surface_distance(a.geometry.bounding_box, b.geometry.bounding_box), 3), "method": "centroid-and-bbox"}
    if query.operation == "minimum_surface_distance":
        a = next((item for item in items if item.id == query.object_id), None); b = next((item for item in items if item.id == query.target_id), None)
        if not a or not b or not a.geometry or not b.geometry: raise HTTPException(400, "Both objects must have geometry")
        return {"operation": query.operation, "distance_mm": round(minimum_surface_distance(a.geometry.bounding_box, b.geometry.bounding_box), 3), "method": "bbox-surface-distance"}
    if query.operation in {"contains", "intersects", "adjacent"}:
        a = next((item for item in items if item.id == query.object_id), None); b = next((item for item in items if item.id == query.target_id), None)
        if not a or not b or not a.geometry or not b.geometry: raise HTTPException(400, "Both objects must have geometry")
        if query.operation == "contains":
            value = contains(a.geometry.bounding_box, b.geometry.bounding_box)
        elif query.operation == "intersects":
            value = intersects(a.geometry.bounding_box, b.geometry.bounding_box)
        else:
            value = adjacent(a.geometry.bounding_box, b.geometry.bounding_box)
        return {"operation": query.operation, "value": value, "method": "bbox-geometry"}
    if query.operation in {"volume", "surface_area"}:
        if not query.object_id: raise HTTPException(400, "object_id is required")
        obj = next((item for item in items if item.id == query.object_id), None)
        if not obj or not obj.geometry: raise HTTPException(400, "Object must have geometry")
        key = "volume_mm3" if query.operation == "volume" else "surface_area_mm2"
        return {"operation": query.operation, "object_id": obj.id, "value": getattr(obj.geometry, key), "unit": "mm3" if key == "volume_mm3" else "mm2", "method": "persisted-geometry"}
    if query.operation == "trajectory":
        if query.start is None or query.end is None: raise HTTPException(400, "start and end are required")
        intersections = trajectory_intersections(query.start, query.end, items)
        return {"operation": query.operation, "length_mm": round(trajectory_length(query.start, query.end), 3), "intersections": intersections, "clearance": [{"object_id": obj.id, "label": obj.label, "clearance_mm": trajectory_clearance(query.start, query.end, obj)} for obj in items if obj.geometry], "method": "segment-aabb"}
    if query.operation == "intersections":
        if query.start is None or query.end is None: raise HTTPException(400, "start and end are required")
        return {"operation": query.operation, "results": trajectory_intersections(query.start, query.end, items)}
    return {"operation": query.operation, "results": []}


@app.post("/api/patient-models/{model_id}/spatial-query")
def patient_model_spatial_query(model_id: str, query: SpatialQuery) -> dict:
    return spatial_query(model_id, query)


@app.post("/api/patient-models/{model_id}/procedure-paths")
def save_procedure_path(model_id: str, payload: dict) -> dict:
    model = load_model(model_id)
    start = payload.get("start")
    end = payload.get("end")
    if not isinstance(start, list) or not isinstance(end, list) or len(start) != 3 or len(end) != 3:
        raise HTTPException(400, "start and end must be three-dimensional points")
    query = SpatialQuery(operation="trajectory", start=tuple(float(value) for value in start), end=tuple(float(value) for value in end))
    result = spatial_query(model_id, query)
    path = {"id": f"path-{uuid.uuid4().hex[:12]}", "start": start, "end": end, "result": result, "created_at": now_iso()}
    model.procedure_paths.append(path)
    save_model(model, reason="procedure_path.created", bump_version=True)
    audit_event("procedure_path.created", path["id"], {"model_id": model_id, "intersection_count": len(result.get("intersections", []))})
    return path


def _model_object(model: PatientModel, object_id: str):
    obj = next((item for item in model.objects if item.id == object_id), None)
    if not obj:
        raise HTTPException(404, "Object not found")
    return obj


@app.post("/api/patient-models/{model_id}/tools")
def patient_model_tool(model_id: str, payload: dict) -> dict:
    """Deterministic tool surface for agents and local AI adapters.

    The endpoint returns structured results plus provenance. It does not call
    an LLM and remains useful when an external model is disabled.
    """
    model = load_model(model_id)
    name = str(payload.get("tool") or payload.get("name") or "").strip()
    args = payload.get("arguments") or payload.get("input") or {}
    if name == "find_object":
        query = str(args.get("query") or "").lower().strip()
        results = [{"object": item.model_dump(), "score": 1.0 if query == item.label.lower() else 0.7} for item in model.objects if not query or query in item.label.lower() or query in item.id.lower()]
        return {"tool": name, "results": results, "provenance": {"model_id": model.id}}
    if name in {"get_object", "focus_object", "isolate_object", "show_neighbors", "show_prior", "get_measurements", "get_context", "get_sources", "get_relationships", "get_changes", "get_prior_version"}:
        object_id = str(args.get("object_id") or "")
        obj = _model_object(model, object_id) if object_id else None
        if name == "get_object":
            return {"tool": name, "object": obj.model_dump(), "provenance": {"model_id": model.id}}
        if name == "get_measurements":
            measurements = {"centroid": obj.geometry.centroid if obj and obj.geometry else None, "volume_mm3": obj.geometry.volume_mm3 if obj and obj.geometry else None, "surface_area_mm2": obj.geometry.surface_area_mm2 if obj and obj.geometry else None, "observations": [item.model_dump() for item in obj.observations] if obj else []}
            return {"tool": name, "object_id": object_id, "measurements": measurements, "provenance": {"model_id": model.id, "method": "persisted geometry and observations"}}
        if name == "get_relationships":
            return {"tool": name, "object_id": object_id, "relationships": [item.model_dump() for item in model.relationships if item.source_object_id == object_id or item.target_object_id == object_id], "provenance": {"model_id": model.id}}
        if name in {"get_context", "get_sources"}:
            bindings = [item.model_dump() for item in model.context_bindings if item.target_id == object_id] if name == "get_context" else [item.model_dump() for item in model.sources]
            return {"tool": name, "object_id": object_id, "results": bindings, "provenance": {"model_id": model.id}}
        if name == "get_changes":
            return {"tool": name, "object_id": object_id, "links": [item.model_dump() for item in model.temporal_links if item.source_object_id == object_id or item.target_object_id == object_id], "provenance": {"model_id": model.id}}
        if name == "get_prior_version":
            prior_id = model.metadata.get("prior_model_id")
            if not prior_id:
                return {"tool": name, "object_id": object_id, "prior": None, "provenance": {"model_id": model.id}}
            prior = load_model(prior_id)
            prior_obj = next((item for item in prior.objects if item.id == (obj.temporal_links[0].target_object_id if obj and obj.temporal_links else object_id)), None)
            return {"tool": name, "object_id": object_id, "prior": prior_obj.model_dump() if prior_obj else None, "prior_model_id": prior.id, "provenance": {"model_id": model.id}}
        if name == "show_neighbors":
            return {"tool": name, "object_id": object_id, "results": nearest(SpatialIndex(model.objects).candidates_for_object(object_id), object_id, limit=100), "action": "highlight-neighbors", "provenance": {"model_id": model.id, "method": "uniform-grid-broad-phase+bbox-surface-distance"}}
        return {"tool": name, "object_id": object_id, "action": name.replace("_", "-"), "provenance": {"model_id": model.id}}
    if name in {"get_neighbors", "nearest_objects", "query_radius"}:
        object_id = str(args.get("object_id") or "")
        if name == "query_radius":
            radius = float(args.get("radius_mm", 20))
            results = within_radius(SpatialIndex(model.objects).candidates_for_object(object_id, radius), object_id, radius)
        else:
            results = nearest(SpatialIndex(model.objects).candidates_for_object(object_id), object_id, limit=int(args.get("limit", 10)))
        return {"tool": name, "results": results, "provenance": {"model_id": model.id, "method": "bbox-surface-distance"}}
    if name == "measure_distance":
        query = SpatialQuery(operation="distance", object_id=args.get("object_id"), target_id=args.get("target_id"))
        return {"tool": name, **spatial_query(model_id, query), "provenance": {"model_id": model.id}}
    if name == "query_path":
        query = SpatialQuery(operation="trajectory", start=args.get("start"), end=args.get("end"))
        return {"tool": name, **spatial_query(model_id, query), "provenance": {"model_id": model.id}}
    raise HTTPException(400, f"Unknown PatientModel tool: {name}")


@app.get("/api/graph/{model_id}")
def graph_export(model_id: str) -> dict:
    model = load_model(model_id)
    return {
        "@context": {"object": "https://phasemed.local/patient-object", "relationship": "https://phasemed.local/relationship"},
        "@id": model.id,
        "patientId": model.patient_id,
        "objects": [item.model_dump() for item in model.objects],
        "relationships": [item.model_dump() for item in model.relationships],
        "temporalLinks": [item.model_dump() for item in model.temporal_links],
        "contextBindings": [item.model_dump() for item in model.context_bindings],
        "sources": [item.model_dump() for item in model.sources],
    }


@app.get("/api/export/{model_id}")
def export_model(model_id: str) -> JSONResponse:
    return JSONResponse(load_model(model_id).model_dump())


@app.get("/api/patient-models/{model_id}/export")
def patient_model_export(model_id: str) -> JSONResponse:
    return export_model(model_id)


def _trial_run_path(run_id: str) -> Path:
    safe = "".join(character for character in run_id if character.isalnum() or character in "-_")
    return TRIAL_ROOT / f"{safe}.json"


@app.post("/api/trial-studio/simulate")
def trial_studio_simulate(payload: dict) -> dict:
    """Run a seeded trial-planning simulation and persist its audit artifact."""
    try:
        result = trial_simulate(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run_id = f"trial-{uuid.uuid4().hex[:12]}"
    result["run_id"] = run_id
    TRIAL_ROOT.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(_trial_run_path(run_id), json.dumps(result, indent=2))
    audit_event("trial.simulated", run_id, {"endpoint": result["design"]["endpoint"], "seed": result["design"]["seed"], "simulations": result["design"]["simulations"]})
    return result


@app.get("/api/trial-studio/runs")
def trial_studio_runs() -> list[dict]:
    runs = []
    for path in sorted(TRIAL_ROOT.glob("trial-*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            runs.append({"run_id": result.get("run_id", path.stem), "title": result.get("design", {}).get("title"), "endpoint": result.get("design", {}).get("endpoint"), "achieved_power": result.get("operating_characteristics", {}).get("achieved_power"), "created_at": path.stat().st_mtime})
        except (OSError, json.JSONDecodeError):
            continue
    return runs[:50]


@app.get("/api/trial-studio/runs/{run_id}")
def trial_studio_run(run_id: str) -> dict:
    path = _trial_run_path(run_id)
    if not path.exists():
        raise HTTPException(404, "Trial simulation not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, "Trial simulation artifact is invalid") from exc


@app.post("/api/trial-studio/randomize")
def trial_studio_randomize(payload: dict) -> dict:
    try:
        result = trial_randomize(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit_event("trial.randomized", f"randomization:{result['seed']}", {"participant_count": len(result["assignments"]), "seed": result["seed"]})
    return result


@app.get("/{path:path}")
def frontend(path: str = "") -> FileResponse:
    if not path:
        return FileResponse(WEB_ROOT / "landing.html")
    if path.rstrip("/") in {"workspace", "workstation"}:
        return FileResponse(WEB_ROOT / "index.html")
    if path.rstrip("/") in {"trial-studio", "trial"}:
        return FileResponse(WEB_ROOT / "trial-studio.html")
    candidate = (WEB_ROOT / path).resolve() if not path.startswith("api/") else WEB_ROOT / "index.html"
    try:
        candidate.relative_to(WEB_ROOT.resolve())
    except ValueError:
        candidate = WEB_ROOT / "index.html"
    target = candidate if candidate.exists() and candidate.is_file() else WEB_ROOT / "index.html"
    return FileResponse(target)
