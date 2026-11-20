"""Concept-set rules (MAJ-28). Pure functions over the proposed concepts, run after each spec validates and before
anything is rendered. Failures go back to the model as a repair round.

(a) subject: the noun the user named (robot, owl, ...) appears in the title or blurb of every Expected concept and
    of at least one concept per tier. For a subject with a known archetype (robot) at least 4 of 6 concepts carry
    it: named so AND two or more archetype cues (antenna ears, visor plate, screen face, head-on-body stack, boxy
    primitives).
(b) face: every concept has at least 2 face decor items (eye, mouth, blush) or a display of 1.5 in or larger.
(c) layout: no two concepts share the same feature order and positions (signature: features sorted by face and
    height, each as role, face and left/centre/right).

With pack `freeform` on (custom csg shapes, check_set(..., freeform=True, wish=...)) rule (b) turns around: no face is
forced. Faces are welcome only when the wish is playful or for a child (or names a character subject such as a robot or
an owl); otherwise the expected and different concepts must be plain products without face decor, and only the
wildcard (a character) may have one. Everything else is the same as with the pack off.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

# noun -> (words that count as naming it, archetype cue set name or None)
SUBJECTS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "robot": (("robot", "robots", "bot", "bots", "robo", "droid", "android", "automaton"), "robot"),
    "owl": (("owl", "owls", "owlet"), "owl"),
    "bear": (("bear", "teddy", "cub"), "bear"),
    "cat": (("cat", "kitty", "kitten", "cats"), "cat"),
    "bunny": (("bunny", "rabbit", "bunnies"), "bunny"),
    "dog": (("dog", "puppy", "pup", "doggy"), None),
    "fox": (("fox",), None),
    "panda": (("panda",), "bear"),
    "penguin": (("penguin",), None),
    "frog": (("frog",), None),
    "dinosaur": (("dinosaur", "dino", "t-rex"), None),
    "dragon": (("dragon",), None),
    "unicorn": (("unicorn",), None),
    "elephant": (("elephant",), None),
    "alien": (("alien",), None),
    "monster": (("monster",), None),
    "moon": (("moon", "crescent"), None),
    "star": (("star",), None),
    "cloud": (("cloud",), None),
    "mushroom": (("mushroom", "toadstool"), None),
    "lantern": (("lantern",), None),
    "lamp": (("lamp", "nightlight", "night-light"), None),
    "clock": (("clock",), None),
    "house": (("house", "cottage", "hut"), None),
    "rocket": (("rocket", "spaceship"), None),
    "egg": (("egg",), None),
}
# Names another product gave its own designs for our benchmark prompts (docs/ref/benchmark-*.md). The product
# must not reuse them: concept titles and project names that match are refused (no third-party branding).
BORROWED_NAMES = ("moon hug", "cloud forecast lamp", "bedtime story robot")
ARCH_MIN_SHARE = 4 / 6
ARCH_MIN_CUES = 2
FACE_DECOR = ("eye", "mouth", "blush")
MIN_FACE_ITEMS = 2
MIN_DISPLAY_IN = 1.5
SIDE_MM = 8.0
FACE_ORDER = {"front": 0, "top": 1, "left": 2, "right": 3, "rear": 4}
# pack `freeform`: words in the wish that make a face welcome (a playful or child-directed wish), and the subjects that
# are characters by nature (a face is part of them). "pet" and "friend" are left out: a pet feeder is an appliance.
PLAYFUL_WORDS = frozenset((
    "child", "children", "kid", "kids", "kiddo", "baby", "babies", "toddler", "toddlers", "son", "daughter", "nephew",
    "niece", "grandson", "granddaughter", "grandchild", "grandkids", "boy", "girl", "nursery", "cute", "playful", "fun",
    "funny", "toy", "toys", "cuddly", "adorable", "kawaii", "silly", "whimsical", "cartoon", "character", "mascot",
    "creature", "buddy", "companion", "critter", "plush"))
CHARACTER_SUBJECTS = frozenset(("robot", "owl", "bear", "cat", "bunny", "dog", "fox", "panda", "penguin", "frog",
                                "dinosaur", "dragon", "unicorn", "elephant", "alien", "monster"))
CHARACTER_TIER = "wildcard"
STACK_MM = 30.0     # a csg union part lifted this much above another reads as a head on a body


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z\-]*", (text or "").lower())


def subject_from_text(text: str) -> dict | None:
    """The first catalogued subject noun in the user's words, with the verbatim phrase around it
    ('a cute robot'). None when the request names no known subject."""
    low = (text or "").lower()
    best = None
    for noun, (words, arch) in SUBJECTS.items():
        for w in words:
            m = re.search(rf"\b{re.escape(w)}\b", low)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), m.end(), noun, arch)
    if best is None:
        return None
    start, end, noun, arch = best
    head = text[:start]
    art = None
    for m in re.finditer(r"\b(a|an|the|my|our)\b", head, re.I):
        if len(head[m.start():].split()) <= 4:
            art = m.start()
            break
    phrase = text[art if art is not None else start:end].strip()
    return {"noun": noun, "words": list(SUBJECTS[noun][0]), "archetype": arch, "phrase": phrase}


def names_subject(c: dict, subject: dict) -> bool:
    toks = set(_tokens(f"{c.get('title') or ''} {c.get('blurb') or ''}"))
    return any(w in toks for w in subject["words"]) or any(
        t.endswith(w) and len(w) >= 3 for t in toks for w in subject["words"] if w in ("bot", "robot"))


def display_inches(module_id: str | None, names: dict[str, dict]) -> float | None:
    """Diagonal from the library name ('2.0 in IPS colour LCD ...')."""
    info = names.get(module_id or "") or {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:in\b|inch|\")", str(info.get("name") or ""))
    return float(m.group(1)) if m and str(info.get("role")) == "display" else None


def _role(module_id: str | None, names: dict[str, dict]) -> str:
    info = names.get(module_id or "") or {}
    return str(info.get("role") or (module_id or "?").split("_", 1)[0].lower())


def face_ok(spec: dict, names: dict[str, dict]) -> tuple[bool, str]:
    items = [d for d in spec.get("decor") or [] if isinstance(d, dict) and d.get("type") in FACE_DECOR]
    big = [f for f in spec.get("features") or [] if (display_inches(f.get("module"), names) or 0) >= MIN_DISPLAY_IN]
    if len(items) >= MIN_FACE_ITEMS or big:
        return True, ""
    kinds = ", ".join(sorted({d["type"] for d in items})) or "none"
    return False, (f"needs a face: at least {MIN_FACE_ITEMS} face decor items (mouth and blush, or eyes) or the "
                   f"{MIN_DISPLAY_IN:g} in or larger display as its face (it has: {kinds})")


def face_items(spec: dict) -> list[str]:
    """The face decor types on a spec (eye, mouth, blush), in order."""
    return [d["type"] for d in spec.get("decor") or [] if isinstance(d, dict) and d.get("type") in FACE_DECOR]


def faces_welcome(wish: str, subject: dict | None, concepts: Iterable[dict] = ()) -> bool:
    """Pack `freeform`: a face is welcome when the wish is playful or for a child (its words, or a concept marked
    child-directed) or names a character subject (robot, owl, bear, ...)."""
    if subject and subject.get("noun") in CHARACTER_SUBJECTS:
        return True
    if any(((c.get("spec") or {}).get("audience") or {}).get("child_directed") for c in concepts):
        return True
    return bool(set(_tokens(wish)) & PLAYFUL_WORDS)


def _stacked(prims: list[dict]) -> bool:
    """A head primitive sitting on a body primitive: one centre clearly above another, overlapping in x."""
    boxes = []
    for p in prims:
        c, s = p.get("center") or [0, 0, 0], p.get("size") or [0, 0, 0]
        if len(c) == 3 and len(s) == 3:
            boxes.append((c[0] - s[0] / 2, c[0] + s[0] / 2, c[2] - s[2] / 2, c[2] + s[2] / 2, s[0] * s[2]))
    big = sorted(boxes, key=lambda b: -b[4])[:3]
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            lo, hi = (a, b) if (a[2] + a[3]) < (b[2] + b[3]) else (b, a)
            xo = min(lo[1], hi[1]) - max(lo[0], hi[0])
            if xo > 0 and (hi[2] + hi[3]) / 2 > lo[3] - 0.25 * (lo[3] - lo[2]) and hi[2] > lo[2] + 0.3 * (lo[3] - lo[2]):
                return True
    return False


def _neck(outline: list) -> bool:
    """Silhouette with a waist between a head and a body: the half-width narrows then widens going up."""
    pts = [(abs(float(x)), float(z)) for x, z in outline if isinstance(x, (int, float)) and isinstance(z, (int, float))]
    if len(pts) < 6:
        return False
    zmax = max(z for _, z in pts) or 1
    right = sorted({(z, x) for x, z in pts})
    widths = [x for z, x in right if 0.15 * zmax < z < 0.95 * zmax]
    if len(widths) < 3:
        return False
    for k in range(1, len(widths) - 1):
        if widths[k] < 0.85 * max(widths[:k]) and widths[k] < 0.85 * max(widths[k + 1:]):
            return True
    return False


def _csg_ops(node: Any) -> list[str]:
    """Every op in a csg tree (pack freeform), depth first."""
    if isinstance(node, list):
        return [o for n in node for o in _csg_ops(n)]
    if not isinstance(node, dict):
        return []
    out = [str(node["op"])] if node.get("op") else []
    for k in ("node", "nodes", "minus"):
        out += _csg_ops(node.get(k))
    return out


def _csg_stacked(node: Any) -> bool:
    """A union with one part lifted at least STACK_MM above another (a head on a body), at any depth."""
    if isinstance(node, list):
        return any(_csg_stacked(n) for n in node)
    if not isinstance(node, dict):
        return False
    if node.get("op") == "union":
        zs = []
        for ch in node.get("nodes") or []:
            to = ch.get("to") if isinstance(ch, dict) and ch.get("op") == "move" else None
            zs.append(float(to[2]) if isinstance(to, list) and len(to) == 3 and isinstance(to[2], (int, float)) else 0.0)
        if len(zs) >= 2 and max(zs) - min(zs) >= STACK_MM:
            return True
    return any(_csg_stacked(node.get(k)) for k in ("node", "nodes", "minus"))


def archetype_cues(spec: dict, names: dict[str, dict], archetype: str | None) -> set[str]:
    cues: set[str] = set()
    decor = [d for d in spec.get("decor") or [] if isinstance(d, dict)]
    body = spec.get("body") or {}
    if archetype == "robot":
        if any(d.get("type") == "ear" and d.get("style") == "antenna" for d in decor):
            cues.add("antenna ears")
        if any(d.get("type") == "face_plate" and d.get("shape") == "rounded_rect" for d in decor):
            cues.add("visor plate")
        if any((display_inches(f.get("module"), names) or 0) >= MIN_DISPLAY_IN for f in spec.get("features") or []):
            cues.add("screen face")
        prims = body.get("primitives") or []
        if body.get("family") == "blob" and _stacked(prims):
            cues.add("head-on-body stack")
        if body.get("family") == "silhouette" and _neck(body.get("outline") or []):
            cues.add("head-on-body stack")
        if any(p.get("kind") in ("rounded_box", "cylinder") for p in prims):
            cues.add("boxy body")
        if body.get("family") == "csg":                  # pack freeform: the same cues read from the csg tree
            tree = body.get("csg")
            if {"box", "cylinder"} & set(_csg_ops(tree)):
                cues.add("boxy body")
            if _csg_stacked(tree):
                cues.add("head-on-body stack")
    elif archetype in ("owl", "bear", "cat", "bunny"):
        style = {"owl": "owl_tuft", "bear": "bear", "cat": "cat", "bunny": "bunny"}[archetype]
        if any(d.get("type") == "ear" and d.get("style") == style for d in decor):
            cues.add(f"{archetype} ears")
    return cues


def layout_signature(spec: dict, names: dict[str, dict]) -> tuple:
    feats = [f for f in spec.get("features") or [] if isinstance(f, dict)]

    def key(f):
        at = f.get("at") or [0, 0]
        return (FACE_ORDER.get(f.get("face") or "front", 9), -float(at[1] if len(at) > 1 else 0), float(at[0] if at else 0))

    out = []
    for f in sorted(feats, key=key):
        at = f.get("at") or [0, 0]
        x = float(at[0]) if at else 0.0
        side = "L" if x < -SIDE_MM else "R" if x > SIDE_MM else "C"
        out.append((_role(f.get("module"), names), f.get("face") or "front", side))
    return tuple(out)


def describe_layout(sig: tuple) -> str:
    return " > ".join(f"{r}({face[0]}{side})" for r, face, side in sig)


def borrowed(name: str | None) -> bool:
    low = " ".join(str(name or "").lower().split())
    return any(b in low for b in BORROWED_NAMES)


def check_set(concepts: list[dict], subject: dict | None, names: dict[str, dict], *, freeform: bool = False,
              wish: str = "") -> list[dict]:
    """concepts: [{title, tier, blurb, spec}]. Returns failures [{rule, titles, message}] (empty = the set passes).
    freeform (pack `freeform` on): no forced face; a face only where faces_welcome(wish, ...) or on the wildcard."""
    fails: list[dict] = []
    n = len(concepts)
    taken = [c["title"] for c in concepts if borrowed(c.get("title")) or borrowed((c.get("spec") or {}).get("name"))]
    if taken:
        fails.append({"rule": "borrowed_name", "titles": taken,
                      "message": "These names belong to another product: " + ", ".join(taken) + ". Give each an "
                                 "original name of your own (title and spec name) in `replaces`."})
    if subject:
        noun = subject["noun"]
        exp_missing = [c["title"] for c in concepts if c.get("tier") == "expected" and not names_subject(c, subject)]
        if exp_missing:
            fails.append({"rule": "subject_expected", "titles": exp_missing,
                          "message": f"Expected concepts must keep the {noun} the user asked for: put '{noun}' in the "
                                     "title or blurb and make the shape read as one (animals or objects only as "
                                     f"styling of the {noun})."})
        for tier in ("different", "wildcard"):
            tc = [c for c in concepts if c.get("tier") == tier]
            if tc and not any(names_subject(c, subject) for c in tc):
                fails.append({"rule": "subject_tier", "titles": [c["title"] for c in tc],
                              "message": f"At least one {tier} concept must still be a {noun} and say so in its title "
                                         "or blurb."})
        arch = subject.get("archetype")
        if arch == "robot" and n >= 3:
            need = math.ceil(ARCH_MIN_SHARE * n)
            carry, weak = [], []
            for c in concepts:
                cues = archetype_cues(c["spec"], names, arch)
                (carry if names_subject(c, subject) and len(cues) >= ARCH_MIN_CUES else weak).append((c, cues))
            if len(carry) < need:
                fails.append({"rule": "archetype", "titles": [c["title"] for c, _ in weak],
                              "message": (f"Only {len(carry)} of {n} concepts read as a robot; at least {need} must be "
                                          "named as robots AND carry two or more robot cues: antenna ears (decor ear "
                                          "style antenna), a visor plate (face_plate shape rounded_rect), a screen face "
                                          "(the 2.0 in LCD), a head-on-body stack (blob body: a head primitive on a "
                                          "body primitive) or a boxy body (rounded_box primitives). "
                                          + "; ".join(f"{c['title']} has {', '.join(sorted(cu)) or 'no cues'}"
                                                      for c, cu in weak[:6]))})
    if freeform:
        if not faces_welcome(wish, subject, concepts):
            faced = [(c["title"], face_items(c["spec"])) for c in concepts
                     if c.get("tier") != CHARACTER_TIER and face_items(c["spec"])]
            if faced:
                fails.append({"rule": "no_face", "titles": [t for t, _ in faced],
                              "message": "This wish is not playful or for a child, so the expected and different "
                                         "concepts are plain products without a face: remove the face decor ("
                                         + "; ".join(f"{t} has {', '.join(sorted(set(k)))}" for t, k in faced)
                                         + "). A display stays a screen. Only the wildcard character may have a face."})
        bad_face = []
    else:
        bad_face = [(c["title"], why) for c in concepts for ok, why in [face_ok(c["spec"], names)] if not ok]
    if bad_face:
        fails.append({"rule": "face", "titles": [t for t, _ in bad_face],
                      "message": "Each concept needs a character face. " + "; ".join(f"{t} {w}" for t, w in bad_face)})
    seen: dict[tuple, str] = {}
    dup: list[tuple[str, str, tuple]] = []
    for c in concepts:
        sig = layout_signature(c["spec"], names)
        if sig in seen:
            dup.append((c["title"], seen[sig], sig))
        else:
            seen[sig] = c["title"]
    if dup:
        fails.append({"rule": "layout", "titles": [t for t, _, _ in dup],
                      "message": "Concepts must not reuse one feature layout. " + "; ".join(
                          f"{t} has the same layout as {o} ({describe_layout(s)}): move its speaker, button, light or "
                          "microphone to a different place or face" for t, o, s in dup)})
    return fails


def failure_lines(fails: Iterable[dict]) -> list[str]:
    return [f"[{f['rule']}] {f['message']}" for f in fails]


def titles_to_fix(fails: Iterable[dict]) -> list[str]:
    out: list[str] = []
    for f in fails:
        for t in f.get("titles") or []:
            if t not in out:
                out.append(t)
    return out


__all__ = ["subject_from_text", "check_set", "archetype_cues", "layout_signature", "face_ok", "names_subject",
           "display_inches", "failure_lines", "titles_to_fix", "faces_welcome", "face_items"]
