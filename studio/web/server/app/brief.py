"""Brief assembly. The model writes the prose sections; the server fills Size (from the spec geometry) and Parts
(from the spec and the library), so neither can be invented."""
from __future__ import annotations

from typing import Any

from .builder.placeholder import envelope_mm
from .util import now_iso

TEXT_FIELDS = ("purpose", "interaction", "privacy_safety", "power")


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


def size_from_spec(spec: dict) -> dict:
    """Size fact computed from the spec (never written by the model)."""
    sz = (spec.get("derived") or {}).get("size_mm")
    if sz and len(sz) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in sz):
        w, d, h = (round(float(v)) for v in sz)
        return {"w_mm": w, "d_mm": d, "h_mm": h, "value": f"About {w} W x {d} D x {h} H mm",
                "source": "suggested",
                "evidence": "Body envelope computed from the design geometry; the CAD build gives the final numbers."}
    env = envelope_mm(spec)
    w, d, h = env.get("w_mm"), env.get("d_mm"), env.get("h_mm")
    val = f"About {w} W x {d} D x {h} H mm" if d else f"About {w} W x {h} H mm"
    return {**env, "value": val, "source": "suggested",
            "evidence": "Approximate, from the design outline; the CAD build gives exact numbers."}


def fact(x: Any, default_source: str = "suggested", limit: int = 1200) -> dict:
    """Normalise a model-written fact ({value, source, evidence} or a bare string)."""
    if isinstance(x, dict):
        src = x.get("source") or x.get("basis")
        return {"value": _clean_str(x.get("value") or x.get("text"), limit),
                "source": src if src in ("user", "suggested") else default_source,
                "evidence": _clean_str(x.get("evidence"), 300)}
    return {"value": _clean_str(x, limit), "source": default_source, "evidence": ""}


def assemble_brief(raw: dict, spec: dict, concept_id: str | None, version: int, names: dict[str, dict],
                   edited_fields: set[str] | None = None) -> dict:
    """Brief with provenance per fact: {value, source: user|suggested, evidence}. Size and parts are computed."""
    raw = raw or {}
    edited = edited_fields or set()

    def f(key: str, x: Any) -> dict:
        out = fact(x)
        if key in edited:
            out["source"], out["evidence"] = "user", "Edited by you in the brief."
        return out

    funcs = []
    for i, fx in enumerate(raw.get("functions") or []):
        if isinstance(fx, dict):
            ff = f(f"functions[{i}]", fx)
            funcs.append({"title": _clean_str(fx.get("title"), 80), **ff})
        elif isinstance(fx, str):
            funcs.append({"title": "", **f(f"functions[{i}]", fx)})
    details = []
    for i, dx in enumerate(raw.get("details") or []):
        if isinstance(dx, dict) and str(dx.get("value") or "").strip():
            details.append({"topic": _clean_str(dx.get("topic"), 60), **f(f"details[{i}]", dx)})
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
        "size": size_from_spec(spec),
        "functions": funcs,
        "details": details,
        "parts": parts,
        "limits": limits,
        "safety_flags": flags,
        "spec": spec,
        "created": now_iso(),
        "approved": False,
        "approved_at": None,
    }
    for k in ("summary",) + TEXT_FIELDS:
        b[k] = f(k, raw.get(k))
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
