# Phasmed presenter guide

This is the guide for the person presenting Phasmed. Learn the story first, then use
the controls that support it. Every control named in **bold** is the exact label on
screen.

## The one-sentence explanation

Phasmed turns an imaging study into a persistent patient model you can measure, ask
questions of, and follow over time — and every screen is a view of that one model.

## The software in one picture

```text
DICOM study (or the included synthetic studies)
        ↓
Index and build
        ↓
Persistent patient model: objects · geometry · relationships · change · evidence
        ↓
Model · Review · Change · Context · Procedure · Hologram
        ↓
Chat · Analyze model · Export
```

The screens are different views of the same model. Nobody copies information between
separate tools.

## Before you present (two minutes)

Do this once, on a good connection, before you are in front of anyone.

1. Start the server and check it answers:
   ```bash
   ./.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
   curl -s http://127.0.0.1:8787/api/health
   ```
2. Load the studies ahead of time. Either open `/workspace` and click **Explore guided
   workspace** once, or run `./.venv/bin/python scripts/seed_demo_data.py`. The first
   run downloads the anatomy atlas (about 70 MB) into `.runtime/atlas`; after that it
   works offline. With no network at all it still works, using simpler built-in shapes.
3. Chat (optional): put `PHASEMED_OPENAI_API_KEY` in `.env` and restart. Open the
   **Chat** tab — the note under the input says whether AI answers are on. Without a
   key the tab still answers with built-in measured queries.
4. Voice (optional): say one command in **Hologram** before the session. The first
   command downloads the speech model (about 150 MB); afterwards it runs offline.
5. Hand gestures (optional) need internet the first time, to fetch the hand-tracking
   model.
6. Allow pop-ups for `127.0.0.1`. Opening a model also opens a separate hologram
   display tab; if the browser blocks it, the workstation says so.
7. Projecting? Press `?` to check the shortcuts, and keep the window at least 1280
   pixels wide so the tool labels stay visible.

## The shortest reliable demo

About three minutes. Steps 5 and 6 are the strongest part — do not skip them.

1. Open `/` and scroll once: the model on the page is the live patient model. Click
   **Open workstation**.
2. On a clean machine, click **Explore guided workspace** and let the progress line
   finish (about 25 seconds with the atlas cached). The studies are synthetic and
   contain no patient data. If the studies are already loaded, the 6-month follow-up
   opens by itself.
3. **Model.** The pulmonary nodule is already selected. Point at the right rail: size,
   volume, **Volume change +396.6%**, and **Nearest structures** with distances. Click
   another structure to show that everything is selectable. **Isolate**, **Links**
   and **Anatomy** change what is drawn; **Reset** puts it back.
4. **Review.** The three slice views open on the slice that contains the nodule, with
   the crosshair on it. Drag **Slice**, **Window** or **Level**. This is the source
   image behind the 3D object.
5. **Change.** The header names both studies: *CT Chest · baseline, 14 Mar 2026* and
   *CT Chest · 6-month follow-up, 12 Sep 2026*. The first row reads **Changed ·
   Pulmonary nodule · volume +396.6%**. Click **Overlay** to see the earlier nodule
   inside the current one, then **Difference**. Say: "measured, not described."
6. **Chat.** Click **Ask about this model** on the scene (or press `/`), then the
   **What changed?** suggestion. The answer quotes 849.6 → 4,218.8 mm³ over 182 days
   and flags the nodule red in the 3D view. Say what the note under the input says: a
   de-identified summary is sent, never images, names or IDs, and it is not a
   diagnosis.
7. **Context.** Three evidence records are bound to the model. **View object** jumps
   from a report to the structure it describes. **Add evidence** (top bar) imports a
   JSON bundle.
8. **Procedure**, then **Draw path**. Length, intersections and clearances come from
   model geometry. Say clearly: planning visualization, not clinical guidance.
9. **Hologram**, only if the audience wants the spatial-computing extension. Hold
   `Space` and say "highlight the right lung", or ask "what changed since the prior
   study?" — the answer appears as a caption on the display. `Esc` leaves.
10. **More → Trial planning** for the Regeneron track: **Load example**, then **Run
    simulation**.

## What each workspace area means

### Left rail: the study library

- **Studies**: imported or synthetic studies. *Model ready* means it can be opened;
  *Needs a model* offers **Build model**.
- **Findings**: the structures and findings in the open model. Flagged ones are
  marked.
- **Evidence**: source files and provenance attached to the model.
- **Import DICOM / Folder**: bring in local imaging data.

### Center: the current view

- **Model**: 3D anatomy and selection. **Measure** takes a distance between two
  points.
- **Review**: axial, coronal and sagittal source images plus a linked 3D preview.
- **Change**: current, prior, overlay, difference and morph views against an earlier
  study of the same patient. With no earlier study it says so and offers to open the
  follow-up.
- **Context**: evidence search and review.
- **Procedure**: geometry-based trajectory planning.
- **Hologram**: a four-view display with voice and gesture control.

### Right rail: inspect, do not just look

Tabs: **Object** (measurements, nearest structures, bound evidence), **Graph**,
**Evidence**, **Chat**. Below them, **Capabilities** lists what this model can and
cannot prove; **Details** opens *What this model can prove*. If something is not set
up, Phasmed says so instead of pretending it ran.

### Top bar: global actions

- **Imaging sources**: browse a connected image server or studies sent from a scanner,
  when configured.
- **Add evidence**: attach a JSON or FHIR context bundle.
- **Export model**: patient-model JSON, a JSON-LD graph, or a compact context bundle.
- **More**: **Trial planning**, **Contrast**, **Camera** (gestures), **Analyze model**
  (feature tables and local algorithms), **Keyboard shortcuts**, **About Phasmed**.

### Keyboard

`1`–`6` switch views · `/` opens chat · `+` `−` `0` zoom and fit · `?` lists
everything · in Hologram, hold `Space` to speak and press `S` to stop the spin.

## The backend story, in plain English

1. The importer indexes DICOM metadata and pixel data.
2. The compiler turns the study into a normalized patient model (`PatientModel` in the
   API and exports).
3. The model stores objects, geometry, relationships, provenance, revisions and audit
   events locally.
4. The frontend requests focused views of that model instead of keeping separate
   copies of the patient story.
5. The chat assistant never produces a number itself: it calls the same deterministic
   tools the app uses and quotes what they return.
6. Every export carries the same model for downstream tools.

Local runtime data lives under `.runtime/`. It is working state, not source code, and
is never committed.

## How to explain the Regeneron track

Trial Studio does not replace a statistician. It is a transparent planning aid:

```text
Study assumptions
        ↓
Planning formula and sample-size estimate
        ↓
Seeded Monte Carlo simulation
        ↓
Power, attrition, interim results, warnings, and synthetic cohort
```

The useful pitch: "A team can see exactly which assumptions produced a design, rerun
it deterministically, and hand a statistician a reviewable planning artifact."

Do not claim clinical validation, patient-outcome prediction, or regulatory readiness.
Those require domain review and a prespecified statistical analysis plan.

## What to say when someone asks "why is this different?"

"Most imaging tools stop at pictures. Phasmed keeps the spatial model, the source
images, the time axis and the evidence connected, so an answer can be inspected,
questioned and exported instead of being a disconnected visualization."

## Safe claims

- Say: measured from geometry, synthetic demonstration studies, software prototype,
  planning visualization, transparent planning aid.
- Distances between structures are bounding-box based today. Say "approximate surface
  distance", not "exact".
- Do not say: diagnosis, clinically validated, real patient data, regulatory ready.

## If something goes wrong during a demo

- **Empty workspace**: click **Explore guided workspace**. Progress and any error stay
  on screen, with **Try again**.
- **"Can't reach the local server"**: the server stopped. Restart it and click **Try
  again**.
- **A study says *Needs a model***: open it and click **Build patient model**.
- **Change says "Nothing to compare yet"**: that study has no earlier study. Click
  **Open the follow-up**.
- **Chat says AI answers are off**: no key is configured. The built-in queries still
  answer about the selected structure; say so and move on.
- **Voice does nothing**: the speech model is still downloading, or the microphone was
  refused. Type the same question in **Chat**.
- **An integration is missing**: open **Details** under Capabilities and show that it
  is listed as not set up.
