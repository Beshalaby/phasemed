from __future__ import annotations

import math
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work" / "project_video"
ASSETS = OUT / "assets"
CLIPS = OUT / "clips"


W, H = 1920, 1080
RED = "#c6382f"
BLACK = "#111111"
GRAY = "#707070"
LIGHT = "#e8e8e8"
CREAM = "#faf9f6"


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def svg_frame(body: str, scene: str, section: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="{W}" height="{H}" fill="{CREAM}"/>
  <g opacity="0.72" fill="{LIGHT}" font-family="monospace" font-size="18">
    <text x="48" y="118">·  ∿  ≈  +  −  |  /  \\  .  :  ,  ;  ?  @  #  0  1  &lt;&gt;  ≡  ∂  ∑  ◦  ○  ◆</text>
    <text x="48" y="146">+  0  /  ≈  ∞  ∠  ∆  ∇  ⊕  ⊗  ◦  ●  ○  ◆  ◇  ▲  △  ▷  ▻  ∿  ~  =</text>
    <text x="1010" y="950">∑  ∂  ≡  +  0  /  .  :  ;  ?  @  #  1  ∆  ◇  ○  ●  ∿  ≈</text>
  </g>
  <g font-family="monospace" fill="{GRAY}" font-size="20" letter-spacing="2">
    <text x="64" y="64">PHASMED · {esc(section.upper())}</text>
    <text x="1650" y="64">{esc(scene)}</text>
  </g>
  <line x1="64" y1="84" x2="1856" y2="84" stroke="{LIGHT}" stroke-width="2"/>
  {body}
  <g font-family="monospace" fill="{GRAY}" font-size="18" letter-spacing="1">
    <text x="64" y="1010">RAW SCANS → STRUCTURED PATIENT MODELS</text>
    <text x="1650" y="1010">LOCAL · TRACEABLE</text>
  </g>
</svg>'''


def text(x: int, y: int, value: str, size: int, fill: str = BLACK, weight: str = "400", anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" font-size="{size}px" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(value)}</text>'


def mono(x: int, y: int, value: str, size: int = 22, fill: str = GRAY, anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" font-family="monospace" font-size="{size}px" fill="{fill}" text-anchor="{anchor}" letter-spacing="1">{esc(value)}</text>'


def arrow(x1: int, y1: int, x2: int, y2: int, width: int = 8) -> str:
    dx, dy = x2 - x1, y2 - y1
    length = max(1.0, math.hypot(dx, dy))
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    bx, by = x2 - ux * 28, y2 - uy * 28
    p2 = (bx + px * 24, by + py * 24)
    p3 = (bx - px * 24, by - py * 24)
    return f'''<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{RED}" stroke-width="{width}"/><polygon points="{x2},{y2} {p2[0]:.1f},{p2[1]:.1f} {p3[0]:.1f},{p3[1]:.1f}" fill="{RED}"/>'''


def scene_01() -> str:
    body = (
        text(64, 360, "Raw scans.", 98, BLACK, "700")
        + text(64, 468, "Useful data.", 98, RED, "700")
        + mono(68, 570, "Phasmed turns imaging studies into a persistent, inspectable patient model.", 25, GRAY)
        + '<rect x="64" y="690" width="610" height="5" fill="%s"/>' % RED
        + mono(64, 742, "PROJECT VIDEO · PHASEMED", 20, GRAY)
        + mono(1810, 790, "01", 22, RED, "end")
    )
    return svg_frame(body, "01 / 08", "opening")


def scene_02() -> str:
    body = text(64, 250, "Start with the study.", 64, BLACK, "700") + mono(68, 294, "01 · IMPORT", 22, RED)
    for i, y in enumerate((410, 470, 530)):
        body += f'<rect x="126" y="{y}" width="430" height="88" rx="4" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
        body += mono(160, y + 54, f"CT_SERIES_0{i+1}  /  DICOM", 24, BLACK)
    body += arrow(680, 500, 940, 500)
    body += f'<rect x="1010" y="350" width="610" height="300" rx="8" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(1055, 410, "PATIENTMODEL", 24, RED)
    body += text(1055, 492, "One study.", 48, BLACK, "700")
    body += text(1055, 555, "One connected model.", 48, BLACK, "700")
    body += mono(1055, 615, "provenance · geometry · evidence", 22, GRAY)
    body += mono(1650, 790, "02", 22, RED, "end")
    return svg_frame(body, "02 / 08", "import")


def scene_03() -> str:
    body = text(64, 250, "Build structure from pixels.", 64, BLACK, "700") + mono(68, 294, "02 · COMPILE", 22, RED)
    # Slice cards.
    for i, x in enumerate((110, 270, 430)):
        body += f'<rect x="{x}" y="390" width="210" height="230" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
        body += f'<ellipse cx="{x+105}" cy="505" rx="58" ry="76" fill="none" stroke="{GRAY}" stroke-width="4"/>'
        body += f'<ellipse cx="{x+80}" cy="490" rx="24" ry="42" fill="none" stroke="{RED}" stroke-width="4"/>'
        body += f'<ellipse cx="{x+132}" cy="490" rx="24" ry="42" fill="none" stroke="{RED}" stroke-width="4"/>'
        body += mono(x + 24, 656, f"SLICE 0{i+1}", 18, GRAY)
    body += arrow(700, 505, 910, 505)
    body += f'<rect x="1000" y="330" width="620" height="360" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += '<path d="M1110 555 C1060 450 1190 390 1290 450 C1400 360 1530 470 1470 560 C1510 650 1320 700 1230 630 C1150 690 1080 625 1110 555 Z" fill="none" stroke="%s" stroke-width="5"/>' % RED
    body += '<ellipse cx="1260" cy="530" rx="70" ry="95" fill="none" stroke="%s" stroke-width="4"/>' % GRAY
    body += mono(1055, 745, "labeled objects · coordinates · measurements", 22, GRAY)
    body += mono(1650, 790, "03", 22, RED, "end")
    return svg_frame(body, "03 / 08", "build")


def scene_04() -> str:
    body = text(64, 250, "Keep anatomy and evidence connected.", 58, BLACK, "700") + mono(68, 294, "03 · REVIEW", 22, RED)
    body += f'<rect x="90" y="360" width="710" height="420" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += '<path d="M260 620 C190 500 340 410 455 490 C560 390 700 500 630 610 C650 720 430 750 370 660 C300 720 235 680 260 620Z" fill="none" stroke="%s" stroke-width="6"/>' % RED
    body += mono(130, 420, "MODEL VIEW", 20, GRAY)
    body += mono(130, 730, "selected: RIGHT LUNG", 20, RED)
    body += f'<rect x="900" y="360" width="400" height="190" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
    body += f'<rect x="1340" y="360" width="400" height="190" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
    body += f'<rect x="900" y="590" width="840" height="190" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
    for x, y, label in ((930, 410, "AXIAL"), (1370, 410, "CORONAL"), (930, 640, "SAGITTAL")):
        body += mono(x, y, label, 18, GRAY)
        body += f'<ellipse cx="{x+150}" cy="{y+75}" rx="110" ry="52" fill="none" stroke="{GRAY}" stroke-width="3"/>'
        body += f'<circle cx="{x+185}" cy="{y+75}" r="18" fill="{RED}"/>'
    body += arrow(790, 600, 900, 600, 6)
    body += mono(1650, 820, "04", 22, RED, "end")
    return svg_frame(body, "04 / 08", "review")


def scene_05() -> str:
    body = text(64, 250, "Measure change over time.", 64, BLACK, "700") + mono(68, 294, "04 · CHANGE", 22, RED)
    body += mono(150, 410, "BASELINE", 20, GRAY)
    body += mono(150, 700, "FOLLOW-UP", 20, GRAY)
    body += f'<rect x="140" y="450" width="520" height="150" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
    body += f'<rect x="140" y="740" width="520" height="150" fill="#ffffff" stroke="{BLACK}" stroke-width="3"/>'
    body += '<circle cx="405" cy="525" r="48" fill="none" stroke="%s" stroke-width="6"/>' % GRAY
    body += '<circle cx="405" cy="815" r="72" fill="none" stroke="%s" stroke-width="6"/>' % RED
    body += arrow(760, 590, 760, 735, 7)
    body += f'<rect x="940" y="430" width="700" height="360" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(1000, 500, "MEASURED DIFFERENCE", 22, RED)
    body += text(1000, 595, "+ 24 px", 72, BLACK, "700")
    body += mono(1000, 665, "linked prior model · reproducible comparison", 22, GRAY)
    body += mono(1650, 820, "05", 22, RED, "end")
    return svg_frame(body, "05 / 08", "change")


def scene_06() -> str:
    body = text(64, 250, "Ask focused questions.", 64, BLACK, "700") + mono(68, 294, "05 · CONTEXT", 22, RED)
    body += f'<rect x="140" y="410" width="780" height="250" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(190, 470, "QUESTION", 20, RED)
    body += text(190, 555, "What changed near the nodule?", 42, BLACK, "700")
    body += arrow(960, 535, 1130, 535)
    body += f'<rect x="1200" y="350" width="480" height="370" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(1250, 420, "ANSWERED FROM", 20, GRAY)
    body += text(1250, 500, "geometry", 38, BLACK, "700")
    body += text(1250, 555, "evidence", 38, BLACK, "700")
    body += text(1250, 610, "measured change", 38, BLACK, "700")
    body += mono(1250, 680, "traceable · inspectable", 20, RED)
    body += mono(1650, 820, "06", 22, RED, "end")
    return svg_frame(body, "06 / 08", "query")


def scene_07() -> str:
    body = text(64, 250, "Use the model in space.", 64, BLACK, "700") + mono(68, 294, "06 · SPATIAL VIEW", 22, RED)
    body += f'<rect x="120" y="380" width="760" height="430" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += '<path d="M330 675 C265 535 420 430 545 520 C660 425 790 540 700 650 C725 750 520 790 450 700 C375 760 300 735 330 675Z" fill="none" stroke="%s" stroke-width="6"/>' % RED
    body += '<line x1="230" y1="735" x2="740" y2="470" stroke="%s" stroke-width="5" stroke-dasharray="16 12"/>' % RED
    body += mono(165, 425, "PROCEDURE PATH", 20, GRAY)
    body += mono(165, 770, "planning visualization · not clinical guidance", 18, GRAY)
    body += f'<rect x="1040" y="390" width="560" height="350" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(1100, 460, "VOICE / GESTURE", 20, RED)
    body += text(1100, 550, '"highlight right lung"', 40, BLACK, "700")
    body += mono(1100, 635, "same model · new view", 22, GRAY)
    body += mono(1650, 820, "07", 22, RED, "end")
    return svg_frame(body, "07 / 08", "spatial")


def scene_08() -> str:
    body = text(64, 250, "Useful models out.", 76, BLACK, "700") + mono(68, 294, "07 · EXPORT", 22, RED)
    body += f'<rect x="140" y="390" width="570" height="330" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(190, 455, "PATIENTMODEL.JSON", 22, RED)
    body += mono(190, 520, "objects: 12", 22, GRAY)
    body += mono(190, 570, "relationships: 31", 22, GRAY)
    body += mono(190, 620, "evidence: linked", 22, GRAY)
    body += mono(190, 670, "audit: local", 22, GRAY)
    body += arrow(780, 550, 980, 550)
    body += f'<rect x="1060" y="350" width="560" height="410" fill="#ffffff" stroke="{BLACK}" stroke-width="4"/>'
    body += mono(1120, 425, "NEXT", 22, RED)
    body += text(1120, 520, "review", 44, BLACK, "700")
    body += text(1120, 585, "query", 44, BLACK, "700")
    body += text(1120, 650, "analyze", 44, BLACK, "700")
    body += text(1120, 715, "export", 44, BLACK, "700")
    body += text(64, 900, "Phasmed", 86, RED, "700")
    body += mono(500, 900, "patient models in · useful models out", 26, GRAY)
    body += mono(1650, 820, "08", 22, RED, "end")
    return svg_frame(body, "08 / 08", "closing")


SCENES = [
    ("01_opening", 12, scene_01),
    ("02_import", 16, scene_02),
    ("03_build", 18, scene_03),
    ("04_review", 18, scene_04),
    ("05_change", 16, scene_05),
    ("06_query", 14, scene_06),
    ("07_spatial", 14, scene_07),
    ("08_closing", 12, scene_08),
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    for name, duration, builder in SCENES:
        svg_path = ASSETS / f"{name}.svg"
        png_path = ASSETS / f"{name}.png"
        clip_path = CLIPS / f"{name}.mp4"
        svg_path.write_text(builder(), encoding="utf-8")
        run(["sips", "-s", "format", "png", str(svg_path), "--out", str(png_path)])
        run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(png_path), "-t", str(duration),
            "-r", "30", "-vf", "format=yuv420p", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-movflags", "+faststart",
            str(clip_path),
        ])

    concat = OUT / "concat.txt"
    concat.write_text("\n".join(f"file '{p}'" for p in sorted(CLIPS.glob("*.mp4"))) + "\n", encoding="utf-8")
    final = OUT / "phasmed_project_video_silent.mp4"
    run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(final),
    ])
    print(final)


if __name__ == "__main__":
    main()
