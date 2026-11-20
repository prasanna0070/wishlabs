#!/usr/bin/env python3
"""desk-bot landing photos on Vertex (gemini-3-pro-image), grounded on our real preview render.

Runs on the dev VM only (tools/vm.sh run), never on the laptop. Call shape reused from
studio/_out/landing/gen_images.py (global endpoint, TEXT+IMAGE, imageSize 2K).

    python landing/designs/desk-bot/gen_photos.py gen <stem>[,<stem>] <try>
    python landing/designs/desk-bot/gen_photos.py final <stem>:<try>[,...]
Reference: _out/designs/desk-bot/preview.png (engine preview of landing/designs/desk-bot/spec.json).
Raw PNGs -> _out/designs/desk-bot/photos/raw/, finals -> _out/designs/desk-bot/photos/final/.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import google.auth
import requests
from google.auth.transport.requests import Request as AuthRequest
from PIL import Image

HERE = Path(__file__).resolve().parent                    # studio/landing/designs/desk-bot
STUDIO = HERE.parents[2]                                  # studio/
OUT = STUDIO / "_out" / "designs" / "desk-bot"
REF = OUT / "preview.png"
WORK = OUT / "photos"
RAW = WORK / "raw"
FINAL = WORK / "final"
MODEL = os.environ.get("IMAGE_MODEL", "gemini-3-pro-image")

_creds = None
_project = None
_lock = threading.Lock()


def auth(force=False):
    global _creds, _project
    with _lock:
        if _creds is None:
            _creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            _project = os.environ.get("GOOGLE_CLOUD_PROJECT") or _project
        if force or not _creds.valid:
            _creds.refresh(AuthRequest())
        return _creds.token, _project


def headers():
    tok, proj = auth()
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "x-goog-user-project": proj}


def url(model):
    _, proj = auth()
    return (f"https://aiplatform.googleapis.com/v1beta1/projects/{proj}/locations/global"
            f"/publishers/google/models/{model}:generateContent")


def small_ref(max_px=1024) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"ref-{max_px}.jpg"
    im = Image.open(REF)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    im.save(out, "JPEG", quality=92, optimize=True)
    return out


BOT = ("THE PRODUCT (match reference image 1 exactly; it is a real CAD render of our design): 'Bolt', a small "
       "desk robot about 13 cm wide and 19 cm tall including its antenna. A boxy head shaped like a rounded "
       "cube (13 cm wide, 9 cm tall, 10 cm deep) sits on a slightly narrower rounded-box body (10 cm wide, "
       "6 cm tall) joined by a short round neck. Matte 3D-printed PLA shell in chalk white (#F2F0EB) with very "
       "faint fine horizontal print layer lines. One thin antenna with a round ball tip stands centred on top "
       "of the head, same chalk white. Head front: a recessed graphite (#2B2E33) rounded-rectangle visor "
       "panel; centred in it a small landscape colour LCD screen in a thin light grey frame, showing a simple "
       "cartoon face on black: two big white eyes with dark pupils and highlights, a tiny smile and two small "
       "pink cheek dots. A tiny dark rectangular sensor window sits centred just above the screen inside the "
       "visor. On the robot's left side of the head (the side facing the viewer's right in the reference) is "
       "one round signal-orange (#FF6A13) push button. Body front: a recessed signal-orange rounded-rectangle "
       "chest panel with a small round cream status light at its top centre and a speaker grille of short "
       "horizontal slots below it. Two small rounded arm nubs on the sides of the body. Nothing else: no "
       "ears, no mouth on the shell, no blush on the shell, no wheels, no legs, no extra buttons, no logos, "
       "no text. Keep its exact shape, proportions, colours and features. ")

RULES = ("RULES: Photorealistic photograph with real-world materials and natural light, believable and "
         "premium, never CGI-looking and never a stock-photo cliche. No people, no human faces, no heads, no "
         "human figures, no photos, posters or screens showing people anywhere. No readable text, letters, "
         "numbers, logos or brand marks anywhere in the image, including on keyboards, monitors, headphones "
         "and mugs. The robot must keep the exact shape, proportions, colours and features of the reference "
         "render.")

HANDS = ("Hands only: at most one or two hands and forearms, cropped by the frame edge, with natural anatomy "
         "(exactly five fingers per hand). No face, head or body in frame. ")

SPECS = {
    "desk-bot-studio": {
        "aspect": "1:1", "w": 1200, "h": 1200, "kb": 200,
        "prompt": BOT + "SCENE: a clean photoreal studio product photograph. The robot stands on a seamless "
        "matte cool slate-grey paper background (a calm mid grey with a faint blue cast) that sweeps up behind "
        "it. Soft large softbox key light from the front left, a subtle cool rim light on the right edge, a "
        "soft natural contact shadow under it. Three-quarter front view from slightly above, the same angle "
        "as the reference, robot centred with generous breathing room, sharp focus throughout. Crisp, "
        "techy, minimal, like a hardware launch photo. " + RULES,
    },
    "desk-bot-life": {
        "aspect": "4:3", "w": 1600, "h": 1200, "kb": 230,
        "prompt": BOT + "SCENE: a teen gamer's desk setup in the evening. The robot stands on a dark grey "
        "felt desk mat beside a compact mechanical keyboard with plain graphite and white keycaps (blank "
        "keycaps, no legends, no logos), a little to the right of the keyboard. Behind it, out of focus, a "
        "large monitor glows with soft abstract blue and violet colour shapes (no text, no interface, no "
        "people), a pair of matte black over-ear headphones hangs on a simple stand, a small potted plant "
        "and a ceramic mug. Warm desk-lamp light plus a soft cool glow from an LED strip behind the monitor; "
        "cosy, modern, tidy. The robot's small cream chest light glows softly. " + HANDS + "One hand rests "
        "relaxed on the keyboard at the left edge of the frame, as if the person has just sat down. "
        "Three-quarter front view at desk height, similar to the reference angle, the robot sharp and "
        "clearly the hero in the right-centre of the frame, shallow depth of field. " + RULES,
    },
}

EXTRAS: dict = {
    ("desk-bot-studio", 2): (
        "CORRECTION: the recessed visor panel around the screen is very dark matte graphite, nearly black "
        "(#2B2E33), exactly as dark as in the reference render. It is NOT mid grey or light grey."),
    ("desk-bot-life", 2): (
        "CORRECTIONS: every keycap is completely blank and smooth, with no letters, numbers, symbols or "
        "legends at all. The speaker slots on the orange chest panel are plain dark openings and do NOT "
        "glow; only the small round cream status light above them glows softly. The visor around the "
        "screen is very dark graphite, nearly black, as in the reference."),
    ("desk-bot-life", 3): (
        "CORRECTIONS: (1) The visor panel around the robot's screen is very dark matte graphite, nearly "
        "black, exactly as in the reference render; it is NOT mid grey or light grey. (2) Every keycap is "
        "completely blank and smooth: no letters, numbers, symbols or legends on any key. (3) The speaker "
        "slots on the orange chest panel are plain dark openings that do NOT glow; only the small round cream "
        "status light above them glows softly. (4) The monitor is far behind and strongly out of focus; its "
        "whole screen shows only a soft abstract blue-violet gradient wave, edge to edge, with NO taskbar, "
        "NO icons, NO windows, NO interface and NO text. The monitor is a plain unbranded black panel with "
        "a thin blank bezel: no logo, badge or lettering anywhere on it. The headphones, mug and lamp carry "
        "no logos or marks."),
}


def generate(stem, attempt, extra=""):
    spec = SPECS[stem]
    extra = " ".join(x for x in (EXTRAS.get((stem, attempt), ""), extra) if x)
    ref = small_ref()
    parts = [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(ref.read_bytes()).decode()}},
             {"text": spec["prompt"] + (" " + extra if extra else "")}]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": spec["aspect"], "imageSize": "2K"}}}
    log = {"stem": stem, "attempt": attempt, "model": MODEL, "ref": str(ref.relative_to(STUDIO)), "extra": extra}
    t = time.time()
    r = None
    for k in range(4):
        try:
            r = requests.post(url(MODEL), headers=headers(), json=body, timeout=420)
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
    outp = cand.get("content", {}).get("parts", [])
    imgs = [p for p in outp if "inlineData" in p and not p.get("thought")]
    log.update(secs=round(time.time() - t, 1), finish=cand.get("finishReason"), modelVersion=d.get("modelVersion"),
               text=" ".join(p.get("text", "") for p in outp if "text" in p and not p.get("thought"))[:300])
    log.pop("error", None)
    if not imgs:
        log["error"] = "no image in response"
        return log
    im = Image.open(io.BytesIO(base64.b64decode(imgs[-1]["inlineData"]["data"]))).convert("RGB")
    RAW.mkdir(parents=True, exist_ok=True)
    raw = RAW / f"{stem}-t{attempt}.png"
    im.save(raw)
    pv = im.copy()
    pv.thumbnail((1400, 1400), Image.LANCZOS)
    pv.save(RAW / f"{stem}-t{attempt}-view.jpg", "JPEG", quality=88)
    log.update(raw=str(raw.relative_to(STUDIO)), size=list(im.size))
    return log


def cmd_gen(stems, attempt, extra=""):
    small_ref()
    logs = []
    with cf.ThreadPoolExecutor(2) as ex:
        for lg in ex.map(lambda s: generate(s, attempt, extra), [s for s in stems.split(",") if s]):
            logs.append(lg)
            print(json.dumps(lg))
    with open(WORK / "gen-log.jsonl", "a") as f:
        for lg in logs:
            f.write(json.dumps(lg) + "\n")


# Deterministic clean-up after the 3-try cap (no model call). desk-bot-life t3: two small blurred
# system-tray icons on the out-of-focus monitor's bottom strip (scene only, not the gadget). The plain
# strip just left of them is copied over them through a feathered mask; nothing else changes.
RETOUCH = {
    ("desk-bot-life", 3): [{"dst": (2318, 548, 2400, 600), "src_dx": -120}],
}


def cmd_retouch(stem, attempt):
    from PIL import ImageDraw, ImageFilter
    im = Image.open(RAW / f"{stem}-t{attempt}.png").convert("RGB")
    out = im.copy()
    for r in RETOUCH[(stem, attempt)]:
        x0, y0, x1, y1 = r["dst"]
        dx = r["src_dx"]
        patch = im.crop((x0 + dx, y0, x1 + dx, y1))
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rectangle((x0 + 6, y0 + 6, x1, y1 - 6), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(4))
        layer = im.copy()
        layer.paste(patch, (x0, y0))
        out = Image.composite(layer, out, mask)
    dst = RAW / f"{stem}-t{attempt}r.png"
    out.save(dst)
    x0, y0, x1, y1 = RETOUCH[(stem, attempt)][0]["dst"]
    for tag, img in (("before", im), ("after", out)):
        c = img.crop((x0 - 260, y0 - 80, min(img.width, x1 + 40), y1 + 80))
        c.resize((c.width * 2, c.height * 2), Image.LANCZOS).save(RAW / f"retouch-{stem}-t{attempt}-{tag}.jpg", quality=90)
    print(json.dumps({"retouched": str(dst.relative_to(STUDIO))}))


def cmd_final(stem, attempt):
    spec = SPECS[stem]
    im = Image.open(RAW / f"{stem}-t{attempt}.png").convert("RGB")
    W, H = spec["w"], spec["h"]
    # cover-crop to the exact target aspect, then resize
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x0, y0 = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((x0, y0, x0 + W, y0 + H))
    FINAL.mkdir(parents=True, exist_ok=True)
    out = FINAL / f"{stem}.jpg"
    limit = spec["kb"] * 1000   # decimal kB: passes both 1000- and 1024-byte readings
    for q in range(92, 49, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= limit:
            break
    out.write_bytes(buf.getvalue())
    rec = {"stem": stem, "from": f"raw/{stem}-t{attempt}.png", "out": str(out.relative_to(STUDIO)), "px": [W, H],
           "quality": q, "bytes": buf.tell(), "limit_bytes": limit}
    print(json.dumps(rec))
    with open(WORK / "final-log.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "gen":
        cmd_gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif c == "retouch":
        cmd_retouch(sys.argv[2], int(sys.argv[3]))
    elif c == "final":
        for s in sys.argv[2].split(","):
            st, _, att = s.partition(":")
            cmd_final(st, att)
    else:
        sys.exit(__doc__)
