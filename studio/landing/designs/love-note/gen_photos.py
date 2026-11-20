#!/usr/bin/env python3
"""Love Note Heart landing photos, grounded on our real engine render. Runs on the dev VM only.

    python3 gen_photos.py gen <stem> <try> ["extra prompt"]   -> img/raw/<stem>-t<try>.png (+ preview jpg)
    python3 gen_photos.py final <stem> <try>                  -> img/final/<stem>.jpg (size-capped)

Call shape reused from studio/_out/landing/gen_images.py (main tree): gemini-3-pro-image on the Vertex
global endpoint, responseModalities TEXT+IMAGE, imageConfig imageSize 2K, reference <= 1024 px.
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

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[2]
REF = STUDIO / "_out" / "designs" / "love-note" / "preview.png"
OUT = STUDIO / "_out" / "designs" / "love-note" / "img"
MODEL = "gemini-3-pro-image"

_creds = _project = None


def auth(force=False):
    global _creds, _project
    if _creds is None:
        _creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _project = os.environ.get("GOOGLE_CLOUD_PROJECT") or _project
    if force or not _creds.valid:
        _creds.refresh(AuthRequest())
    return _creds.token, _project


def headers():
    tok, proj = auth()
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "x-goog-user-project": proj}


def url():
    _, proj = auth()
    return (f"https://aiplatform.googleapis.com/v1beta1/projects/{proj}/locations/global"
            f"/publishers/google/models/{MODEL}:generateContent")


def small_ref() -> bytes:
    im = Image.open(REF)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


PRODUCT = (
    "THE PRODUCT (match the reference render exactly; it is our real CAD design): the 'Love Note Heart', a "
    "bedside keepsake about 16 cm wide, 17.5 cm tall and 10 cm deep. A plump, puffy heart with very soft, "
    "pillow-rounded edges, thick like a cushion, standing upright on a low soft pill-shaped base; the heart's "
    "lower point flows into the base through a short narrow neck. One continuous matte 3D-printed PLA shell in "
    "dusty blush rose pink (#EDB1BA), very faint fine print layer lines. On the heart's front face: one round "
    "frosted warm-white light window (about 4.5 cm across) in the upper middle, and below it one small thin "
    "engraved circle (about 1.6 cm) marking the touch spot. On the front of the base, the engraved letters "
    "'A + R' filled in deep berry (#9B4A5C), exactly those three characters. On the right end of the base, a "
    "small round speaker grille of concentric arc slots. Nothing else on it: no face, no eyes, no mouth, no "
    "ears, no buttons on the front, no screen, no logo. ")
RULES = (
    "RULES: Photorealistic photograph with real-world materials and natural light, believable, calm and "
    "premium, never CGI-looking and never a cheap stock-photo cliche. No human faces, no heads, no human "
    "figures, no photos or posters of people anywhere. No readable text, letters, numbers, logos or brand marks "
    "anywhere in the image except the engraved 'A + R' on the gadget's base. The gadget must keep the exact "
    "shape, proportions, colours and features of the reference render; do not add or remove features, do not "
    "add decorations, bows or stickers onto the gadget itself.")
HANDS = ("Hands only: at most one hand and forearm, cropped by the frame edge, natural anatomy with exactly five "
         "fingers. No face, head or body in frame. ")

SPECS = {
    "love-note-studio": {
        "aspect": "1:1", "px": (1200, 1200), "kb": 200,
        "prompt": PRODUCT +
        "SCENE: a photoreal studio product photograph on a seamless pale blush-cream paper background, a lighter, "
        "warmer tint of the gadget's own rose, with a soft gradient falling slightly darker toward the floor. Large "
        "soft box light from the upper left, a gentle soft contact shadow under the base. The round light window "
        "glows softly from within in warm white, subtle, not blown out. Three-quarter front view at a slightly "
        "raised eye level like the reference, the gadget centred with generous breathing room on all sides. "
        "Clean, minimal, gift-worthy. " + RULES,
    },
    "love-note-life": {
        "aspect": "4:3", "px": (1600, 1200), "kb": 230,
        "prompt": PRODUCT +
        "SCENE: a cosy bedroom in the evening. The heart stands on a light oak bedside table, slightly right of "
        "centre, seen from a three-quarter front view at table height like the reference. Its round light window "
        "glows a soft warm white and casts a gentle warm pool of light on the wood. Around it: two stacked "
        "hardback books with plain linen covers (no titles, no words), a small clear glass bud vase with one pale "
        "pink rose, and an opened shallow gift box lined with soft tissue paper, a loosened blush satin ribbon "
        "trailing across the table. " + HANDS + "One slender hand enters from the right edge of the frame, its "
        "index fingertip about to touch the small engraved circle on the heart's front, below the light window, "
        "without covering the light window or the 'A + R' letters. Warm lamplight, soft linen bedding and a "
        "window with dusk-blue light softly out of focus behind, shallow depth of field. Tender and tasteful, "
        "not kitsch. " + RULES,
    },
}


def gen(stem: str, attempt: int, extra: str = ""):
    sp = SPECS[stem]
    text = sp["prompt"] + ((" " + extra) if extra else "")
    body = {"contents": [{"role": "user", "parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(small_ref()).decode()}},
        {"text": text}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": {"aspectRatio": sp["aspect"], "imageSize": "2K"}}}
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
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    (OUT / "preview").mkdir(parents=True, exist_ok=True)
    raw = OUT / "raw" / f"{stem}-t{attempt}.png"
    im.save(raw)
    pv = im.copy()
    pv.thumbnail((1400, 1400), Image.LANCZOS)
    pv.save(OUT / "preview" / f"{stem}-t{attempt}.jpg", "JPEG", quality=88)
    log.update(raw=str(raw), size=list(im.size))
    with open(OUT / "gen-log.jsonl", "a") as f:
        f.write(json.dumps({**log, "prompt": text}) + "\n")
    print(json.dumps(log))


def final(stem: str, attempt: str):
    sp = SPECS[stem]
    W, H = sp["px"]
    im = Image.open(OUT / "raw" / f"{stem}-t{attempt}.png").convert("RGB")
    # centre-crop to the exact target aspect, then resize
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
    limit = sp["kb"] * 1024
    for q in range(92, 39, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= limit:
            break
    out = OUT / "final" / f"{stem}.jpg"
    out.write_bytes(buf.getvalue())
    print(json.dumps({"stem": stem, "from": f"raw/{stem}-t{attempt}.png", "px": [W, H], "quality": q,
                      "bytes": buf.tell(), "limit_bytes": limit}))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "final":
        final(sys.argv[2], sys.argv[3])
