"""What an edit actually changed (MAJ-34), computed on the server from the two specs, never taken from the model.

changed_paths(base, new)            -> ['decor[1].size', 'body.edge_round']
describe(base, new, requested)      -> [{path, text, requested}]  e.g. 'ears 25->32 mm'
named_paths(instruction, spec)      -> spec paths the user's words point at ('ears' -> decor[1])
unsupported(instruction, spec, ...) -> properties the user asked for that have no parameter ('rounder' ears)
"""
from __future__ import annotations

import re
from typing import Any

ARROW = "→"

DECOR_NAMES = {"face_plate": "face plate", "ear": "ears", "eye": "eye", "mouth": "mouth", "blush": "blush",
               "text": "text"}
BODY_NAMES = {"edge_round": "body edge rounding", "depth": "body depth", "outline": "body outline",
              "profile": "body profile", "primitives": "body shape", "wall": "wall thickness", "feet": "feet",
              "split": "shell split", "squash_y": "body depth squash", "family": "body type"}
COLOR_NAMES = {"body": "body colour", "accent": "accent colour", "detail": "detail colour",
               "body_name": "body colour name", "accent_name": "accent colour name", "detail_name": "detail colour name"}

# words in an instruction -> decor types / feature module fragments / top-level areas
WORDS_DECOR = {"ear": "ear", "ears": "ear", "antenna": "ear", "antennas": "ear", "antennae": "ear", "tuft": "ear",
               "face": "face_plate", "visor": "face_plate", "plate": "face_plate", "mouth": "mouth", "smile": "mouth",
               "blush": "blush", "cheek": "blush", "cheeks": "blush", "eye": "eye", "eyes": "eye"}
WORDS_FEATURE = {"button": "BUTTON", "speaker": "SPK", "grille": "SPK", "light": "LED", "lamp": "LED", "led": "LED",
                 "mic": "MIC", "microphone": "MIC", "screen": ("OLED", "LCD"), "display": ("OLED", "LCD"),
                 "eyes": ("OLED", "LCD")}
WORDS_BODY = ("body", "shell", "taller", "shorter", "wider", "narrower", "deeper", "thinner", "edge", "edges",
              "corner", "corners", "size", "bigger overall", "smaller overall", "whole")
WORDS_COLOR = ("colour", "color", "pink", "blue", "teal", "green", "yellow", "purple", "lavender", "white", "cream",
               "orange", "red", "grey", "gray", "black", "mint", "peach")

# property words that need a shape parameter; per decor type, the spec fields that would carry it
SHAPE_WORDS = {"round": "roundness", "rounder": "roundness", "rounded": "roundness", "pointy": "pointiness",
               "pointier": "pointiness", "sharper": "pointiness", "softer": "roundness", "curvier": "roundness",
               "fluffier": "texture", "fluffy": "texture", "floppy": "droop", "droopier": "droop"}
SHAPE_FIELDS = {"roundness": ("roundness", "round", "corner_radius", "shape", "stem", "neck"),
                "pointiness": ("pointiness", "tip", "shape"), "texture": ("texture",), "droop": ("droop", "tilt")}


def changed_paths(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k == "derived":
                continue
            p = f"{path}.{k}" if path else k
            if k not in a or k not in b:
                if a.get(k) != b.get(k):
                    out.append(p)
            else:
                out += changed_paths(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(isinstance(x, dict) for x in a + b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += changed_paths(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [path]


def _get(spec: Any, path: str) -> Any:
    cur = spec
    for tok in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if tok.startswith("["):
            i = int(tok[1:-1])
            cur = cur[i] if isinstance(cur, list) and i < len(cur) else None
        else:
            cur = cur.get(tok) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def _fmt(v: Any) -> str:
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if isinstance(v, (list, tuple)) and v and all(isinstance(x, (int, float)) for x in v):
        if len(v) == 2 and v[0] == v[1]:
            return f"{v[0]:g}"
        return "x".join(f"{x:g}" for x in v)
    if isinstance(v, list):
        return f"{len(v)} points" if v and isinstance(v[0], (list, tuple)) else f"{len(v)} items"
    if v is None:
        return "none"
    return str(v)


def _name(spec: dict, path: str) -> str:
    m = re.match(r"^(decor|features)\[(\d+)\](?:\.(.+))?$", path)
    if m:
        coll, i, rest = m.group(1), int(m.group(2)), m.group(3) or ""
        item = _get(spec, f"{coll}[{i}]") or {}
        if coll == "decor":
            base = DECOR_NAMES.get(item.get("type"), item.get("type") or f"decor {i}")
        else:
            base = item.get("label") or item.get("id") or f"feature {i}"
        field = rest.split(".")[0]
        if field in ("size", "") or (coll == "decor" and field == "size"):
            return base
        word = {"at": "position", "depth": "depth", "style": "style", "shape": "shape", "tilt": "tilt",
                "relief": "relief", "thickness": "thickness", "side": "sides", "rotate": "rotation",
                "grille": "grille", "face": "face", "module": "part", "label": "label"}.get(field, field)
        return f"{base} {word}"
    if path.startswith("body."):
        return BODY_NAMES.get(path.split(".")[1], path)
    if path.startswith("colors."):
        return COLOR_NAMES.get(path.split(".")[1], path)
    return path.replace("_", " ")


def _unit(path: str, v: Any) -> str:
    if re.search(r"(size|depth|edge_round|wall|thickness|\.at|\.w|\.h|\.hole|\.pitch)$", path) or \
            re.search(r"\.(at|size)$", path):
        return " mm"
    return ""


def _group(path: str) -> str:
    """Collapse list-valued leaves to their field (decor[1].size[0] -> decor[1].size)."""
    return re.sub(r"\[\d+\]$", "", re.sub(r"(\.(size|at|outline|profile|primitives|center))\[\d+\].*$", r"\1", path))


def describe(base: dict, new: dict, requested: set[str] | None = None) -> list[dict]:
    """[{path, text, requested}], one per changed field, e.g. 'ears 25→32 mm'."""
    paths: list[str] = []
    for p in changed_paths(base, new):
        g = _group(p)
        if g not in paths:
            paths.append(g)
    out = []
    for p in paths:
        a, b = _get(base, p), _get(new, p)
        if a is None and b is not None:
            text = f"added {_name(new, p)}"
        elif b is None:
            text = f"removed {_name(base, p)}"
        else:
            text = f"{_name(new, p)} {_fmt(a)}{ARROW}{_fmt(b)}{_unit(p, b)}"
        out.append({"path": p, "text": text, "requested": in_scope(p, requested) if requested is not None else True})
    return out


def changes_text(items: list[dict], limit: int = 6) -> str:
    asked = [i["text"] for i in items if i["requested"]]
    also = [i["text"] for i in items if not i["requested"]]
    bits = []
    if asked:
        bits.append(", ".join(asked[:limit]) + (f" and {len(asked) - limit} more" if len(asked) > limit else ""))
    if also:
        bits.append("also changed: " + ", ".join(also[:limit]) + (f" and {len(also) - limit} more" if len(also) > limit else ""))
    return "; ".join(bits) or "no change"


def in_scope(path: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    return any(path == a or path.startswith(a + ".") or path.startswith(a + "[") for a in allowed)


def named_paths(instruction: str, spec: dict) -> set[str]:
    """Spec paths that the user's words name: 'make the ears rounder and the face bigger' -> decor[i] of each ear
    and face plate. Colour words -> colors; body words -> body."""
    words = set(re.findall(r"[a-z]+", (instruction or "").lower()))
    low = (instruction or "").lower()
    out: set[str] = set()
    decor = spec.get("decor") or []
    want_types = {t for w, t in WORDS_DECOR.items() if w in words}
    for i, d in enumerate(decor):
        if isinstance(d, dict) and d.get("type") in want_types:
            out.add(f"decor[{i}]")
    if "eye" in want_types or "eyes" in words:
        for i, d in enumerate(decor):
            if isinstance(d, dict) and d.get("type") == "eye":
                out.add(f"decor[{i}]")
    if "face" in words:
        for i, d in enumerate(decor):
            if isinstance(d, dict) and d.get("type") in ("eye", "mouth", "blush"):
                out.add(f"decor[{i}]")
    for i, f in enumerate(spec.get("features") or []):
        mod = str((f or {}).get("module") or "")
        lab = f"{(f or {}).get('label') or ''} {(f or {}).get('id') or ''}".lower()
        for w, frag in WORDS_FEATURE.items():
            frags = frag if isinstance(frag, tuple) else (frag,)
            if w in words and any(fr in mod for fr in frags):
                out.add(f"features[{i}]")
        if any(w in lab.split() for w in words if len(w) > 3 and w not in ("make", "with", "bigger", "smaller")):
            out.add(f"features[{i}]")
    if any(w in low for w in WORDS_BODY):
        out.add("body")
    if any(w in words for w in WORDS_COLOR):
        out.add("colors")
    return out


def unsupported(instruction: str, spec: dict, targets: set[str], decor_fields: dict[str, set[str]]) -> list[dict]:
    """Shape properties the user asked for on a targeted decor item when its type has no such parameter.
    decor_fields: decor type -> field names of its schema model. [{word, property, part, path}]"""
    words = re.findall(r"[a-z]+", (instruction or "").lower())
    asked = [(w, SHAPE_WORDS[w]) for w in words if w in SHAPE_WORDS]
    if not asked:
        return []
    out, seen = [], set()
    for t in sorted(targets):
        m = re.match(r"^decor\[(\d+)\]$", t)
        if not m:
            continue
        d = (spec.get("decor") or [])[int(m.group(1))] if int(m.group(1)) < len(spec.get("decor") or []) else None
        if not isinstance(d, dict):
            continue
        fields = decor_fields.get(str(d.get("type")), set())
        for w, prop in asked:
            if not any(f in fields for f in SHAPE_FIELDS.get(prop, ())):
                key = (d.get("type"), prop)
                if key not in seen:
                    seen.add(key)
                    out.append({"word": w, "property": prop, "part": DECOR_NAMES.get(d.get("type"), d.get("type")),
                                "path": t})
    return out


def unsupported_note(items: list[dict]) -> str:
    """'The ears have no roundness setting yet, so their roundness did not change.'"""
    parts = []
    for u in items:
        parts.append(f"The {u['part']} have no {u['property']} setting yet, so their {u['property']} did not change."
                     if u["part"].endswith("s") else
                     f"The {u['part']} has no {u['property']} setting yet, so its {u['property']} did not change.")
    return " ".join(parts)


def claims(message: str, items: list[dict]) -> bool:
    """True when the reply claims a property listed as unsupported ('made the ears rounder')."""
    low = (message or "").lower()
    return any(re.search(rf"\b{re.escape(u['word'])}\w*", low) for u in items)


DIRECTION = {"round": +1, "rounder": +1, "rounded": +1, "softer": +1, "curvier": +1, "pointy": -1, "pointier": -1,
             "sharper": -1}


def shape_hints(instruction: str, spec: dict, targets: set[str], decor_fields: dict[str, set[str]]) -> list[str]:
    """MAJ-34 / MAJ-30: shape words mapped to the parameter that carries them, when the schema has one
    ('rounder' ears -> decor[1].roundness, 0..1, higher is rounder)."""
    words = re.findall(r"[a-z]+", (instruction or "").lower())
    asked = [w for w in words if w in DIRECTION]
    out: list[str] = []
    for t in sorted(targets):
        m = re.match(r"^decor\[(\d+)\]$", t)
        if not m or int(m.group(1)) >= len(spec.get("decor") or []):
            continue
        d = spec["decor"][int(m.group(1))]
        if not isinstance(d, dict) or "roundness" not in decor_fields.get(str(d.get("type")), set()):
            continue
        for w in asked:
            cur = d.get("roundness")
            way = "raise" if DIRECTION[w] > 0 else "lower"
            out.append(f"'{w}' for the {DECOR_NAMES.get(d.get('type'), d.get('type'))} means {t}.roundness (0 to 1, "
                       f"higher is rounder; now {cur if cur is not None else 'the style default'}): {way} it by "
                       "about 0.25 and keep the size unless the user asked for a size change.")
            break
    return out
