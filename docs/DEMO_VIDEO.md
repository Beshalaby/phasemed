# Demo video: shot list and narration

Target length: 100 seconds (hard ceiling 2 minutes). One take per shot, cut together;
no live seeding on camera. The shots follow the slide deck's middle section (See it →
Trust it → Track it → Ask it → Walk around it), so the video and the deck tell the
same story with the same numbers.

## Set up once

- Follow "Before you present" in [PRESENTER_GUIDE.md](PRESENTER_GUIDE.md): studies
  loaded, Chat key set, one voice command already spoken so the speech model is cached.
- Browser window 1920×1080, zoom 100%, light theme, bookmarks bar hidden. Close the
  auto-opened hologram display tab's window out of frame (leave the tab open).
- Record the screen at 30 fps or more with system audio off; record narration
  separately and lay it under the cut. Keep the cursor slow and deliberate.
- Every number spoken below is on screen at that moment. If a number on screen
  differs, say the one on screen.

## Shots

| # | Time | On screen | Narration |
|---|------|-----------|-----------|
| 1 | 0:00–0:10 | Landing page `/`. Scroll slowly from the hero through "Import · Build · Analyze"; the model turns and the nodule lights up as you scroll. | "A CT scan is pixels and a free-text report. Phasmed compiles it into a patient model — anatomy you can measure, question and follow over time." |
| 2 | 0:10–0:18 | Click **Open workstation**. The 6-month follow-up opens by itself with the nodule selected. | "This is one study, built once. Everything you'll see is a view of that one model." |
| 3 | 0:18–0:32 | **Model**. Orbit a quarter turn. Hover the right rail: 18 mm, volume 4,218.8 mm³, **Nearest structures**. Click the heart, then the nodule again. Toggle **Isolate** on and off. | "Every structure is an object with geometry. Click one and you get its size, its volume, and what's nearest to it — computed, not typed in." |
| 4 | 0:32–0:44 | Press `2` (**Review**). The slices are already on the nodule, crosshair on it. Drag **Slice** a little each way; nudge **Window**. | "And it never loses the source. The 3D object links back to the exact slices it came from, in all three planes." |
| 5 | 0:44–1:00 | Press `3` (**Change**). Pause on the two study names and dates, then the first row: **Changed · Pulmonary nodule · volume +396.6%**. Click **Overlay**, then **Difference**. | "Six months later, the same patient. Phasmed links the two models object by object. The nodule went from 850 to 4,219 cubic millimetres — up 397 percent in 182 days. Measured, not described." |
| 6 | 1:00–1:18 | Press `1`, then click **Ask about this model**. Click the **What changed?** suggestion. Let the answer stream; the nodule turns red in the 3D view. Rest the cursor on the privacy note under the input. | "You can just ask. The assistant doesn't guess — it calls the same measurement tools and quotes their numbers, then flags what it's talking about. It sees a five-kilobyte de-identified summary. Never images, never names." |
| 7 | 1:18–1:32 | Press `6` (**Hologram**). Hold `Space`: "highlight the right lung". Then hold `Space`: "what changed since the prior study?" — the caption appears on the display. | "For a room instead of a desk: a four-view hologram display with local voice control. Same model, same answers." |
| 8 | 1:32–1:42 | `Esc`, then **More → Trial planning**. **Load example**, **Run simulation**; results scroll into view. | "For the Regeneron track, Trial Studio makes trial assumptions explicit: sample size, seeded simulation, attrition, and a report a statistician can review." |
| 9 | 1:42–1:50 | Back on the landing page's closing section, or the deck's last slide. | "Phasmed. Raw scans in, useful data out. It runs locally, it's open, and it starts in one click." |

## After recording

- Add a lower-third on shots 3–7: "Synthetic demonstration study · software prototype,
  not for clinical use."
- Captions on: the narration above is the caption file.
- Export 1080p H.264, under 100 MB. Link it from the README's Quickstart section and
  from the deck's last slide.

## If a shot misbehaves

- Chat answers slowly or not at all: check the note under the input says AI answers
  are on; otherwise record shot 6 with the built-in query and drop the "five-kilobyte"
  sentence.
- Voice mishears: say the structure name alone ("right lung"). The on-screen transcript
  shows what was heard.
- The display tab steals focus: it is a separate tab by design; click back to the
  workstation tab before recording.
