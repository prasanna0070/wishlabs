#!/usr/bin/env python3
"""Focus Cube landing photos on Vertex (gemini-3-pro-image, global endpoint), grounded on our real preview render.

Runs on the dev VM only (tools/vm.sh run), never on the laptop. Call shape copied from
consumer-hardware-platform/studio/_out/landing/gen_images.py: v1beta1, TEXT+IMAGE, imageSize 2K.

    python3 landing/designs/focus-cube/gen_photos.py gen <stem> <try> [extra]
    python3 landing/designs/focus-cube/gen_photos.py final <stem> <try>
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import google.auth
import requests
from google.auth.transport.requests import Request as AuthRequest
from PIL import Image

STUDIO = Path(__file__).resolve().parents[3]
REF = STUDIO / "_out/designs/focus-cube/preview.png"
WORK = STUDIO / "_out/designs/focus-cube/photos"
MODEL = "gemini-3-pro-image"
API = "v1beta1"

PRODUCT = (
    "The attached image is the exact product reference, a real 3D-printed gadget we designed: a 'focus cube' "
    "study timer about 10 cm on each side. It is a matte charcoal-black cube with softly rounded edges and corners. "
    "On the front face: a large flat cream-coloured circular light diffuser, surrounded by a slightly raised "
    "tomato-red ring, and below it the small engraved number 25 in warm grey. On the top face: a small round "
    "touch mark near the front edge and a small circular speaker grille of tiny dots behind it. "
    "Keep the product's shape, proportions, size, every feature, their positions and all colours exactly as in "
    "the reference. Do not add buttons, screens, lights, cables, logos, labels or any other text or features to it; "
    "the only text on the product is the engraved 25. The cream diffuser is switched off (plain cream, no glow). "
    "Fine, realistic matte 3D-printed surface with barely visible layer lines. "
)

SPECS = {
    "focus-cube-studio": {
        "aspect": "1:1", "size": (1200, 1200), "kb": 200,
        "prompt": PRODUCT + (
            "Create a premium studio product photograph of this exact cube: three-quarter view from slightly above, "
            "front face toward the camera as in the reference, standing on a seamless warm sand-beige paper "
            "background that sweeps up behind it, soft large softbox key light from the upper left, gentle fill, "
            "a soft natural contact shadow under the cube. Clean, minimal, high-end catalogue look, sharp focus on "
            "the product, centred with generous space around it. No props, no people, no hands, no text, no logos, "
            "no watermark."),
    },
    "focus-cube-life": {
        "aspect": "4:3", "size": (1600, 1200), "kb": 230,
        "prompt": PRODUCT + (
            "Create a realistic lifestyle photograph of this exact cube on a student's wooden study desk in India "
            "during exam season, early evening, warm light from a desk lamp off to the side. Around it: a short stack "
            "of closed textbooks with plain solid-colour covers and no titles, an open spiral notebook whose pages "
            "are softly out of focus so no writing can be read, a few highlighter pens and a pencil, and a small glass "
            "of chai. A student's hand holding a pen rests on the notebook at the edge of the frame; only the hand "
            "and forearm, no face, no head, no person visible beyond the hand. The cube sits in the foreground right "
            "of centre, front face toward the camera at a slight three-quarter angle, in sharp focus, with a shallow "
            "depth of field behind. Natural, calm, focused mood. No readable text anywhere except the engraved 25 on "
            "the cube, no logos or brand marks on any object, no laptop, no phone screen, no watermark."),
    },
}

_creds = None
_project = None


def auth(force=False):
    global _creds, _project
    if _creds is None:
        _creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _project = os.environ.get("GOOGLE_CLOUD_PROJECT") or _project
    if force or not _creds.valid:
        _creds.refresh(AuthRequest())
    return _creds.token, _project


def url():
    _, proj = auth()
    return (f"https://aiplatform.googleapis.com/{API}/projects/{proj}/locations/global"
            f"/publishers/google/models/{MODEL}:generateContent")


def headers():
    tok, proj = auth()
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "x-goog-user-project": proj}


def small_ref(max_px=1024) -> bytes:
    im = Image.open(REF)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def gen(stem: str, attempt: int, extra: str = ""):
    spec = SPECS[stem]
    WORK.mkdir(parents=True, exist_ok=True)
    text = spec["prompt"] + (" " + extra if extra else "")
    body = {"contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(small_ref()).decode()}},
                {"text": text}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": spec["aspect"], "imageSize": "2K"}}}
    log = {"stem": stem, "attempt": attempt, "model": MODEL, "extra": extra}
    t = time.time()
    r = None
    for k in range(4):
        try:
            r = requests.post(url(), headers=headers(), json=body, timeout=420)
        except requests.RequestException as e:
            log["error"] = str(e)[:200]
            time.sleep(10 * (k + 1))
            continue
        if r.status_code == 200:
            break
        log["error"] = f"{r.status_code} {r.text[:300]}"
        if r.status_code == 401:
            auth(force=True)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(15 * (k + 1))
            continue
        break
    if r is None or r.status_code != 200:
        print(json.dumps(log))
        return
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    imgs = [p for p in parts if "inlineData" in p and not p.get("thought")]
    log.pop("error", None)
    log.update(secs=round(time.time() - t, 1), finish=cand.get("finishReason"), modelVersion=d.get("modelVersion"),
               text=" ".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))[:300])
    if not imgs:
        log["error"] = "no image in response"
        print(json.dumps(log))
        return
    im = Image.open(io.BytesIO(base64.b64decode(imgs[-1]["inlineData"]["data"]))).convert("RGB")
    raw = WORK / f"{stem}-t{attempt}.png"
    im.save(raw)
    pv = im.copy()
    pv.thumbnail((1400, 1400), Image.LANCZOS)
    pv.save(WORK / f"{stem}-t{attempt}-preview.jpg", "JPEG", quality=88)
    log.update(raw=str(raw.relative_to(STUDIO)), size=list(im.size))
    print(json.dumps(log))
    with open(WORK / "gen-log.jsonl", "a") as f:
        f.write(json.dumps(log) + "\n")


def final(stem: str, attempt: str):
    spec = SPECS[stem]
    im = Image.open(WORK / f"{stem}-t{attempt}.png").convert("RGB")
    W, H = spec["size"]
    # cover-crop to the exact target aspect, then resize
    sw, sh = im.size
    tr = W / H
    if sw / sh > tr:
        nw = round(sh * tr)
        im = im.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
    elif sw / sh < tr:
        nh = round(sw / tr)
        im = im.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
    im = im.resize((W, H), Image.LANCZOS)
    limit = spec["kb"] * 1024
    for q in range(92, 39, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= limit:
            break
    out = WORK / f"{stem}.jpg"
    out.write_bytes(buf.getvalue())
    print(json.dumps({"stem": stem, "from": f"{stem}-t{attempt}.png", "out": str(out.relative_to(STUDIO)),
                      "px": [W, H], "quality": q, "bytes": buf.tell(), "limit": limit}))


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "gen":
        gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif c == "final":
        final(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
