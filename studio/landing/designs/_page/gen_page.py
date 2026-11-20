#!/usr/bin/env python3
"""Landing page images that show RANGE: one hero lineup of six real designs, and four how-it-works steps,
each step a different gadget. Every gadget is grounded on its real engine preview render.

Runs on the dev VM only (tools/vm.sh run), never on the laptop: Vertex bills the main project there.

    python3 landing/designs/_page/gen_page.py gen <stem>[,<stem>] <try>   # -> _out/page/raw/<stem>-t<try>.png
    python3 landing/designs/_page/gen_page.py crop <stem>-t<try> x0,y0,x1,y1 [scale]  # zoom crop for review
    python3 landing/designs/_page/gen_page.py final <stem>:<try>[,...]    # -> _out/page/final/<stem>.jpg

References: _out/designs/<slug>/preview.png in this frozen copy (the engine's preview renders; byte-identical to
the main tree's landing/designs/<slug>/render.png), downscaled to <= 1024 px JPEG.
Model: gemini-3-pro-image (Nano Banana Pro), Vertex global endpoint, TEXT+IMAGE, imageSize 2K (same call shape as
the earlier landing script, studio/_out/landing/gen_images.py in the main tree).
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

STUDIO = Path(__file__).resolve().parents[3]          # .../consumer-hardware-platform-stable/studio
OUT = STUDIO / "_out" / "page"
RAW, PREVIEW, FINAL, REFS, CROPS = (OUT / d for d in ("raw", "preview", "final", "refs", "crops"))
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


def url():
    _, proj = auth()
    return (f"https://aiplatform.googleapis.com/v1beta1/projects/{proj}/locations/global"
            f"/publishers/google/models/{MODEL}:generateContent")


def ref(slug: str, max_px: int = 1024) -> Path:
    """The engine's preview render for <slug>, as a <= max_px JPEG on its own light background."""
    REFS.mkdir(parents=True, exist_ok=True)
    out = REFS / f"{slug}-{max_px}.jpg"
    if not out.exists():
        im = Image.open(STUDIO / "_out" / "designs" / slug / "preview.png")
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (243, 242, 239))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        tmp = out.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        im.save(tmp, "JPEG", quality=92, optimize=True)
        os.replace(tmp, out)
    return out


def inline(p: Path) -> dict:
    return {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(p.read_bytes()).decode()}}


# ------------------------------------------------------------------ the six designs, as the photo agents described them
DESK_BOT = (
    "'Bolt', a desk robot about 13 cm wide and 19 cm tall including its antenna. A boxy head shaped like a rounded cube "
    "(13 cm wide, 9 cm tall, 10 cm deep) sits on a slightly narrower rounded-box body joined by a short round neck. "
    "Matte chalk-white (#F2F0EB) 3D-printed shell. One thin antenna with a round ball tip centred on top of the head. "
    "Head front: a recessed graphite (#2B2E33) rounded-rectangle visor panel; centred in it a small landscape colour "
    "screen in a thin light-grey frame; a tiny dark sensor window just above the screen inside the visor. One round "
    "signal-orange (#FF6A13) push button on the side of the head (the viewer's right in the reference). Body front: a "
    "recessed signal-orange rounded-rectangle chest panel with a small round cream status light at its top and a grille "
    "of short horizontal slots. Two small rounded nubs on the body sides. No ears, no mouth or blush on the shell.")
DESK_BOT_FACE = ("Its screen shows a simple cartoon face on black: two big white eyes with dark pupils, a tiny smile "
                 "and two small pink cheek dots.")

BOOK_LIGHT = (
    "'Folio', an upright, closed, chunky hardback-book-shaped lamp about 21 cm tall, 15 cm deep and 9 cm thick, matte "
    "deep forest green (#3B5F4C). Its page edge is a cream (#F2EADB) panel with fine vertical tan page lines, framed by "
    "the two green cover boards that stand slightly proud of it. One round cream diffuser window in the upper part of "
    "the page edge. One small round speaker grille of concentric ring slots on a green cover side. One small round "
    "cream button on top. A rounded book spine at the back. No title, no letters, no other features.")

LOVE_NOTE = (
    "the 'Love Note Heart', about 16 cm wide, 17.5 cm tall and 10 cm deep: a plump, puffy heart with very soft "
    "pillow-rounded edges, thick like a cushion, standing upright on a low soft pill-shaped base; the heart's lower "
    "point flows into the base through a short narrow neck. One continuous matte dusty blush-rose (#EDB1BA) shell. On "
    "the heart's front: one round frosted warm-white light window in the upper middle, and below it one small thin "
    "engraved circle marking the touch spot. On the front of the base the engraved letters 'A + R' in deep berry "
    "(#9B4A5C), exactly those three characters. A small round speaker grille of concentric arcs on the right end of "
    "the base. No face, no eyes, no screen, no logo.")

PILL = (
    "'Dose', a medicine reminder: a horizontal two-tone capsule (pill) shaped device about 25 cm long and 10 cm tall, "
    "lying on its side. The left half is matte clinic white; the right half (the cap) is soft mint (#A6DCCD), very "
    "slightly larger in diameter, with a crisp circular step where the halves meet, like a real medicine capsule. A thin "
    "strip of mint along the very top of the white half. On the front of the white half: one small rectangular colour "
    "screen in a thin mint frame. On top of the white half: one round speaker grille of concentric rings. On top of the "
    "mint cap: one big round teal (#16897E) push button. On the front of the mint cap: one small round cream light dot.")
PILL_ICON = ("Its screen shows a single simple flat icon of a white-and-teal capsule pill on a dark background and "
             "nothing else: no face, no eyes, no text, no numbers.")

SHOP = (
    "'Swagat', a countertop shop greeter: a marigold-yellow (#F4A81D) arched signboard about 20 cm wide and 20 cm tall "
    "with a gently curved top, standing on a wider, deeper rounded rectangular marigold base like a step. A plain round "
    "cream light diffuser near the top of the arch; below it a recessed deep sindoor-red (#B3122E) band with the "
    "engraved marigold letters KIRAN STORES; below the band a small round patch of tiny speaker holes; on the front of "
    "the base a recessed peacock-teal (#0B6E69) band with the engraved marigold letters WELCOME, a small engraved circle "
    "at its left end and a small square sensor window at its right end. The letters read exactly KIRAN STORES and "
    "WELCOME in a bold sans-serif.")

CUBE = (
    "'Pomo', a focus-timer cube about 10 cm on each side: matte charcoal-black (#2F3135) with softly rounded edges. On "
    "the front face: a large flat cream circular light diffuser inside a slightly raised tomato-red (#FF5A3C) ring, and "
    "below it the small engraved number 25 in warm grey. On the top face: a small round touch mark and a small circular "
    "grille of tiny dots. Nothing else.")

RULES = (
    "RULES: Photorealistic photograph with real-world materials and natural light, believable, calm and premium, never "
    "CGI-looking and never a stock-photo cliche. No human faces, no heads, no human figures, no photos, posters or screens "
    "showing people anywhere. No logos, brand marks, labels or readable text anywhere in the image, except the letters "
    "engraved on the gadgets themselves as described. Every gadget keeps the exact shape, proportions, colours and "
    "features of its reference render: do not add, remove, move or restyle any part, and do not add decorations to it. "
    "Fine realistic matte 3D-printed surfaces with barely visible layer lines.")

HANDS = ("Hands only: at most two hands and forearms, cropped by the frame edge, natural anatomy with exactly five "
         "fingers per hand. No face, head or body in frame. ")

SPECS = {
    "hero-lineup": {
        "aspect": "16:9", "width": 2400, "max_bytes": 400_000,
        "refs": ["@hero-layout", "desk-bot", "book-light", "love-note", "pill-reminder", "focus-cube", "shop-greeter"],
        "prompt": (
            "Image 1 is a layout mock-up made from our real CAD renders: six real gadgets we designed, cut out exactly "
            "and placed at their true relative sizes on flat stand-in colours. Images 2 to 7 are the full renders of "
            "the same six gadgets, left to right, for detail: (2) " + DESK_BOT + " " + DESK_BOT_FACE + " (3) " + BOOK_LIGHT
            + " (4) " + LOVE_NOTE + " (5) " + PILL + " (6) " + CUBE + " (7) " + SHOP + " "
            "TASK: turn image 1 into one photorealistic photograph. Keep the composition, the camera angle and every "
            "gadget exactly as in image 1: the same order, position, size, viewing angle, silhouette, colours, parts and "
            "engraved letters. Do not redesign, restyle, swap, rotate, mirror, resize or add parts to any gadget. Only "
            "replace the surroundings and the light: the flat beige band becomes a warm off-white limewashed plaster "
            "wall; the tan band becomes a long light oak table top with a fine natural grain; the brown band at the "
            "bottom becomes the table's front edge in soft shadow. Give the gadgets real matte 3D-printed materials with "
            "barely visible layer lines and real soft contact shadows on the table. Warm natural late-afternoon sunlight "
            "from a window on the left, soft long shadows falling to the right, a gentle warm tone, everything in sharp "
            "focus. The white-and-mint capsule's small screen shows a simple flat white-and-teal capsule pill icon on a "
            "dark background instead of a face. The round light windows of the green book lamp and the pink heart glow "
            "a soft warm white; every other light window stays plain unlit cream. Nothing else on the table or the "
            "wall. No people, no hands. " + RULES),
    },
    "step-manufacture": {
        "aspect": "3:2", "width": 1200, "max_bytes": 160_000,
        "refs": ["book-light"],
        "prompt": (
            "The attached image is a real CAD render of our book lamp: " + BOOK_LIGHT + " "
            "SCENE: a small, clean electronics and 3D-printing workbench in India, bright daylight from a window, white "
            "wall, real-world feel. The finished green book-lamp shell from the reference stands upright on the bench at "
            "the right, page edge toward the camera, exactly as the reference. In the foreground, " + HANDS + "The hands "
            "have a warm brown skin tone and are fitting the lamp's electronics: on a flat base tray in front of the shell "
            "sit a small purple circuit board with an ESP32-S3 development board plugged into two long rows of black "
            "header sockets, a small round board carrying a ring of 12 tiny LEDs, and a round 4 cm speaker, joined by short "
            "cables with small white keyed plugs; one hand presses a white keyed plug into the purple board. Nearby on "
            "the bench: fine tweezers, a small screwdriver and a spool of forest-green 3D-printer filament. Softly out of "
            "focus in the background: a plain unbranded 3D printer. No laptop, no screens, no paper with writing, no "
            "lettering or labels on the circuit boards (plain boards). Eye-level three-quarter view, natural colours, "
            "shallow depth of field with the hands, board and lamp sharp. " + RULES),
    },
    "step-receive": {
        "aspect": "3:2", "width": 1200, "max_bytes": 160_000,
        "refs": ["love-note"],
        "prompt": (
            "The attached image is a real CAD render of our gift gadget: " + LOVE_NOTE + " "
            "SCENE: an unboxing on a warm wooden table in soft daylight, seen from above at about 45 degrees. A plain "
            "brown kraft gift box is open, its lid set aside; a blush-pink satin ribbon, just untied, lies loosely across "
            "the table. Inside the box, nestled in soft cream tissue paper, the heart gadget from the reference stands "
            "upright with its front toward the camera, the engraved A + R on its base visible. " + HANDS + "The hands "
            "have a warm brown skin tone; one hand holds the kraft lid at the edge of the frame. Beside the box: a "
            "neatly coiled white USB-C cable and a small folded blank card with no writing. Warm, intimate, simple, no "
            "brand marks on the box or ribbon. " + RULES),
    },
    "step-modify": {
        "aspect": "3:2", "width": 1200, "max_bytes": 160_000,
        "refs": ["desk-bot"],
        "prompt": (
            "The attached image is a real CAD render of our desk robot: " + DESK_BOT + " "
            "Its screen now shows a DIFFERENT, happier expression in the same simple style: two white upward-curved "
            "closed-eye arcs (like ^ ^) and the same two small pink cheek dots on black, as if it just learned something "
            "new. SCENE: the robot stands on a light wooden desk in calm evening light from a warm table lamp. Beside it, "
            "a plain unbranded smartphone leans on a small stand, its screen showing only a simple chat: five rounded "
            "message bubbles, alternating left (light grey) and right (soft orange), the text inside every bubble shown "
            "only as soft blank bars, nothing legible, no status bar, no app icons. A mug and a small plant softly out of "
            "focus behind. No people. Three-quarter view slightly from above like the reference, natural colours, "
            "shallow depth of field. " + RULES),
    },
    "step-repurpose": {
        "aspect": "3:2", "width": 1200, "max_bytes": 160_000,
        "refs": ["pill-reminder"],
        "prompt": (
            "The attached image is a real CAD render of our medicine reminder: " + PILL + " "
            "Its small screen shows, instead of a face, one glowing teal circular progress ring about three-quarters "
            "complete, centred on a dark background, with no numbers and no text. SCENE: it sits on a light wooden side "
            "table near a window in soft morning daylight. A white USB-C cable is plugged into its back and loosely "
            "coiled beside it. Next to it, a square pale-yellow sticky note with one hand-drawn tick mark in dark pen and "
            "no words. Calm, minimal, lots of clean space. No people. Three-quarter front view at eye level like the "
            "reference, shallow depth of field. " + RULES),
    },
}

# Corrections added after looking at a failed attempt: (stem, try) -> extra prompt text.
EXTRAS: dict[tuple[str, int], str] = {
    ("step-modify", 2): (
        "CORRECTION: attempt 1 showed the robot mirrored. Keep the reference's exact viewpoint and handedness: we see "
        "the robot's front and the side that faces the viewer's RIGHT, and that right-hand side of the head carries the "
        "one round orange button; the side nub on the body is also on the viewer's right. The head's left side (as the "
        "viewer sees it) has no button. Put the phone on the LEFT of the robot and the lamp out of frame or on the far "
        "right."),
    ("step-receive", 2): (
        "CORRECTION: attempt 1 laid the heart down and distorted it. The heart stands UPRIGHT in a deep open box, seen "
        "from the front and slightly above, exactly the reference silhouette: a full plump two-lobed heart on top, its "
        "point flowing through a short narrow neck into a wide, low, soft pill-shaped base that is about as wide as the "
        "heart; the engraved A + R sits on the front of that base. Same dusty rose colour as the reference."),
    ("hero-lineup", 3): (
        "CORRECTIONS after attempt 2: (a) Use soft, diffused warm daylight, as through a sheer curtain: NO hard window "
        "or frame shadows and no sharp light-and-shadow edges falling across any gadget; each gadget is evenly lit like in "
        "image 1, with only a soft contact shadow on the table. (b) The white-and-mint capsule's white half is clean matte "
        "white all the way to its rounded left end, exactly like image 1; no grey or translucent end. (c) The robot's screen "
        "face is exactly as in image 1: two white eyes with dark pupils, a tiny smile and two small pink cheek dots on "
        "black; the tiny sensor window above the screen is a small dark rectangle. (d) The pink heart is one even dusty "
        "rose colour all over; below its light window the touch spot is a small thin engraved CIRCLE; the base reads "
        "exactly A + R with a plain plus sign. (e) Everything else stays exactly as in attempt 2's correct parts: the green "
        "book lamp, the charcoal cube with its red ring and 25, and the yellow sign reading KIRAN STORES and WELCOME."),
    ("step-receive", 3): (
        "CORRECTION after two attempts that laid the heart down and distorted it: the heart has just been taken out of "
        "the box and now STANDS UPRIGHT on the table in front of the open kraft box, seen from the same three-quarter "
        "front view as the reference render, so its silhouette matches the reference exactly: a full plump two-lobed "
        "cushion heart, its point flowing through a short narrow neck into a wide, low, soft pill-shaped base, the round "
        "light window centred in the upper middle of the heart, a small engraved circle below it, and A + R engraved on "
        "the front of the base. The opened box with crumpled cream tissue paper sits just behind it, its lid leaning "
        "beside; the untied blush-pink satin ribbon lies loosely in the foreground. One hand rests on the box lid at the "
        "edge of the frame. Camera slightly above table height, warm daylight."),
    ("step-repurpose", 2): (
        "CORRECTION: make it look like a real photograph taken with a camera, not a 3D render: real oak grain with "
        "small imperfections, natural window light with real falloff, a slightly textured matte print surface. The cap "
        "is a fresh pale mint (#A6DCCD) like the reference, not sage or grey-green. The ring on the screen is clearly "
        "three-quarters complete, with a visible gap in its last quarter."),
}


def generate(stem: str, attempt: int, extra: str = "") -> dict:
    spec = SPECS[stem]
    extra = " ".join(x for x in (EXTRAS.get((stem, attempt), ""), extra) if x)
    parts = [inline(REFS / (s[1:] + "-1024.jpg") if s.startswith("@") else ref(s)) for s in spec["refs"]]
    parts.append({"text": spec["prompt"] + (" " + extra if extra else "")})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": spec["aspect"], "imageSize": "2K"}}}
    log = {"stem": stem, "attempt": attempt, "model": MODEL, "refs": spec["refs"], "extra": extra,
           "prompt": parts[-1]["text"], "when": time.strftime("%Y-%m-%d %H:%M:%S")}
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
    else:
        return log
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    out_parts = cand.get("content", {}).get("parts", [])
    imgs = [p for p in out_parts if "inlineData" in p and not p.get("thought")]
    log.update(secs=round(time.time() - t, 1), finish=cand.get("finishReason"), modelVersion=d.get("modelVersion"),
               text=" ".join(p.get("text", "") for p in out_parts if "text" in p and not p.get("thought"))[:400])
    log.pop("error", None)
    if not imgs:
        log["error"] = "no image in response"
        return log
    im = Image.open(io.BytesIO(base64.b64decode(imgs[-1]["inlineData"]["data"]))).convert("RGB")
    RAW.mkdir(parents=True, exist_ok=True)
    PREVIEW.mkdir(parents=True, exist_ok=True)
    raw = RAW / f"{stem}-t{attempt}.png"
    im.save(raw)
    pv = im.copy()
    pv.thumbnail((1600, 1600), Image.LANCZOS)
    pv.save(PREVIEW / f"{stem}-t{attempt}.jpg", "JPEG", quality=88)
    log.update(raw=str(raw.relative_to(STUDIO)), size=list(im.size))
    return log


def cmd_gen(stems: str, attempt: int, extra: str = ""):
    names = [s for s in stems.split(",") if s]
    for s in names:
        for r in SPECS[s]["refs"]:
            if not r.startswith("@"):
                ref(r)
    OUT.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(5) as ex:
        logs = list(ex.map(lambda s: generate(s, attempt, extra), names))
    with open(OUT / "gen-log.jsonl", "a") as f:
        for lg in logs:
            f.write(json.dumps(lg) + "\n")
    for lg in logs:
        print(json.dumps({k: v for k, v in lg.items() if k != "prompt"}))


def cmd_crop(name: str, box: str, scale: float = 1.0):
    im = Image.open(RAW / f"{name}.png").convert("RGB")
    x0, y0, x1, y1 = (int(v) for v in box.split(","))
    c = im.crop((x0, y0, x1, y1))
    if scale != 1.0:
        c = c.resize((round(c.width * scale), round(c.height * scale)), Image.LANCZOS)
    CROPS.mkdir(parents=True, exist_ok=True)
    out = CROPS / f"{name}-{x0}-{y0}-{x1}-{y1}.jpg"
    c.save(out, "JPEG", quality=90)
    print(out.relative_to(STUDIO), im.size)


def cmd_final(stem: str, attempt: str):
    spec = SPECS[stem]
    im = Image.open(RAW / f"{stem}-t{attempt}.png").convert("RGB")
    aw, ah = (int(v) for v in spec["aspect"].split(":"))      # centre-crop the model's near-ratio size to the exact ratio
    if im.width * ah > im.height * aw:
        cw = round(im.height * aw / ah)
        im = im.crop(((im.width - cw) // 2, 0, (im.width - cw) // 2 + cw, im.height))
    else:
        ch = round(im.width * ah / aw)
        im = im.crop((0, (im.height - ch) // 2, im.width, (im.height - ch) // 2 + ch))
    w = spec["width"]
    h = round(w * ah / aw)
    im = im.resize((w, h), Image.LANCZOS)
    FINAL.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    for q in range(92, 49, -2):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2)
        if buf.tell() <= spec["max_bytes"]:
            break
    out = FINAL / f"{stem}.jpg"
    out.write_bytes(buf.getvalue())
    rec = {"stem": stem, "from": f"raw/{stem}-t{attempt}.png", "out": str(out.relative_to(STUDIO)), "px": [w, h],
           "quality": q, "bytes": buf.tell(), "max_bytes": spec["max_bytes"], "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(OUT / "final-log.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c == "gen":
        cmd_gen(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif c == "crop":
        cmd_crop(sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 1.0)
    elif c == "final":
        for s in sys.argv[2].split(","):
            st, _, att = s.partition(":")
            cmd_final(st, att)
    else:
        sys.exit(__doc__)
