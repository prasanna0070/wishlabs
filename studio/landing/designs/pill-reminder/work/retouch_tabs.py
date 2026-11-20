#!/usr/bin/env python3
"""Recorded clean-up of pill-reminder-life-t2: remove tiny pseudo-glyph marks on the pill organiser's hinge tabs
(scene props only; the gadget is untouched). Feathered median filter on small patches -> raw/pill-reminder-life-t2r.png"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[4] / "_out/designs/pill-reminder-photos"
im = Image.open(OUT / "raw/pill-reminder-life-t2.png").convert("RGB")
# tab mark centres (raw px) and half-sizes, organiser lower right; all far from the gadget (x < 1500)
PATCHES = [(1835, 1347, 30, 16), (1915, 1300, 30, 16), (1995, 1255, 30, 16), (2070, 1215, 30, 16),
           (1748, 1395, 28, 15), (1660, 1440, 28, 15)]
med = im.filter(ImageFilter.MedianFilter(11)).filter(ImageFilter.GaussianBlur(1.2))
mask = Image.new("L", im.size, 0)
d = ImageDraw.Draw(mask)
for x, y, hw, hh in PATCHES:
    d.ellipse((x - hw, y - hh, x + hw, y + hh), fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(4))
out = Image.composite(med, im, mask)
out.save(OUT / "raw/pill-reminder-life-t2r.png")
for i, (x, y, hw, hh) in enumerate(PATCHES):
    box = (x - 70, y - 45, x + 70, y + 45)
    pair = Image.new("RGB", (280, 90))
    pair.paste(im.crop(box), (0, 0)); pair.paste(out.crop(box), (140, 0))
    pair.resize((840, 270), Image.LANCZOS).save(OUT / f"preview/retouch-tab{i}.jpg", quality=90)
print("ok")
