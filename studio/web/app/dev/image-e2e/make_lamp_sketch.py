"""A synthetic hand-drawn sketch of a desk lamp (pencil lines and handwritten-style notes on paper), for testing
"start from an image" with a sketch. No real user image is used.
  python make_lamp_sketch.py <out.png>"""
from __future__ import annotations

import math
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1400, 1050
PAPER = (247, 244, 236)
INK = (58, 62, 74)
rng = random.Random(7)


def font(size: int):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/System/Library/Fonts/Supplemental/Noteworthy.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                           # Pillow < 10.1
        return ImageFont.load_default()


def stroke(d: ImageDraw.ImageDraw, pts, w=3, passes=2):
    """A pencil line: each segment drawn twice with a little wobble."""
    for _ in range(passes):
        j = [(x + rng.uniform(-1.6, 1.6), y + rng.uniform(-1.6, 1.6)) for x, y in pts]
        d.line(j, fill=INK, width=w, joint="curve")


def ellipse(d, cx, cy, rx, ry, w=3, a0=0, a1=360):
    pts = [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a))) for a in range(a0, a1 + 1, 6)]
    stroke(d, pts, w)


def arrow(d, a, b):
    stroke(d, [a, b], 2, 1)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    for s in (-0.45, 0.45):
        stroke(d, [b, (b[0] - 18 * math.cos(ang + s), b[1] - 18 * math.sin(ang + s))], 2, 1)


def main(out: str) -> None:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    for _ in range(9000):                       # paper grain
        x, y = rng.randrange(W), rng.randrange(H)
        g = rng.randrange(225, 242)
        d.point((x, y), fill=(g, g - 2, g - 8))
    d.text((70, 50), "desk lamp idea  (sketch v2)", font=font(46), fill=INK)
    stroke(d, [(70, 112), (640, 108)], 2, 1)
    # weighted round base
    ellipse(d, 520, 860, 190, 46)
    ellipse(d, 520, 828, 190, 46, a0=180, a1=360)
    stroke(d, [(330, 828), (330, 860)])
    stroke(d, [(710, 828), (710, 860)])
    # touch pad on the base
    ellipse(d, 600, 840, 34, 10, w=2)
    # arm: two segments with a hinge
    stroke(d, [(520, 820), (450, 520)], w=5)
    stroke(d, [(450, 520), (700, 330)], w=5)
    ellipse(d, 450, 520, 16, 16)
    # head: a cone with the light below it
    stroke(d, [(690, 300), (860, 300), (900, 400), (650, 400), (690, 300)], w=4)
    ellipse(d, 775, 400, 125, 22, w=3)
    for k in range(-3, 4):                      # light rays
        stroke(d, [(775 + k * 30, 430), (775 + k * 70, 560)], 2, 1)
    # USB-C cable out of the back of the base
    stroke(d, [(330, 850), (250, 880), (160, 870), (90, 900)], 3)
    d.rectangle((84, 892, 104, 908), outline=INK, width=2)
    # notes with arrows
    f = font(34)
    d.text((930, 250), "warm white light,", font=f, fill=INK)
    d.text((930, 292), "dims smoothly", font=f, fill=INK)
    arrow(d, (920, 300), (870, 340))
    d.text((860, 760), "touch pad on the base:", font=f, fill=INK)
    d.text((860, 802), "tap = on/off, hold = dim", font=f, fill=INK)
    arrow(d, (850, 790), (640, 838))
    d.text((70, 940), "USB-C power, no battery", font=f, fill=INK)
    d.text((80, 470), "about 35 cm tall", font=f, fill=INK)
    d.text((80, 512), "matte white shell", font=f, fill=INK)
    d.text((930, 470), "maybe: a soft wake-up", font=f, fill=INK)
    d.text((930, 512), "glow at a set time?", font=f, fill=INK)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(out)
    print(out, img.size)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "lamp-sketch.png")
