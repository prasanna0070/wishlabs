#!/usr/bin/env python3
"""Exact cut-outs of the six engine preview renders, placed at true relative scale in one 16:9 layout.

The layout is the composition reference for the hero photo, so each gadget keeps the exact pixels of its real
preview render (shape, features, colours, engraved text, the screen decal) and Nano Banana only has to restyle the
scene around them. Run on the dev VM with the frozen engine:

    PYTHONPATH=$PWD/engine python landing/designs/_page/lineup_layout.py

How the mask is made: the frozen renderer's own camera (gstudio.cad.render._camera, same direction and size as the
preview) draws each part of _out/designs/<slug>/preview.glb flat white on black, at the preview's 2x supersampling.
That silhouette is the alpha; the colour pixels come unchanged from _out/designs/<slug>/preview.png. Nothing in the
engine is modified; this script only calls its functions.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

STUDIO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(STUDIO / "engine"))
from gstudio.cad import render as R  # noqa: E402

OUT = STUDIO / "_out" / "page"
CUT = OUT / "cutouts"
SIZE, SS = 1024, 2
DIRECTION = (0.52, -1.0, 0.40)          # gstudio.cad.render.hero default, used by the preview
ORDER = ["desk-bot", "book-light", "love-note", "pill-reminder", "focus-cube", "shop-greeter"]


def silhouette(slug: str) -> tuple[Image.Image, dict]:
    items = R.items_from_glb(STUDIO / "_out" / "designs" / slug / "preview.glb")
    pl = R._plotter((SIZE, SIZE), SS)
    pl.set_background("black")
    sel = R._sel(items, skip=("REFERENCE_",))
    bb = R._bbox_items(items, R.SHELLS)
    c, rad = R._camera(pl, bb, DIRECTION, zoom=1.0)
    for _name, _sub, m, _col, _alpha in sel:
        pl.add_mesh(m, color="white", lighting=False, show_scalar_bar=False)
    img = pl.screenshot(return_img=True)
    pl.close()
    a = Image.fromarray(np.asarray(img)[..., 0]).resize((SIZE, SIZE), Image.LANCZOS)
    d = np.asarray(DIRECTION, float)
    elev = math.atan2(d[2], math.hypot(d[0], d[1]))
    px_per_mm = SIZE / (2.16 * rad)       # _camera: dist = R / tan(va/2) * 1.08, so the frame spans 2.16 R
    half_h = (bb[5] - bb[4]) / 2
    ground = (SIZE / 2, SIZE / 2 + half_h * math.cos(elev) * px_per_mm)   # image point under the bbox centre at z=0
    meta = {"bbox_mm": [round(float(v), 1) for v in bb], "radius_mm": round(float(rad), 2),
            "px_per_mm": round(px_per_mm, 4), "ground_px": [round(ground[0], 1), round(ground[1], 1)]}
    return a, meta


def main():
    CUT.mkdir(parents=True, exist_ok=True)
    cuts = {}
    for slug in ORDER:
        alpha, meta = silhouette(slug)
        rgb = Image.open(STUDIO / "_out" / "designs" / slug / "preview.png").convert("RGB")
        im = rgb.copy()
        im.putalpha(alpha)
        box = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
        meta["alpha_bbox_px"] = list(box)
        im.save(CUT / f"{slug}.png")
        cuts[slug] = (im, meta)
        print(slug, json.dumps(meta))

    # ---- layout: 2752 x 1536 (what gemini-3-pro-image returns for 16:9 at 2K)
    W, H = 2752, 1536
    S = 2.05                                   # px per mm in the layout
    table_y = 1040                             # where every gadget's ground centre sits
    gap_mm = 34.0
    widths = []
    for slug in ORDER:
        im, meta = cuts[slug]
        k = S / meta["px_per_mm"]
        x0, _, x1, _ = meta["alpha_bbox_px"]
        widths.append((x1 - x0) * k)
    total = sum(widths) + gap_mm * S * (len(ORDER) - 1)
    x = (W - total) / 2
    canvas = Image.new("RGB", (W, H))
    dr = ImageDraw.Draw(canvas)
    for y in range(H):                          # flat stand-ins: warm wall above, light oak table below
        if y < 860:
            t = y / 860
            col = (int(238 - 10 * t), int(229 - 10 * t), int(216 - 10 * t))
        elif y < 1330:
            t = (y - 860) / 470
            col = (int(214 - 18 * t), int(178 - 16 * t), int(132 - 14 * t))
        else:
            col = (150, 112, 74)
        dr.line([(0, y), (W, y)], fill=col)
    placed = []
    shadow = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shadow)
    for slug, w in zip(ORDER, widths):
        im, meta = cuts[slug]
        k = S / meta["px_per_mm"]
        x0, y0, x1, y1 = meta["alpha_bbox_px"]
        crop = im.crop((x0, y0, x1, y1))
        crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))), Image.LANCZOS)
        gx, gy = meta["ground_px"]
        px = round(x)
        py = round(table_y - (gy - y0) * k)
        bw = (meta["bbox_mm"][1] - meta["bbox_mm"][0]) * S
        cx = px + (gx - x0) * k
        sd.ellipse([cx - bw * 0.62, table_y - 22, cx + bw * 0.62, table_y + 30], fill=120)
        placed.append((crop, px, py))
        x += w + gap_mm * S
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    dark = Image.new("RGB", (W, H), (90, 66, 44))
    canvas = Image.composite(dark, canvas, shadow.point(lambda v: int(v * 0.55)))
    for crop, px, py in placed:
        canvas.paste(crop, (px, py), crop)
    canvas.save(OUT / "raw" / "hero-layout.png")
    small = canvas.copy()
    small.thumbnail((1024, 1024), Image.LANCZOS)
    (OUT / "refs").mkdir(parents=True, exist_ok=True)
    small.save(OUT / "refs" / "hero-layout-1024.jpg", "JPEG", quality=92)
    pv = canvas.copy()
    pv.thumbnail((1600, 1600), Image.LANCZOS)
    (OUT / "preview").mkdir(parents=True, exist_ok=True)
    pv.save(OUT / "preview" / "hero-layout.jpg", "JPEG", quality=88)
    print("layout", W, H, "px/mm", S)


if __name__ == "__main__":
    main()
