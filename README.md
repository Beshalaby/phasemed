# Phasmed

Phasmed is a local-first medical imaging workstation built around a persistent PatientModel: imported DICOM studies are indexed, compiled into source-volume objects, measured deterministically, and exposed to a viewer and model API.

The name is a coined blend of Greek *anatē* (structure/form) and *topos* (place), reflecting anatomy mapped into patient-specific space. It is a working product name, not a trademark or domain clearance claim.

## Run locally

The application is standalone and does not use ChatGPT Sites or a hosted deployment. The source is versioned in the Phasmed GitHub repository for collaboration and hackathon delivery.

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

The same optional stack includes a SimpleITK registration runner. It writes a measured Euler3D rigid transform by default, or a BSpline transform when `PHASEMED_REGISTRATION_MODE=deformable`. Completed rigid results are applied to prior geometry during temporal matching and retained as provenance-bearing JSON on the PatientModel; deformable results remain provenance-only until a displacement field can be persisted and applied.

## Demo data

From the workstation's empty state, choose **Explore sample workspace** to load the synthetic studies in one guided flow. The workspace opens the Rivera follow-up by default so the Model, Review, Change, Context, and Procedure surfaces are immediately available; sample studies are marked `SAMPLE` in the library.

With the server running, populate the workstation with seven synthetic studies: four chest CT studies (including a baseline/follow-up pair), plus lower-leg, arm, and brain studies:

```bash
./.venv/bin/python scripts/seed_demo_data.py
```

If the original chest samples are already loaded, add just the extra anatomy studies with:

```bash
./.venv/bin/python scripts/seed_extra_models.py
```

The studies are procedurally generated phantoms, not patient data. Chest organ shapes are voxelized from the BodyParts3D atlas; the lower-leg, arm, and brain fixtures use explicit analytic geometry. Every study is packaged as a CT series plus a DICOM SEG and goes through the normal import and compile endpoints, so the resulting PatientModels, meshes, relationships, temporal links, and context bindings come from the real pipeline. The first chest seed can download about 70 MB of atlas meshes into `.runtime/atlas/bodyparts3d/` (git-ignored, never committed); later runs work offline (`--offline` enforces that). If the download fails, the chest seeder falls back to an analytic ellipsoid phantom.

To reseed from scratch, stop the server and remove `.runtime/studies`, `.runtime/models`, and `.runtime/phasemed.sqlite3`; keep `.runtime/atlas` to avoid downloading again.

Anatomy credit: BodyParts3D, © The Database Center for Life Science, licensed under [CC Attribution-Share Alike 2.1 Japan](https://creativecommons.org/licenses/by-sa/2.1/jp/).

Landing page typefaces (self-hosted in `web/fonts/` so the page works offline): Fraunces and IBM Plex Sans/Mono, both under the SIL Open Font License 1.1; the license texts sit alongside the font files.

## Hologram voice control

The hologram view has a hold-to-talk control: hold `Space` (or hold the button) and speak,
release to send. Three kinds of command are understood:

- **Structures** — "highlight right lung", "show me the trachea", "highlight rib four on
  the right", "highlight t five". Matched structures paint red.
- **Several at once** — "highlight right lung and spine", "highlight the heart and the
  aorta". Each clause resolves on its own, so a side in one cannot leak into the other.
- **Kinds of object** — "highlight abnormalities" (reviewed findings and lesions, falling
  back to the compiler's unlabeled regions), "highlight everything".
- **The view** — "zoom in", "zoom out", "reset the view", "stop spinning", "start
  rotating". These move the camera and select nothing.

"clear the highlight" removes the colour again.

Transcription runs locally and needs no API key. It is included in the main
`requirements.txt`; existing virtual environments can add it with:

```bash
./.venv/bin/pip install -r requirements.txt
```

The first command downloads the Whisper model (`base.en`, about 150 MB) into the usual
Hugging Face cache, or into `PHASEMED_WHISPER_CACHE_DIR` when set; afterwards it runs
offline on the CPU in well under a second per clip. The recorded audio is decoded in
process and never leaves the machine. The decoder is conditioned on the open
PatientModel's own label vocabulary, so anatomy names are recognised far more reliably
than with a general prompt.

An ElevenLabs key (`PHASEMED_ELEVENLABS_API_KEY`) is an optional fallback for hosts that
would rather not carry the model files; `PHASEMED_STT_PROVIDER` pins the engine. What the
transcript *means* is never decided by a model: `backend/voice.py` maps the text onto
PatientObject ids with explicit, testable rules and returns the trace it used.

## Chat assistant

The inspector's **Chat** tab answers questions about the open PatientModel — "what changed since the prior study?", "what is closest to the nodule?" — and flags the structures it describes in the 3D view. It sits beside the scene rather than replacing it, so a flag is visible as it lands.

It is optional. Without a key the tab answers with the built-in deterministic keyword queries about the selected object. To enable the language model:

```bash
cp .env.example .env && chmod 600 .env      # .env is git-ignored
# set PHASEMED_OPENAI_API_KEY=... in .env, then restart the server
```

How it stays grounded: the language model never produces a measurement. It receives a compact digest of the PatientModel (about 5 KB for the demo study, against a 196 KB raw export) and a small set of tools that run the same deterministic bounding-box geometry as the rest of the workstation (`backend/geometry.py`). Every tool result carries its `method`, and the answer's "How this was answered" disclosure lists the calls and returned values. The assistant can flag or select objects and change view; it cannot write to the PatientModel, change a review status, or create a revision. It is instructed to describe geometry and measured change and not to diagnose, stage, or recommend treatment.

What leaves the machine when a key is configured: object labels and types, measured geometry, bounding-box relationships, measured change against the prior study, and — unless `PHASEMED_ASSISTANT_SHARE_CONTEXT=off` — the text of imported clinical context. Never sent: images, the patient name or id, DICOM UIDs (objects are renamed `O1`, `O2`, … before anything is serialised), absolute dates, file paths, adapter logs. Requests use `store: false`. De-identification of free text is pattern-based and best effort; a final check refuses to send a payload that still contains the patient id or a UID. Each turn is written to the local audit trail as `assistant.answered` without the answer text, the digest, or the key.

First run with a real key — worth checking once: the configured model id is accepted (`model_not_found` is reported in the Chat tab if not), and a question that needs a tool ("what changed?") completes, which exercises the echo of reasoning items that `store: false` requires.

`backend/assistant.py` holds the digest, tools, transport and agent loop; `POST /api/patient-models/{model_id}/assistant` streams newline-delimited JSON events (`status`, `delta`, `action`, `done`, `error`).

## Camera gestures

Open the dedicated hologram display with `Display` in the workstation command bar;
that tab starts browser hand tracking automatically and owns the webcam. The browser
requests webcam permission when the display tab starts; video and landmarks stay in the
browser. The hand-tracking runtime and model are fetched on demand from the MediaPipe
CDN, so an internet connection is needed the first time gesture control is enabled.

Opening a PatientModel from the workspace now reuses the named hologram tab when it is
already open, or opens it when it is missing. The display receives the model immediately
and starts its camera again for each newly opened model.

The main tab also has an optional diagnostic camera: click its camera button to see the
mirrored preview and the currently detected action. `Hide preview` hides the video but
keeps gesture detection running; `Show preview` restores it. The gesture guide below
the preview explains the pose, movement, and result for each control.

- Pinch and move: orbit the model or hologram.
- Pinch with both hands and move them apart/together: zoom.
- Point and hold: select a visible structure, or place a procedure-path point.
- Two fists and drag down/up: scrub through the axial scan layers; both tabs show a scan preview.
- Swipe left/right: move through Model, Review, Change, Context, Procedure, and Hologram.
- Peace sign: cycle anatomy; move it up/down to reverse the direction.
- Hold a fist: fit the current view.

The control layer uses the same camera state as mouse and keyboard interaction. The
dedicated display keeps its webcam active while it is left open; closing that tab or
turning its camera off stops local tracking.

### Hologram display tab

Open the `Display` control once to create the dedicated same-browser hologram tab and
leave it open. That tab owns the gesture camera and starts it automatically; the main
tab remains the control surface and sends the current PatientModel, selection, filters,
and highlights through a local `BroadcastChannel`. When a model is opened or compiled,
the open hologram tab switches to it immediately. No video, network relay, or second
computer is required.

## Current product flow

1. Open the local workstation.
2. Import DICOM files, a folder, or a ZIP containing a study.
3. The backend validates and indexes readable DICOM instances into a local SQLite record and immutable study folder.
4. The compiler emits real stage events, reconstructs a source volume, extracts pixel-derived connected regions, accepts supplied DICOM SEG objects, builds a uniform-grid spatial index, calculates relationships, and persists a PatientModel JSON artifact.
5. The workstation opens the model for object selection, source-image MPR, geometry, relationships, evidence, review, timeline, procedure-path, hologram, and deterministic model queries.
6. Import FHIR/plain context; normalized records retain raw provenance and bind to the most specific matching object available.
7. Export the active PatientModel as JSON or JSON-LD-compatible graph data for downstream services.

## Regeneron HackMIT track: Trial Studio

Phasemed also includes a working clinical-trial planning and biostatistics surface at
`/trial-studio`. It is designed around a real early-development bottleneck: making
sample-size and operating-characteristic assumptions explicit before a team recruits
patients. The workflow supports binary response, continuous, and time-to-event
endpoints; two-arm allocation; attrition adjustment; seeded Monte Carlo power
simulation; an interim information look; synthetic cohort generation; reproducible
randomization; and JSON, CSV, and statistician-handoff report export.

Run the app and open [http://127.0.0.1:8787/trial-studio](http://127.0.0.1:8787/trial-studio),
then choose **Load demo** and **Run simulation**. Every result includes the declared
assumptions, planning formula, random seed, simulation count, Monte Carlo interval,
synthetic cohort preview, warnings, and provenance. Simulation artifacts are retained
locally under `.runtime/trial-runs/` and can be reopened through the Trial Studio API.

Trial Studio API endpoints:

- `POST /api/trial-studio/simulate` — validate a design and run a seeded simulation.
- `POST /api/trial-studio/randomize` — reproducibly assign synthetic participant IDs.
- `GET /api/trial-studio/runs` and `GET /api/trial-studio/runs/{run_id}` — inspect saved runs.

The implementation is a transparent planning aid, not a validated statistical package.
It does not make clinical decisions, infer patient outcomes, or replace a prespecified
statistical analysis plan. A statistician must review the estimand, multiplicity,
missing-data, censoring, subgroup, and interim-decision rules before real use.

### Presenter guide

The product walkthrough is documented separately in
[`docs/PRESENTER_GUIDE.md`](docs/PRESENTER_GUIDE.md). It explains what each screen is
for, the shortest reliable demo path, what happens behind each control, and which
claims are safe to make. The UI stays focused on the actual clinical workflow.

## Architecture

- `backend/` — FastAPI application, DICOM indexing, compiler, PatientModel schemas, geometry engine, SQLite persistence, and spatial query endpoints.
- `web/` — standalone Apple-like clinical workstation UI served by the backend; it is not a hosted ChatGPT Site.
- `tests/` — real pydicom fixture generation, DICOM indexing/compiler tests, and geometry tests.
- `.runtime/` — local-only study data, SQLite state, and compiled model artifacts; never commit this directory.

## API surface

- `GET /api/health`
- `GET /api/audit-events?subject=...` and `/api/models/{model_id}/audit` — bounded, newest-first local audit trail
- `GET /api/studies`
- `POST /api/studies/import` — multipart DICOM files or ZIP
- `GET /api/studies/{study_id}`
- `GET /api/studies/{study_id}/volume` — largest renderable series by default; pass `series_uid` to select a specific series
- `GET /api/studies/{study_id}/series/{series_uid}/mpr`
- `POST /api/studies/{study_id}/compile`
- `GET /api/jobs/{job_id}`
- `GET /api/models/{model_id}`
- `GET /api/models/{model_id}/history` and `/revisions/{version}` — immutable PatientModel revision history and snapshots
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
- `POST /api/patient-models/{model_id}/assistant` — chat about the model; NDJSON stream, 503 when no key is configured
- `GET /api/model-lab/schema` — stable PatientModel feature schema and algorithm capabilities
- `POST /api/model-lab/datasets` — assemble a labeled feature dataset from persisted PatientModels
- `POST /api/model-lab/datasets/import` — import a CSV/JSON label cohort while resolving features from persisted PatientModels
- `GET /api/model-lab/features` — produce a bulk feature table for all or selected PatientModels
- `POST /api/model-lab/cohort-analysis` — project, cluster, and rank anomalies across unlabeled PatientModels
- `GET /api/model-lab/cohort-analysis` and `GET /api/model-lab/cohort-analysis/{analysis_id}` — inspect persisted cohort analyses
- `GET /api/model-lab/datasets/{dataset_id}/csv` — export a feature dataset for external analysis
- `GET /api/model-lab/datasets/{dataset_id}/quality` — summarize cohort distributions and optional baseline drift
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
for classification or regression, plus unlabeled standardized PCA projection,
deterministic k-means cohort grouping, and anomaly ranking. Every run persists its algorithm
configuration, training rows, deterministic validation metrics, feature
importance, tree structure where applicable, and per-prediction explanations.
The workstation can also run cohort-wide inference and deterministic
cross-validation against a persisted dataset, with the selected model,
dataset, fold assignments, and metrics retained as inspectable artifacts.

## Honest capability boundary

The local pipeline performs real DICOM parsing, multi-series indexing, spacing-aware source-volume geometry, axial/coronal/sagittal source rendering with DICOM rescale/window-level handling, pixel statistics where the transfer syntax is readable, connected-component region extraction (kept explicitly unlabeled), DICOM SEG labelmap ingestion into reviewable PatientObjects, exposed-voxel OBJ surface meshes, persisted surface-area measurements, uniform-grid spatial queries, exact AABB trajectory checks, optional DICOMweb integration, opt-in localhost C-STORE receiving, and a provenance-bearing context binding path. Validated model-generated anatomy and longitudinal registration run through explicit adapter commands when configured: `PHASEMED_SEGMENTATION_COMMAND` must write DICOM SEG output to `{output_dir}`, and `PHASEMED_REGISTRATION_COMMAND` must write JSON to `{output_json}`. Rigid Euler3D registration is applied to temporal geometry; deformable registration remains partial until its displacement field is persisted and applied. Until then those capabilities remain partial/unconfigured; the product never fabricates them. There is no sample patient in the initial workspace.

This is a software prototype and must not be used for clinical decisions.
