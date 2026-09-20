# Phasemed presenter guide

This is the guide for the person presenting Phasemed. It is intentionally written in
plain language: learn the story first, then use the controls that support that story.

## The one-sentence explanation

Phasemed turns imaging studies into a persistent, inspectable patient model so a user
can move from anatomy, to source pixels, to change over time, to evidence, procedure
planning, modeling, and export without losing context.

## The software in one picture

```text
DICOM study or synthetic sample
        ↓
Index and compile
        ↓
Persistent PatientModel
        ↓
Model · Review · Change · Context · Procedure · Hologram
        ↓
Queries · Model Lab · Exports
```

The important idea is that the screens are different views of the same model. The
user is not copying information between separate tools.

## The shortest reliable demo

1. Open `/workspace`.
2. Click **Explore sample workspace**. The sample is synthetic and contains no patient
   data.
3. Start in **Model**. Click an anatomical object to show the inspector, spatial
   relationships, measurements, and evidence links.
4. Click **Review**. Move the linked slice cursor or change the window and level to
   show the source images behind the 3D object.
5. Click **Change**. Explain that longitudinal comparison is available when a prior
   PatientModel is linked; the current sample may say that no prior is available.
6. Click **Context**. Search evidence or use **Ask model** to query the selected
   object.
7. Click **Procedure**, then **Draw path**. This calculates path length,
   intersections, and clearances from the model geometry. Say clearly that this is a
   planning visualization, not clinical guidance.
8. Click **Hologram** or **Display** only if the audience wants the spatial-computing
   extension. It is a synchronized view of the same model with optional voice,
   gesture, rotation, and anatomy filters.
9. Open **Trial Studio** from the top bar when discussing the Regeneron track. This
   is a separate biostatistics workflow for trial design assumptions, sample-size
   planning, seeded simulation, attrition, interim looks, and a reviewable report.

## What each workspace area means

### Left rail: the study library

- **Studies**: imported or synthetic studies.
- **Findings**: anatomy and model objects available in the selected study.
- **Evidence**: source files and provenance attached to the model.
- **Import DICOM / Folder**: bring in local imaging data.
- **Gateway**: inspect configured DICOMweb or C-STORE sources when adapters exist.

### Center: the current view

- **Model**: 3D spatial anatomy and object selection.
- **Review**: axial, coronal, and sagittal source-image views plus a linked spatial
  preview.
- **Change**: current, prior, overlay, difference, and morph views when temporal data
  exists.
- **Context**: evidence search and model questions.
- **Procedure**: geometry-based trajectory planning.
- **Hologram**: display-oriented spatial view.

### Right rail: inspect, do not just look

The right rail is where the selected object becomes useful. It contains object facts,
the spatial graph, evidence, capability status, measurements, and model state. If a
feature is not configured or not supported, Phasemed should say so explicitly instead
of pretending it ran.

### Top bar: global actions

- **Trial Studio** opens the biostatistics track.
- **Attach evidence** adds a JSON context bundle.
- **Model lab** builds reproducible feature tables and validates local algorithms.
- **Export** produces PatientModel JSON, a JSON-LD graph, or a compact context bundle.
- **Display** opens the synchronized hologram presentation view.

## The backend story, in plain English

1. The importer indexes DICOM metadata and pixel data.
2. The compiler turns the study into a normalized PatientModel.
3. The PatientModel stores objects, geometry, relationships, provenance, revisions,
   and audit events locally.
4. The frontend requests focused views of that model instead of maintaining separate
   copies of the patient story.
5. Every export carries a representation of the same model for downstream tools.

Local runtime data lives under `.runtime/`. It is working state, not source code, and
should not be committed.

## How to explain the Regeneron track

Trial Studio is not claiming to replace a statistician. It is a transparent planning
aid:

```text
Study assumptions
        ↓
Planning formula and sample-size estimate
        ↓
Seeded Monte Carlo simulation
        ↓
Power, attrition, interim results, warnings, and synthetic cohort
```

The useful pitch is: “A team can see exactly which assumptions produced a design,
rerun it deterministically, and hand a statistician a reviewable planning artifact.”

Do not claim clinical validation, patient outcome prediction, or regulatory readiness.
Those require domain review and a prespecified statistical analysis plan.

## What to say when someone asks “why is this different?”

“Most imaging tools stop at pictures. Phasemed keeps the spatial model, the source
images, the time axis, and the evidence connected, so an answer can be inspected and
exported instead of being a disconnected visualization.”

## If something is missing during a demo

- No study loaded: use **Explore sample workspace**.
- No prior comparison: say that the temporal view is ready but needs a linked prior
  PatientModel.
- No evidence loaded: attach a context JSON or explain that provenance is explicit.
- No configured clinical service: open **Capabilities** and show the unavailable
  integration rather than implying it ran.
- No real clinical validation: use the phrase “planning visualization” or
  “transparent planning aid.”
