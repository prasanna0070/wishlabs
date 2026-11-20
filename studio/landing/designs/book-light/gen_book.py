#!/usr/bin/env python3
"""Book-light landing photos on Vertex (Nano Banana Pro), grounded on our real preview render.

Runs on the dev VM only (tools/vm.sh run), never on the laptop: Vertex bills the main project there.
Call shape reused from studio/_out/landing/gen_images.py (global endpoint, TEXT+IMAGE, imageSize 2K).

    python gen_book.py gen <stem> <try> [extra]     -> _out/designs/book-light/img/<stem>-t<try>.png
    python gen_book.py final <stem> <try>           -> _out/designs/book-light/img/<stem>.jpg (size + KB caps)
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
REF = STUDIO / "_out" / "designs" / "book-light" / "preview.png"
OUT = STUDIO / "_out" / "designs" / "book-light" / "img"
MODEL = "gemini-3-pro-image"

OBJECT = (
    "The attached image is a render of our real product. Reproduce THIS EXACT OBJECT faithfully: an upright, closed, "
    "chunky hardback-book-shaped lamp about 21 cm tall, 15 cm deep and 9 cm thick, 3D printed in matte deep forest green. "
    "Facing the camera is its page edge: a cream panel with fine vertical tan page lines, framed by the two green cover "
    "boards that stand slightly proud of it. Set into the upper part of the cream page edge is one round diffuser window. "
    "On the green cover side there is one small round speaker grille made of concentric ring slots. On the top there is one "
    "small round cream button, and the back has a rounded book spine. Keep the exact shape, proportions, colours, the "
    "number of features and their positions exactly as in the reference; do not add, remove, move or restyle any part; "
    "no extra buttons, screens, cables or lights. No text, titles, letters, numbers, logos or labels anywhere on the "
    "object. Same three-quarter viewpoint as the reference (page edge facing the viewer, green cover on the right)."
)

SPECS = {
    "book-light-studio": {
        "aspect": "1:1", "px": (1200, 1200), "kb": 200,
        "prompt": OBJECT + " Scene: a clean premium studio product photograph on a seamless warm oat-beige paper "
        "background that complements the forest green, soft diffused key light from the front left, a gentle fill, a soft "
        "natural contact shadow on the floor. The round window glows with a soft warm 2700 K light. Subtle realistic matte "
        "3D-print surface. Nothing else in the frame: no props, no people, no hands, no text. Photorealistic, high-end "
        "design-store catalogue look, sharp focus on the whole object.",
    },
    "book-light-life": {
        "aspect": "4:3", "px": (1600, 1200), "kb": 230,
        "prompt": OBJECT + " Scene: a cosy bedside table at night in a calm Indian home, photographed as a real lifestyle "
        "photo. The book lamp stands on a warm walnut bedside table next to a short stack of three plain hardback books "
        "whose spines and covers are solid colours with no lettering at all, a pair of reading glasses folded on top, and a "
        "small glass of tea. Its round window glows warm amber and casts a soft pool of light on the table; the room behind "
        "is dim, softly out of focus, with a hint of a linen pillow and a knitted throw. Optionally one adult hand is "
        "resting near the lamp, reaching toward the top button; no face, no head and no body other than the hand. No text, "
        "words, letters, logos or brand marks anywhere in the image (books, glass and walls are blank). Photorealistic, "
        "warm, quiet, cinematic, shallow depth of field, the lamp in sharp focus.",
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
    return f"https://aiplatform.googleapis.com/v1beta1/projects/{proj}/locations/global/publishers/google/models/{MODEL}:generateContent"


def ref_part() -> dict:
    im = Image.open(REF).convert("RGB")
    im.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def gen(stem: str, attempt: int, extra: str = ""):
    sp = SPECS[stem]
    text = sp["prompt"] + (" " + extra if extra else "")
    body = {"contents": [{"role": "user", "parts": [ref_part(), {"text": text}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": sp["aspect"], "imageSize": "2K"}}}
    log = {"stem": stem, "attempt": attempt, "model": MODEL, "extra": extra}
    t = time.time()
    r = None
    for k in range(4):
        tok, proj = auth()
        try:
            r = requests.post(url(), headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                                              "x-goog-user-project": proj}, json=body, timeout=420)
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
    log.update(secs=round(time.time() - t, 1), finish=cand.get("finishReason"), modelVersion=d.get("modelVersion"))
    log.pop("error", None)
    if not imgs:
        log["error"] = "no image in response"
        print(json.dumps(log))
        return
    im = Image.open(io.BytesIO(base64.b64decode(imgs[-1]["inlineData"]["data"]))).convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    raw = OUT / f"{stem}-t{attempt}.png"
    im.save(raw)
    log.update(raw=str(raw), size=list(im.size))
    print(json.dumps(log))
    with open(OUT / "gen-log.jsonl", "a") as f:
        f.write(json.dumps(log) + "\n")


def final(stem: str, attempt: str):
    sp = SPECS[stem]
    im = Image.open(OUT / f"{stem}-t{attempt}.png").convert("RGB")
    W, H = sp["px"]
    # centre-crop to the target aspect, then resize
    ar = W / H
    if im.width / im.height > ar:
        nw = round(im.height * ar)
        im = im.crop(((im.width - nw) // 2, 0, (im.width - nw) // 2 + nw, im.height))
    elif im.width / im.height < ar:
        nh = round(im.width / ar)
        im = im.crop((0, (im.height - nh) // 2, im.width, (im.height - nh) // 2 + nh))
    im = im.resize((W, H), Image.LANCZOS)
    limit = sp["kb"] * 1024
    for q in range(92, 39, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= limit:
            break
    out = OUT / f"{stem}.jpg"
    out.write_bytes(buf.getvalue())
    print(json.dumps({"stem": stem, "from": f"{stem}-t{attempt}.png", "out": str(out), "px": [W, H], "quality": q,
                      "bytes": buf.tell(), "limit_bytes": limit}))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "final":
        final(sys.argv[2], sys.argv[3])
