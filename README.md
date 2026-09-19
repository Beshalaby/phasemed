# Phasemed

Phasemed is a local-first medical imaging workstation built around a persistent PatientModel: imported DICOM studies are indexed, compiled into source-volume objects, measured deterministically, and exposed to a viewer and model API.

The name is a coined blend of Greek *anatē* (structure/form) and *topos* (place), reflecting anatomy mapped into patient-specific space. It is a working product name, not a trademark or domain clearance claim.

## Run locally

The application is standalone and does not use ChatGPT Sites or a hosted deployment. The source is versioned in the Phasemed GitHub repository for collaboration and hackathon delivery.

Set `PHASEMED_RUNTIME_DIR` when the local studies/models database should live somewhere other than `.runtime` in the project folder.

```bash
./.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787) for the product pitch, or [http://127.0.0.1:8787/workspace](http://127.0.0.1:8787/workspace) for the imaging workstation.

The first run can be prepared with:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Optional adapter configuration is documented in `.env.example`. The local UI remains functional without those integrations; configured segmentation commands must emit DICOM SEG, and configured registration commands must emit JSON through the declared output placeholders.

For a local model-generated anatomy run, install `requirements-clinical.txt` and configure the TotalSegmentator command shown in `.env.example`. The runner selects the largest non-SEG image series and emits a real DICOM SEG that the compiler ingests as reviewable PatientObjects.

The same optional stack includes a SimpleITK registration runner. It writes a measured Euler3D rigid transform by default, or a BSpline transform when `PHASEMED_REGISTRATION_MODE=deformable`; both results remain provenance-bearing JSON artifacts attached to the PatientModel.

## Current product flow

1. Open the local workstation.
2. Import DICOM files, a folder, or a ZIP containing a study.
3. The backend validates and indexes readable DICOM instances into a local SQLite record and immutable study folder.
4. The compiler emits real stage events, reconstructs a source volume, extracts pixel-derived connected regions, accepts supplied DICOM SEG objects, builds a uniform-grid spatial index, calculates relationships, and persists a PatientModel JSON artifact.
5. The workstation opens the model for object selection, source-image MPR, geometry, relationships, evidence, review, timeline, procedure-path, hologram, and deterministic model queries.
6. Import FHIR/plain context; normalized records retain raw provenance and bind to the most specific matching object available.
7. Export the active PatientModel as JSON or JSON-LD-compatible graph data for downstream services.

## Architecture

- `backend/` — FastAPI application, DICOM indexing, compiler, PatientModel schemas, geometry engine, SQLite persistence, and spatial query endpoints.
- `web/` — standalone Apple-like clinical workstation UI served by the backend; it is not a hosted ChatGPT Site.
- `tests/` — real pydicom fixture generation, DICOM indexing/compiler tests, and geometry tests.
- `.runtime/` — local-only study data, SQLite state, and compiled model artifacts; never commit this directory.

## API surface

- `GET /api/health`
- `GET /api/studies`
- `POST /api/studies/import` — multipart DICOM files or ZIP
- `GET /api/studies/{study_id}`
- `GET /api/studies/{study_id}/volume`
- `GET /api/studies/{study_id}/series/{series_uid}/mpr`
- `POST /api/studies/{study_id}/compile`
- `GET /api/jobs/{job_id}`
- `GET /api/models/{model_id}`
- `GET /api/models/{model_id}/objects`
- `GET /api/models/{model_id}/objects/{object_id}`
- `GET /api/models/{model_id}/objects/{object_id}/context`
- `GET /api/models/{model_id}/objects/{object_id}/history`
- `GET /api/patient-models/{model_id}/objects/{object_id}/graph` — object subgraph traversal
- `GET /api/patient-models/{model_id}/objects/{object_id}/mesh` — persisted OBJ surface mesh when available
- `GET /api/models/{model_id}/relationships`
- `POST /api/models/{model_id}/spatial` — nearest, radius, distance, trajectory
- `POST /api/models/{model_id}/temporal` — history, changes, compare
- `POST /api/models/{model_id}/context` — normalize and bind FHIR/plain JSON context
- `POST /api/models/{model_id}/context-query`
- `POST /api/models/{model_id}/context/{binding_id}/review`
- `GET /api/graph/{model_id}` — JSON-LD-compatible graph export
- `GET /api/export/{model_id}`
- `POST /api/patient-models/{model_id}/tools` — deterministic downstream AI tool surface
- `GET /api/model-lab/schema` — stable PatientModel feature schema and algorithm capabilities
- `POST /api/model-lab/datasets` — assemble a labeled feature dataset from persisted PatientModels
- `POST /api/model-lab/datasets/import` — import a CSV/JSON label cohort while resolving features from persisted PatientModels
- `GET /api/model-lab/features` — produce a bulk feature table for all or selected PatientModels
- `GET /api/model-lab/datasets/{dataset_id}/csv` — export a feature dataset for external analysis
- `POST /api/model-lab/train` — train an auditable local classification or regression algorithm from a dataset
- `POST /api/model-lab/search` — compare a bounded, reproducible configuration sweep and persist the selected algorithm
- `POST /api/model-lab/algorithms/{algorithm_id}/predict` — explain one prediction with feature contributions
- `POST /api/model-lab/algorithms/{algorithm_id}/batch-predict` — run the same algorithm across many PatientModels
- `POST /api/model-lab/algorithms/{algorithm_id}/cross-validate` — run deterministic, label-aware k-fold validation against the source cohort
- `POST /api/model-lab/algorithms/{algorithm_id}/evaluate` — score an algorithm against caller-supplied evaluation labels
- `POST /api/patient-models/{model_id}/spatial-query`
- `POST /api/patient-models/{model_id}/graph-query` — neighbors, ancestors, descendants, context, and prior-version traversal
- `GET /api/dicomweb/capabilities`
- `GET /api/dicomweb/studies` — QIDO-RS when `PHASEMED_DICOMWEB_URL` or `ORTHANC_URL` is configured
- `GET /api/dicomweb/studies/{study_uid}/series/{series_uid}/metadata` — WADO-RS metadata proxy
- `GET /api/dicomweb/studies/{study_uid}/series/{series_uid}/instances` — QIDO-RS instance listing
- `GET /api/dicomweb/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}` — WADO-RS instance retrieval
- `POST /api/dicomweb/studies/{study_uid}/import` — pull a remote study into the local compiler workspace
- `POST /api/dicomweb/stow` — STOW-RS proxy when configured
- `GET /api/dicomweb/cstore/status` — opt-in local C-STORE receiver status
- `GET /api/dicomweb/cstore/studies` — inspect staged C-STORE studies
- `POST /api/dicomweb/cstore/studies/{study_uid}/import` — promote a staged C-STORE study into the local compiler workspace
- `GET /api/adapters/status` — configured segmentation and registration adapter status

The model lab is deliberately downstream of the clinical object compiler. It
extracts a stable numeric feature vector from PatientObjects, geometry,
relationships, context bindings, meshes, temporal changes, and compiled
source-image statistics. Labels are caller-supplied and retained in dataset
provenance; the local workbench never turns an imaging heuristic into a
clinical label automatically. It supports transparent NumPy logistic
classification, linear regression, and deterministic bootstrap random forests
for classification or regression. Every run persists its algorithm
configuration, training rows, deterministic validation metrics, feature
importance, tree structure where applicable, and per-prediction explanations.
The workstation can also run cohort-wide inference and deterministic
cross-validation against a persisted dataset, with the selected model,
dataset, fold assignments, and metrics retained as inspectable artifacts.

## Honest capability boundary

The local pipeline performs real DICOM parsing, multi-series indexing, spacing-aware source-volume geometry, axial/coronal/sagittal source rendering with DICOM rescale/window-level handling, pixel statistics where the transfer syntax is readable, connected-component region extraction (kept explicitly unlabeled), DICOM SEG labelmap ingestion into reviewable PatientObjects, exposed-voxel OBJ surface meshes, persisted surface-area measurements, uniform-grid spatial queries, exact AABB trajectory checks, optional DICOMweb integration, opt-in localhost C-STORE receiving, and a provenance-bearing context binding path. Validated model-generated anatomy and deformable longitudinal registration run through explicit adapter commands when configured: `PHASEMED_SEGMENTATION_COMMAND` must write DICOM SEG output to `{output_dir}`, and `PHASEMED_REGISTRATION_COMMAND` must write JSON to `{output_json}`. Until then those capabilities remain partial/unconfigured; the product never fabricates them. There is no sample patient in the initial workspace.

This is a software prototype and must not be used for clinical decisions.
