"""Brief assembly. The model writes the prose sections; the server fills Size (from the design geometry) and Parts
(from the spec and the library), so neither can be invented.

Provenance rules enforced here:
* a fact with source "user" must quote the user verbatim (a stored answer or a phrase of a user message);
  otherwise its evidence is rewritten to quote the closest stored answer, or the source becomes "suggested" (MIN-41);
* the subject the user named ("a cute robot") is kept as a user-sourced fact (MAJ-28);
* the size says what it measures: the concept preview's printed shell, the CAD build, or the body only (MAJ-36);
* every edit build writes brief v(n+1) with the change as a user-sourced fact (MAJ-36).
"""
from __future__ import annotations

import copy
import json
import re
import struct
from typing import Any

from .builder.placeholder import envelope_mm
from .util import now_iso, spec_hash

TEXT_FIELDS = ("purpose", "interaction", "privacy_safety", "power")
GEOMETRY_KEYS = ("body", "decor", "features")
MAX_CHANGE_FACTS = 12
_WORD = re.compile(r"[a-z0-9]+")
STOP = {"a", "an", "the", "and", "or", "of", "to", "for", "with", "you", "your", "i", "my", "it", "is", "in", "on",
        "she", "her", "he", "his", "they", "be", "will", "that", "this", "chose", "said", "asked", "want", "yes", "no"}


def _clean_str(x: Any, limit: int = 2000) -> str:
    return str(x or "").strip()[:limit]


def parts_from_spec(spec: dict, names: dict[str, dict]) -> list[dict]:
    """From the engine's derived.instances when the spec is normalized; else from core/features/extras."""
    inst = ((spec.get("derived") or {}).get("instances")) or []
    if inst:
        rows_d: dict[str, dict] = {}
        for i in inst:
            mid = i.get("module")
            if not mid:
                continue
            info = names.get(mid) or {}
            r = rows_d.get(mid)
            if r:
                r["qty"] += int(i.get("qty") or 1)
                r["refs"].append(i.get("ref"))
                continue
            rows_d[mid] = {"module_id": mid, "name": i.get("name") or info.get("name") or mid,
                           "role": i.get("role") or info.get("role", ""), "qty": int(i.get("qty") or 1),
                           "implied": i.get("source") == "implied", "reason": i.get("reason", ""),
                           "refs": [i.get("ref")]}
        return list(rows_d.values())
    rows: dict[str, dict] = {}

    def add(mid: str | None, implied: bool = False, label: str | None = None):
        if not mid:
            return
        info = names.get(mid) or {}
        row = rows.get(mid)
        if row:
            row["qty"] += 1
            return
        rows[mid] = {"module_id": mid, "name": info.get("name") or label or mid, "role": info.get("role", ""),
                     "qty": 1, "implied": implied, "in_library": mid in names if names else None}

    el = spec.get("electronics") or {}
    add(el.get("core"))
    for f in spec.get("features") or []:
        add(f.get("module"), label=f.get("label"))
    for m in el.get("extra_modules") or []:
        add(m if isinstance(m, str) else (m or {}).get("module"))
    for key in ("implied", "implied_modules", "implied_parts"):
        for m in (el.get(key) or spec.get(key) or []):
            add(m if isinstance(m, str) else (m or {}).get("module") or (m or {}).get("id"), implied=True)
    return list(rows.values())


# ---------------------------------------------------------------------------------------------- size
def glb_bbox_mm(glb: bytes | None, prefix: str = "PRINT_") -> dict | None:
    """Overall size of the printed nodes in a Gadget Studio preview GLB (metres, gadget z up -> glTF +Y).
    Uses accessor min/max of each node's POSITION; nodes with a rotation are skipped (None when nothing usable)."""
    if not glb or glb[:4] != b"glTF" or len(glb) < 20:
        return None
    try:
        clen, ctype = struct.unpack("<II", glb[12:20])
        doc = json.loads(glb[20:20 + clen])
    except (struct.error, ValueError):
        return None
    acc, meshes = doc.get("accessors") or [], doc.get("meshes") or []
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    used = 0
    for node in doc.get("nodes") or []:
        if not str(node.get("name") or "").startswith(prefix) or node.get("mesh") is None:
            continue
        if node.get("rotation") not in (None, [0, 0, 0, 1]) or node.get("matrix"):
            continue
        t = node.get("translation") or [0, 0, 0]
        s = node.get("scale") or [1, 1, 1]
        for prim in (meshes[node["mesh"]].get("primitives") or []) if node["mesh"] < len(meshes) else []:
            a = acc[prim.get("attributes", {}).get("POSITION", -1)] if 0 <= prim.get("attributes", {}).get(
                "POSITION", -1) < len(acc) else None
            if not a or not a.get("min") or not a.get("max"):
                continue
            for k in range(3):
                v0, v1 = a["min"][k] * s[k] + t[k], a["max"][k] * s[k] + t[k]
                lo[k], hi[k] = min(lo[k], v0, v1), max(hi[k], v0, v1)
            used += 1
    if not used:
        return None
    w, h, d = ((hi[k] - lo[k]) * 1000 for k in range(3))   # glTF x, y(up), z
    return {"w_mm": round(w), "d_mm": round(d), "h_mm": round(h)}


def same_geometry(a: dict | None, b: dict | None) -> bool:
    return bool(a and b) and all(json.dumps((a or {}).get(k), sort_keys=True) == json.dumps((b or {}).get(k), sort_keys=True)
                                 for k in GEOMETRY_KEYS)


def _has_ears(spec: dict) -> bool:
    return any(isinstance(d, dict) and d.get("type") == "ear" for d in spec.get("decor") or [])


def size_from_spec(spec: dict, measured: dict | None = None, measured_from: str | None = None) -> dict:
    """Size fact computed from the design (never written by the model). `measured` (w/d/h mm of the printed parts,
    e.g. from the concept preview GLB or the CAD build) wins; otherwise the body envelope, labelled body-only when
    decor such as ears stands proud of it."""
    if measured and all(isinstance(measured.get(k), (int, float)) and measured[k] > 0 for k in ("w_mm", "d_mm", "h_mm")):
        w, d, h = (round(float(measured[k])) for k in ("w_mm", "d_mm", "h_mm"))
        src = measured_from or "the design geometry"
        if "preview" in src:   # the preview shell stands on z=0: the CAD build adds the feet underneath
            return {"w_mm": w, "d_mm": d, "h_mm": h, "scope": "shell", "value": f"About {w} W x {d} D x {h} H mm "
                    "(shell with ears, without feet)", "source": "suggested",
                    "evidence": f"Measured on {src}: the printed shell with its ears, before the feet are added; the "
                                "CAD build gives the final numbers."}
        return {"w_mm": w, "d_mm": d, "h_mm": h, "scope": "overall", "value": f"About {w} W x {d} D x {h} H mm",
                "source": "suggested",
                "evidence": f"Measured on {src} (all printed parts, ears included, standing on its feet)."}
    ears = _has_ears(spec)
    sz = (spec.get("derived") or {}).get("size_mm")
    if sz and len(sz) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in sz):
        w, d, h = (round(float(v)) for v in sz)
        if ears:
            return {"w_mm": w, "d_mm": d, "h_mm": h, "scope": "body",
                    "value": f"About {w} W x {d} D x {h} H mm, body only (the ears add height)",
                    "source": "suggested",
                    "evidence": "Body envelope from the design outline, without the ears; the CAD build measures "
                                "the whole shell."}
        return {"w_mm": w, "d_mm": d, "h_mm": h, "scope": "overall", "value": f"About {w} W x {d} D x {h} H mm",
                "source": "suggested",
                "evidence": "Body envelope computed from the design geometry; the CAD build gives the final numbers."}
    env = envelope_mm(spec)
    w, d, h = env.get("w_mm"), env.get("d_mm"), env.get("h_mm")
    val = f"About {w} W x {d} D x {h} H mm" if d else f"About {w} W x {h} H mm"
    return {**env, "scope": "estimate", "value": val + " (estimate)", "source": "suggested",
            "evidence": "Estimate from the design outline (ears approximated); the CAD build gives exact numbers."}


# ---------------------------------------------------------------------------------------------- user quotes
def user_texts(p: dict) -> list[str]:
    """Everything the user typed or chose, verbatim: messages (minus the app's 'My choices:' wrapper) and the
    answers stored on question cards."""
    out: list[str] = []
    for m in p.get("chat") or []:
        if m.get("role") == "user" and m.get("kind") == "text":
            t = str(m.get("text") or "").strip()
            if t.startswith("My choices:"):
                continue
            if t and not (m.get("payload") or {}).get("action"):
                out.append(t)
        if m.get("kind") == "choices":
            pl = m.get("payload") or {}
            for a in pl.get("answers") or []:
                v = str((a or {}).get("value") or "").strip()
                if v:
                    out.append(v)
            if pl.get("free_text"):
                out.append(str(pl["free_text"]).strip())
    seen, res = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            res.append(t)
    return res


def _words(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if w not in STOP}


def _quotes_user(evidence: str, texts: list[str]) -> bool:
    ev = evidence.lower()
    for t in texts:
        tl = t.lower().strip()
        if len(tl) >= 2 and len(tl) <= 80 and tl in ev:
            return True
    for q in re.findall(r"[\"“']([^\"”']{3,200})[\"”']", evidence):
        ql = q.lower().strip()
        if len(_words(ql)) >= 1 and any(ql in t.lower() for t in texts):
            return True
    return False


def _clause(text: str, words: set[str], limit: int = 90) -> str:
    """The verbatim sentence or clause of `text` that shares the most words with `words`."""
    parts = [x.strip() for x in re.split(r"(?<=[.!?;])\s+|,\s+(?:and|but)\s+|\s+and\s+", text) if x.strip()]
    best = max(parts or [text], key=lambda c: len(_words(c) & words))
    best = best.rstrip(".!?;")
    if len(best) > limit:
        cut = best[:limit]
        best = cut[:cut.rfind(" ")] if " " in cut else cut
    return best


def enforce_user_quote(fact: dict, texts: list[str]) -> dict:
    """A 'user' fact keeps its evidence only when it quotes the user verbatim; else it quotes the closest stored
    answer, or becomes 'suggested' when nothing the user said relates to it."""
    if fact.get("source") != "user" or not texts:
        return fact
    ev = str(fact.get("evidence") or "")
    if _quotes_user(ev, texts):
        return fact
    want = _words(f"{fact.get('title') or ''} {fact.get('topic') or ''} {fact.get('value') or ''} {ev}")
    scored = sorted(((len(_words(t) & want), -len(t), t) for t in texts), reverse=True)
    if scored and scored[0][0] > 0:
        t = scored[0][2]
        q = t if len(t) <= 90 else _clause(t, want)
        return {**fact, "evidence": f'You said: "{q}"', "evidence_rewritten": True}
    return {**fact, "source": "suggested", "evidence": ev or "Proposed by the assistant", "evidence_rewritten": True}


def subject_fact(subject: dict | None) -> dict | None:
    """{topic: 'Subject', value: 'A robot', source: user, evidence: 'You wrote: "a cute robot"'}"""
    if not subject or not subject.get("noun"):
        return None
    return {"topic": "Subject", "value": f"A {subject['noun']}", "source": "user",
            "evidence": f'You wrote: "{subject.get("phrase") or subject["noun"]}"'}


# ---------------------------------------------------------------------------------------------- brief
def fact(x: Any, default_source: str = "suggested", limit: int = 1200) -> dict:
    """Normalise a model-written fact ({value, source, evidence} or a bare string)."""
    if isinstance(x, dict):
        src = x.get("source") or x.get("basis")
        return {"value": _clean_str(x.get("value") or x.get("text"), limit),
                "source": src if src in ("user", "suggested") else default_source,
                "evidence": _clean_str(x.get("evidence"), 300)}
    return {"value": _clean_str(x, limit), "source": default_source, "evidence": ""}


def assemble_brief(raw: dict, spec: dict, concept_id: str | None, version: int, names: dict[str, dict],
                   edited_fields: set[str] | None = None, *, texts: list[str] | None = None,
                   subject: dict | None = None, measured: dict | None = None,
                   measured_from: str | None = None) -> dict:
    """Brief with provenance per fact: {value, source: user|suggested, evidence}. Size and parts are computed."""
    raw = raw or {}
    edited = edited_fields or set()
    texts = texts or []

    def f(key: str, x: Any, label: str = "") -> dict:
        out = fact(x)
        if key in edited:
            out["source"], out["evidence"] = "user", "Edited by you in the brief."
        elif texts:
            out = enforce_user_quote({**out, "title": label}, texts)
            out.pop("title", None)
        return out

    funcs = []
    for i, fx in enumerate(raw.get("functions") or []):
        if isinstance(fx, dict):
            funcs.append({"title": _clean_str(fx.get("title"), 80), **f(f"functions[{i}]", fx, str(fx.get("title") or ""))})
        elif isinstance(fx, str):
            funcs.append({"title": "", **f(f"functions[{i}]", fx)})
    details = []
    for i, dx in enumerate(raw.get("details") or []):
        if isinstance(dx, dict) and str(dx.get("value") or "").strip():
            details.append({"topic": _clean_str(dx.get("topic"), 60),
                            **f(f"details[{i}]", dx, str(dx.get("topic") or ""))})
    sf = subject_fact(subject)
    if sf and not any(str(d.get("topic", "")).lower() == "subject" for d in details):
        details.insert(0, sf)
    limits = [f(f"limits[{i}]", x) for i, x in enumerate(raw.get("limits") or [])
              if (x.get("value") if isinstance(x, dict) else str(x or "")).strip()]
    flags = list(((spec.get("safety") or {}).get("flags")) or [])
    parts = parts_from_spec(spec, names)
    for p in parts:
        p.setdefault("source", "suggested")
        p.setdefault("evidence", "Added automatically for the design" if p.get("implied") else "From the parts catalog")
    b = {
        "version": version,
        "concept_id": concept_id,
        "title": _clean_str(raw.get("title") or spec.get("name"), 80),
        "size": size_from_spec(spec, measured, measured_from),
        "functions": funcs,
        "details": details,
        "parts": parts,
        "limits": limits,
        "safety_flags": flags,
        "spec": spec,
        "spec_hash": spec_hash(spec),
        "created": now_iso(),
        "approved": False,
        "approved_at": None,
    }
    for k in ("summary",) + TEXT_FIELDS:
        b[k] = f(k, raw.get(k))
    return b


def revise_brief(prev: dict, spec: dict, *, version: int, for_version: int, source: str, change: str,
                 names: dict[str, dict]) -> dict:
    """Brief v(n+1) for an edit build: same text, the new spec, a recomputed size and parts list, and the change
    recorded as a user-sourced fact ('Part edit v2: ears 25->32 mm'). Approved by the edit itself."""
    b = copy.deepcopy({k: v for k, v in prev.items() if k != "spec"})
    kind = {"update": "Edit", "tweak": "Direct edit", "story_audio": "Story audio"}.get(source, "Edit")
    b.update(version=version, spec=spec, spec_hash=spec_hash(spec), created=now_iso(), approved=True,
             approved_at=now_iso(), approved_via=source, revised_from=prev.get("version"),
             auto_for_version=for_version)
    b.pop("edited_by_user", None)
    b["size"] = size_from_spec(spec)
    mods_prev = sorted(f.get("module") or "" for f in (prev.get("spec") or {}).get("features") or [])
    mods_new = sorted(f.get("module") or "" for f in spec.get("features") or [])
    if mods_prev != mods_new or not b.get("parts"):
        b["parts"] = parts_from_spec(spec, names)
        for p in b["parts"]:
            p.setdefault("source", "suggested")
            p.setdefault("evidence", "Added automatically for the design" if p.get("implied")
                         else "From the parts catalog")
    details = [d for d in b.get("details") or [] if isinstance(d, dict)]
    changes = [d for d in details if str(d.get("topic", "")).startswith("Change in v")]
    others = [d for d in details if d not in changes]
    changes.append({"topic": f"Change in v{for_version}", "value": change, "source": "user",
                    "evidence": f"{kind} v{for_version}: {change}"})
    b["details"] = others + changes[-MAX_CHANGE_FACTS:]
    b["safety_flags"] = list(((spec.get("safety") or {}).get("flags")) or [])
    return b


def _v(x: Any) -> str:
    return x.get("value", "") if isinstance(x, dict) else str(x or "")


def brief_text(b: dict) -> str:
    """Plain-text rendering used in the model's history."""
    lines = [f"Brief v{b.get('version')} for {b.get('title')}", f"Summary: {_v(b.get('summary'))}",
             f"Purpose: {_v(b.get('purpose'))}", f"Size: {_v(b.get('size'))}"]
    for i, fx in enumerate(b.get("functions") or [], 1):
        lines.append(f"Function {i}: {fx.get('title')}: {_v(fx)} [{fx.get('source', '')}]")
    for k, lab in (("interaction", "Interaction"), ("privacy_safety", "Privacy and safety"), ("power", "Power")):
        lines.append(f"{lab}: {_v(b.get(k))}")
    for d in b.get("details") or []:
        lines.append(f"{d.get('topic')}: {_v(d)} [{d.get('source', '')}]")
    lines.append("Parts: " + ", ".join(f"{p['name']} x{p['qty']}" for p in b.get("parts") or []))
    lines.append("Limits: " + "; ".join(_v(x) for x in b.get("limits") or []))
    return "\n".join(lines)
