#!/usr/bin/env python3
"""Pill-reminder landing photos from our real preview render (Nano Banana Pro on Vertex, global endpoint).

Runs on the dev VM only (tools/vm.sh run), never on the laptop: Vertex bills the main project there.
Call shape reused from studio/_out/landing/gen_images.py (v1beta1, TEXT+IMAGE, imageSize 2K).

    python landing/designs/pill-reminder/work/gen_photos.py gen <stem> <try> ["extra prompt text"]
    python landing/designs/pill-reminder/work/gen_photos.py final <stem> <try>
Outputs under _out/designs/pill-reminder-photos/{ref,raw,preview,final}/.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path

import google.auth
import requests
from google.auth.transport.requests import Request as AuthRequest
from PIL import Image

STUDIO = Path(__file__).resolve().parents[4]
RENDER = STUDIO / "_out/designs/pill-reminder/preview.png"
OUT = STUDIO / "_out/designs/pill-reminder-photos"
MODEL = "gemini-3-pro-image"
API = "v1beta1"

_creds = None
_project = None


def auth(force=False):
    global _creds, _project
    if _creds is None:
        _creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
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


def ref_jpeg(max_px=1024) -> bytes:
    (OUT / "ref").mkdir(parents=True, exist_ok=True)
    im = Image.open(RENDER)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    p = OUT / "ref" / f"pill-reminder-render-{max_px}.jpg"
    im.save(p, "JPEG", quality=92, optimize=True)
    return p.read_bytes()


OBJECT = (
    "The reference image is a real 3D render of our product, a medicine reminder for grandparents. Reproduce "
    "EXACTLY this object, with the same shape, proportions, colours and features: a horizontal two-tone capsule "
    "(pill) shaped tabletop device about 25 cm long and 10 cm tall, lying on its side on a flat base. The left "
    "half is matte clinic white; the right half (the cap) is soft mint green, very slightly larger in diameter, "
    "with a crisp circular step where the two halves meet, exactly like a real two-part medicine capsule. A thin "
    "strip of mint shows along the very top of the white half. On the front of the white half: one small "
    "rectangular colour screen set in a thin mint frame. On top of the white half: one round speaker grille made "
    "of concentric rings. On top of the mint cap: one big round teal arcade push button. On the front of the mint "
    "cap: one small round cream-white light dot. Nothing else on the body: do not add or remove any button, "
    "hole, screen, light, seam, text, label or logo, and do not change any colour. Finish: smooth satin "
    "plastic. The screen shows a single simple flat icon of a white-and-teal capsule pill on a dark background "
    "and nothing else: no face, no eyes, no text, no numbers. "
)

SPECS = {
    "pill-reminder-studio": {
        "aspect": "1:1", "size": (1200, 1200), "kb": 200,
        "prompt": OBJECT + (
            "Make a photorealistic studio product photograph of it. Seamless background sweep in a very pale "
            "mint-grey that suits the white and mint body. Three-quarter view from the front right and slightly "
            "above, like the reference, the whole object in frame and centred with generous space around it. "
            "Large soft diffused key light from the upper left, gentle fill, a soft contact shadow under the "
            "object. Calm, clean, clinical-friendly premium product photography. No people, no hands, no props, "
            "no text, no logos, no watermark."),
    },
    "pill-reminder-life": {
        "aspect": "4:3", "size": (1600, 1200), "kb": 230,
        "prompt": OBJECT + (
            "Make a photorealistic lifestyle photograph, landscape 4:3, of it in use at home. It sits on a warm "
            "teak side table beside a cushioned armchair in a bright, tidy Indian living room in the morning. Next "
            "to it on the table: a weekly pill organiser with seven small coloured lids (plain lids, no letters), "
            "a steel tumbler of water, and a pair of folded reading glasses on a small embroidered cloth. A potted "
            "plant and a sunny window are softly out of focus behind. The device is the hero, sharp and in the "
            "front half of the frame, at its true size next to the tumbler. An elderly person's hand, with gentle "
            "wrinkled skin and a plain thin gold bangle, comes in from the right edge with one finger resting on "
            "the big teal button on top of the mint cap; only the hand and forearm are in frame, no face, no head, "
            "no other person. Soft natural window light, shallow depth of field, warm and calm. No legible text "
            "anywhere, no logos, no brand names, no labels on anything."),
    },
}


def gen(stem: str, attempt: int, extra: str = "") -> dict:
    spec = SPECS[stem]
    text = spec["prompt"] + (" " + extra if extra else "")
    body = {"contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(ref_jpeg()).decode()}},
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
        return log
    if r is None or r.status_code != 200:
        return log
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    imgs = [p for p in parts if "inlineData" in p and not p.get("thought")]
    log.pop("error", None)
    log.update(secs=round(time.time() - t, 1), finish=cand.get("finishReason"), modelVersion=d.get("modelVersion"),
               text=" ".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))[:300])
    if not imgs:
        log["error"] = "no image in response"
        return log
    im = Image.open(io.BytesIO(base64.b64decode(imgs[-1]["inlineData"]["data"]))).convert("RGB")
    for sub in ("raw", "preview"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    raw = OUT / "raw" / f"{stem}-t{attempt}.png"
    im.save(raw)
    pv = im.copy()
    pv.thumbnail((1400, 1400), Image.LANCZOS)
    pv.save(OUT / "preview" / f"{stem}-t{attempt}.jpg", "JPEG", quality=88)
    log.update(raw=str(raw.relative_to(STUDIO)), size=list(im.size))
    return log


def final(stem: str, attempt: str) -> dict:
    spec = SPECS[stem]
    W, H = spec["size"]
    im = Image.open(OUT / "raw" / f"{stem}-t{attempt}.png").convert("RGB")
    # centre-crop to the exact aspect, then resize
    ar = W / H
    if im.width / im.height > ar:
        nw = round(im.height * ar)
        x0 = (im.width - nw) // 2
        im = im.crop((x0, 0, x0 + nw, im.height))
    elif im.width / im.height < ar:
        nh = round(im.width / ar)
        y0 = (im.height - nh) // 2
        im = im.crop((0, y0, im.width, y0 + nh))
    im = im.resize((W, H), Image.LANCZOS)
    (OUT / "final").mkdir(parents=True, exist_ok=True)
    limit = spec["kb"] * 1000  # decimal KB, the stricter reading
    for q in range(92, 49, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= limit:
            break
    out = OUT / "final" / f"{stem}.jpg"
    out.write_bytes(buf.getvalue())
    return {"stem": stem, "from": f"raw/{stem}-t{attempt}.png", "out": str(out.relative_to(STUDIO)), "px": [W, H],
            "quality": q, "bytes": buf.tell(), "limit_bytes": limit}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        res = gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    else:
        res = final(sys.argv[2], sys.argv[3])  # attempt may be "2r" (retouched by retouch_tabs.py)
    print(json.dumps(res))
    with open(OUT / "log.jsonl", "a") as f:
        f.write(json.dumps(res) + "\n")
