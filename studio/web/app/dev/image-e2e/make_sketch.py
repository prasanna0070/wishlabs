"""Synthetic test images for "start from an image" (no real user's image is ever committed or used in tests).

    /opt/gs/venv/bin/python web/app/dev/image-e2e/make_sketch.py <out_dir>

Writes mood-cloud-sheet.png (1200 x 1600, a ChatGPT-style concept sheet for a made-up "Mood Cloud" lamp: a
hand-drawn cloud, call-outs, a feature list including things our catalog cannot build, like a battery) and
desk-sketch.jpg (a quick pencil-style sketch on off-white paper, as a phone photo would be: JPEG with EXIF
orientation 6, so the server has to rotate it).
"""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "_out/image-e2e")
OUT.mkdir(parents=True, exist_ok=True)
random.seed(7)


def font(size: int, bold: bool = False):
    for name in (["DejaVuSans-Bold.ttf"] if bold else []) + ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"]:
        for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/dejavu"):
            p = Path(d) / name
            if p.exists():
                return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wobbly_line(d: ImageDraw.ImageDraw, pts, fill, width=3):
    """A slightly shaky pen line through the points."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / 12))
        for i in range(n):
            t = i / n
            out.append((x0 + (x1 - x0) * t + random.uniform(-1.4, 1.4), y0 + (y1 - y0) * t + random.uniform(-1.4, 1.4)))
    out.append(pts[-1])
    d.line(out, fill=fill, width=width, joint="curve")


def cloud(d, cx, cy, s, ink, fill):
    puffs = [(-0.55, 0.1, 0.42), (-0.15, -0.22, 0.5), (0.32, -0.08, 0.44), (0.62, 0.16, 0.32), (0.05, 0.22, 0.5)]
    for dx, dy, r in puffs:
        x, y, rr = cx + dx * s, cy + dy * s, r * s
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=fill)
    for dx, dy, r in puffs:
        x, y, rr = cx + dx * s, cy + dy * s, r * s
        pts = [(x + rr * math.cos(a), y + rr * math.sin(a)) for a in [i * math.pi / 18 for i in range(37)]]
        wobbly_line(d, pts, ink, 4)
    d.rectangle([cx - 0.75 * s, cy + 0.3 * s, cx + 0.85 * s, cy + 0.75 * s], fill=fill)
    wobbly_line(d, [(cx - 0.75 * s, cy + 0.72 * s), (cx + 0.85 * s, cy + 0.72 * s)], ink, 4)


def sheet():
    W, H = 1200, 1600
    im = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(im)
    ink, blue, grey = "#1f2a44", "#3b6fd8", "#6b7280"
    d.text((70, 60), "MOOD CLOUD", font=font(72, True), fill=ink)
    d.text((74, 150), "concept sheet v0.2  -  a desk lamp that shows tomorrow's weather", font=font(28), fill=grey)
    d.line([(70, 205), (W - 70, 205)], fill="#d7dbe3", width=3)
    cloud(d, 520, 560, 330, ink, "#eaf1ff")
    # rain drops under the cloud
    for x in (380, 470, 560, 650):
        wobbly_line(d, [(x, 860), (x - 12, 905)], blue, 5)
    callouts = [
        ((760, 430), (900, 330), "RGB LED ring inside:\nblue = rain tomorrow"),
        ((640, 380), (860, 250), "tap the top to\nchange the mood"),
        ((300, 700), (110, 960), "USB-C power\nat the back"),
        ((830, 640), (900, 980), "Wi-Fi: weather\nfrom the internet"),
    ]
    f = font(30)
    for (x0, y0), (x1, y1), label in callouts:
        wobbly_line(d, [(x0, y0), (x1, y1)], grey, 3)
        d.ellipse([x0 - 6, y0 - 6, x0 + 6, y0 + 6], fill=grey)
        d.multiline_text((x1 - 10 if x1 > 600 else x1, y1 + 8), label, font=f, fill=ink, spacing=6)
    d.text((70, 1100), "FEATURES", font=font(34, True), fill=ink)
    items = [
        "Glows blue the evening before it rains",
        "Soft white night light, dims on a schedule",
        "Tap the top to switch moods",
        "Small speaker: a soft chime for rain",
        "2-day battery so it can sit anywhere",
        "About 18 cm wide, matte white shell",
    ]
    for i, t in enumerate(items):
        y = 1160 + i * 56
        d.ellipse([78, y + 12, 92, y + 26], fill=blue)
        d.text((110, y), t, font=font(32), fill=ink)
    d.text((70, H - 70), "made with an AI image tool - not to scale", font=font(24), fill=grey)
    im.save(OUT / "mood-cloud-sheet.png", optimize=True)


def sketch():
    W, H = 1600, 1200  # landscape pixels, shown rotated (EXIF orientation 6), like a phone photo
    im = Image.new("RGB", (W, H), "#f4f1e8")
    d = ImageDraw.Draw(im)
    pencil = "#3d3a36"
    for _ in range(4000):  # paper grain
        x, y = random.randrange(W), random.randrange(H)
        d.point((x, y), fill="#e9e4d6")
    wobbly_line(d, [(420, 760), (1180, 760), (1120, 860), (480, 860), (420, 760)], pencil, 5)  # base
    wobbly_line(d, [(800, 760), (800, 420)], pencil, 5)  # stem
    wobbly_line(d, [(560, 420), (1040, 420), (930, 250), (670, 250), (560, 420)], pencil, 5)  # shade
    for x in (640, 720, 880, 960):
        wobbly_line(d, [(x, 450), (x - 30, 520)], "#8a8274", 3)
    d.text((440, 900), "desk lamp, wooden base, warm light", font=font(40), fill=pencil)
    d.text((440, 960), "touch dimmer?  USB powered", font=font(40), fill=pencil)
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 CW to view
    im.save(OUT / "desk-sketch.jpg", quality=88, exif=exif)


sheet()
sketch()
for p in sorted(OUT.glob("*.*")):
    print(p, p.stat().st_size)
