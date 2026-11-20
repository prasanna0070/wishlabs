#!/usr/bin/env python3
"""shop-greeter landing photos on Vertex (gemini-3-pro-image, global), grounded on our real preview render.

Runs on the dev VM only (tools/vm.sh run), never on the laptop. Call shape copied from
studio/_out/landing/gen_images.py (TEXT+IMAGE modalities, imageConfig aspectRatio + imageSize 2K).

    python gen.py ref                       # crop + downscale the render to refs/render-ref.jpg (<= 1024 px)
    python gen.py gen <stem> <try> [extra]  # stem = studio | life -> photos/raw/shop-greeter-<stem>-t<try>.png
    python gen.py final <stem>:<try>[,...]  # raw PNG -> photos/final/shop-greeter-<stem>.jpg (size + KB caps)
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

STUDIO = Path(__file__).resolve().parents[4]          # studio/
RENDER = STUDIO / "_out" / "designs" / "shop-greeter" / "preview.png"
OUT = STUDIO / "_out" / "designs" / "shop-greeter" / "photos"
REF = OUT / "refs" / "render-ref.jpg"
MODEL = "gemini-3-pro-image"

KEEP = (
    "Keep the product IDENTICAL to the reference render. Same shape and proportions: a marigold-yellow arched signboard "
    "with a gently curved top, about as thick as a paperback book, standing on a wider, deeper rounded rectangular "
    "marigold base like a step. Same parts in the same places, nothing added, removed or moved: a plain round cream "
    "light diffuser near the top of the arch; below it a recessed deep sindoor-red band across the board with the "
    "engraved marigold letters KIRAN STORES; below the band a small round patch of tiny speaker holes; on the front of "
    "the base a recessed peacock-teal band with the engraved marigold letters WELCOME, a small engraved circle at the "
    "left end of the teal band and a small square sensor window at its right end. Same colours: marigold-yellow body, "
    "deep sindoor-red band, peacock-teal band, cream diffuser. The letters must read exactly KIRAN STORES and WELCOME "
    "in a bold sans-serif, nothing else. No screen, no face, no eyes, no buttons, no screws, no extra lights, no "
    "ornaments. Material: smooth matte 3D-printed plastic with a fine satin finish and softly rounded edges."
)

SPECS = {
    "studio": {
        "aspect": "1:1", "width": 1200, "height": 1200, "kb": 200,
        "prompt": (
            "The attached image is a CAD render of a real product we make: a countertop shop greeter sign that lights "
            "up and plays a welcome chime when a customer walks in. Create a photorealistic studio product photograph "
            "of exactly this object. " + KEEP + " Scene: a seamless matte paper backdrop in a muted deep teal, darker "
            "and greyer than the product's teal band, with a soft vertical gradient; the product stands on the same "
            "seamless surface with a soft natural contact shadow. Three-quarter front view from slightly above, close "
            "to the reference angle, product centred and filling about 60 percent of the frame. Large soft key light "
            "from the upper left, gentle fill, a subtle rim light on the right edge. The diffuser is unlit, plain cream. "
            "No people, no faces, no hands, no other objects or props, no logos, no extra text, no watermark."
        ),
    },
    "life": {
        "aspect": "4:3", "width": 1600, "height": 1200, "kb": 230,
        "prompt": (
            "The attached image is a CAD render of a real product we make: a countertop shop greeter sign that lights "
            "up and plays a welcome chime when a customer walks in. Create a photorealistic lifestyle photograph of "
            "exactly this object in use in a small, tidy Indian neighbourhood shop. " + KEEP + " Scene: the product "
            "stands near the front edge of the shop's worn wooden counter, facing the entrance. Its round diffuser "
            "glows a soft warm marigold light, as if a customer has just walked in. Background in soft focus: the open "
            "shop doorway full of bright morning daylight, a fresh orange and yellow marigold flower garland strung "
            "across the top of the doorway, and wooden shelves with neatly stacked plain unlabelled glass jars, steel "
            "containers and folded cloth. On the counter, a small steel tray with a few coins and a folded cotton "
            "cloth. One shopkeeper's hand rests flat on the counter at the right edge of the frame (only the hand and "
            "wrist, no arm above the wrist, no body, no face). Camera at counter height, three-quarter front view, "
            "the product sharp in the left-centre foreground and about one third of the frame width, shallow depth of "
            "field, warm natural light. No faces anywhere, no other people. No readable text, letters, numbers or "
            "labels on any jar, box, shelf, sign or poster; every other surface is plain. No logos, no watermark. The "
            "only text in the whole image is the engraved KIRAN STORES and WELCOME on the product."
        ),
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
    return (f"https://aiplatform.googleapis.com/v1beta1/projects/{proj}/locations/global"
            f"/publishers/google/models/{MODEL}:generateContent")


def cmd_ref():
    im = Image.open(RENDER)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im = im.crop((140, 150, 885, 955))          # the product with a margin (render is 1024 x 1024)
    im.thumbnail((1024, 1024), Image.LANCZOS)
    REF.parent.mkdir(parents=True, exist_ok=True)
    im.save(REF, "JPEG", quality=92, optimize=True)
    print(json.dumps({"ref": str(REF.relative_to(STUDIO)), "size": im.size, "bytes": REF.stat().st_size}))


def cmd_gen(stem, attempt, extra=""):
    spec = SPECS[stem]
    text = spec["prompt"] + (" " + extra if extra else "")
    parts = [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(REF.read_bytes()).decode()}},
             {"text": text}]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": spec["aspect"], "imageSize": "2K"}}}
    log = {"stem": stem, "attempt": attempt, "model": MODEL, "extra": extra}
    t = time.time()
    r = None
    for k in range(4):
        try:
            r = requests.post(url(), headers={"Authorization": f"Bearer {auth()[0]}", "Content-Type": "application/json",
                                              "x-goog-user-project": auth()[1]}, json=body, timeout=420)
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
    outp = cand.get("content", {}).get("parts", [])
    imgs = [p for p in outp if "inlineData" in p and not p.get("thought")]
    log.update(secs=round(time.time() - t, 1), finish=cand.get("finishReason"), modelVersion=d.get("modelVersion"),
               text=" ".join(p.get("text", "") for p in outp if "text" in p and not p.get("thought"))[:300])
    log.pop("error", None)
    if not imgs:
        log["error"] = "no image in response"
        print(json.dumps(log))
        return
    im = Image.open(io.BytesIO(base64.b64decode(imgs[-1]["inlineData"]["data"]))).convert("RGB")
    raw = OUT / "raw" / f"shop-greeter-{stem}-t{attempt}.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    im.save(raw)
    pv = im.copy()
    pv.thumbnail((1400, 1400), Image.LANCZOS)
    (OUT / "preview").mkdir(parents=True, exist_ok=True)
    pv.save(OUT / "preview" / f"shop-greeter-{stem}-t{attempt}.jpg", "JPEG", quality=88)
    log.update(raw=str(raw.relative_to(STUDIO)), size=list(im.size))
    print(json.dumps(log))
    with open(OUT / "gen-log.jsonl", "a") as f:
        f.write(json.dumps(log | {"prompt": text}) + "\n")


def cmd_final(stem, attempt):
    spec = SPECS[stem]
    im = Image.open(OUT / "raw" / f"shop-greeter-{stem}-t{attempt}.png").convert("RGB")
    W, H = spec["width"], spec["height"]
    # cover-crop to the exact aspect, then resize
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x0, y0 = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((x0, y0, x0 + W, y0 + H))
    limit = spec["kb"] * 1024
    for q in range(92, 49, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= limit:
            break
    out = OUT / "final" / f"shop-greeter-{stem}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    rec = {"stem": stem, "from": f"raw/shop-greeter-{stem}-t{attempt}.png", "out": str(out.relative_to(STUDIO)),
           "px": [W, H], "quality": q, "bytes": buf.tell(), "limit_bytes": limit}
    print(json.dumps(rec))
    with open(OUT / "final-log.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "ref":
        cmd_ref()
    elif c == "gen":
        cmd_gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif c == "final":
        for s in sys.argv[2].split(","):
            st, _, att = s.partition(":")
            cmd_final(st, att)
    else:
        sys.exit(__doc__)
