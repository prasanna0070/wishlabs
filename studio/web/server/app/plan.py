"""Tweak planning: which engine stages a direct edit needs (MIN-37) and what the base version's CAD said about the
layout (MAJ-8 nudges). Pure functions over specs and the base version's files.

reuse_plan()   -> {mode: full|partial, only, skip, reason, expected_seconds, expected_basis}
base_nudges()  -> [{parts, labels, feature, spec_path, message, move_mm, direction, resolved}]
"""
from __future__ import annotations

import json
import math
from typing import Any

FIRMWARE_KEYS = ("name", "slug", "audience", "behaviour", "features", "electronics")
PCB_KEYS = ("name", "slug")
MARGIN_MM = 2.0
VOL_EPS = 0.01
CLEAR_MIN = 0.5


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def _same(a: dict, b: dict, keys: tuple[str, ...]) -> bool:
    return all(_canon((a or {}).get(k)) == _canon((b or {}).get(k)) for k in keys)


def colour_only(base: dict, new: dict) -> bool:
    keys = set(base or {}) | set(new or {})
    return all(k == "colors" or _canon((base or {}).get(k)) == _canon((new or {}).get(k)) for k in keys) and \
        _canon((base or {}).get("colors")) != _canon((new or {}).get("colors"))


def stage_seconds(status: dict | None) -> dict[str, float]:
    out = {}
    for s in (status or {}).get("stages") or []:
        if isinstance(s, dict) and isinstance(s.get("seconds"), (int, float)) and s.get("state") == "done":
            out[str(s.get("id"))] = float(s["seconds"])
    return out


HEAVY = ("cad", "pcb", "firmware", "order", "manual")


def wall_estimate(sec: dict[str, float], run: set[str]) -> float | None:
    """The pipeline DAG: normalize, electrical, then (cad -> order) | pcb | firmware in parallel, then manual,
    checks, finalize, docs, package in sequence. Stages not run count 0. None when a heavy stage that will run has
    no measured time (no guessing)."""
    if not sec or any(s in run and s not in sec for s in HEAVY):
        return None
    d = lambda s: sec.get(s, 0.0) if s in run else 0.0  # noqa: E731
    return round(d("normalize") + d("electrical") + max(d("cad") + d("order"), d("pcb"), d("firmware"))
                 + d("manual") + d("checks") + d("finalize") + d("docs") + d("package"), 1)


def reuse_plan(stage_ids: tuple[str, ...], base_spec: dict, new_spec: dict, base_el: Any, new_el: Any,
               base_status: dict | list | None = None, base_n: int | None = None) -> dict:
    """Skip pcb and firmware when the wiring (electrical.json) is unchanged and nothing those stages read changed."""
    el_same = isinstance(base_el, dict) and isinstance(new_el, dict) and _canon(base_el) == _canon(new_el)
    skip: list[str] = []
    if el_same and _same(base_spec, new_spec, PCB_KEYS):
        skip.append("pcb")
    if el_same and _same(base_spec, new_spec, FIRMWARE_KEYS):
        skip.append("firmware")
    run = [s for s in stage_ids if s not in skip]
    sec: dict[str, float] = {}
    for st in (base_status if isinstance(base_status, list) else [base_status]):   # newest first
        for k, v in stage_seconds(st).items():
            sec.setdefault(k, v)
    full = wall_estimate(sec, set(stage_ids))
    est = wall_estimate(sec, set(run))
    if not el_same:
        reason = ("the wiring changes, so every stage runs" if isinstance(base_el, dict) and isinstance(new_el, dict)
                  else "the wiring could not be compared here, so every stage runs")
    elif skip:
        reason = f"the wiring is unchanged, so {' and '.join(skip)} are reused from v{base_n}"
    else:
        reason = "the firmware and board read parts of the design that changed, so every stage runs"
    return {"mode": "partial" if skip else "full", "only": run if skip else None, "skip": skip, "reason": reason,
            "seed_from": base_n if skip else None, "colour_only": colour_only(base_spec, new_spec),
            "expected_seconds": est, "full_seconds": full,
            "expected_basis": (f"estimated from the stage times of v{base_n} and the versions it came from"
                               if est is not None else None)}


# ---------------------------------------------------------------------------------------------- MAJ-8 nudges
def _ov(a0, a1, b0, b1) -> float:
    return min(a1, b1) - max(a0, b0)


def _feature_index(spec: dict, fid: str | None) -> int | None:
    for i, f in enumerate((spec or {}).get("features") or []):
        if isinstance(f, dict) and f.get("id") == fid:
            return i
    return None


def _move_for(a: list, b: list, face: str) -> tuple[str, float] | None:
    """Smallest move along the face plane (front/rear: x or z) that separates box a from box b by MARGIN_MM."""
    if face not in ("front", "rear", None):
        return None
    ox, oz = _ov(a[0], a[1], b[0], b[1]), _ov(a[4], a[5], b[4], b[5])
    if ox <= 0 or oz <= 0:
        return None
    ca_x, cb_x = (a[0] + a[1]) / 2, (b[0] + b[1]) / 2
    ca_z, cb_z = (a[4] + a[5]) / 2, (b[4] + b[5]) / 2
    cand = [("up" if ca_z >= cb_z else "down", oz + MARGIN_MM),
            ("right" if ca_x >= cb_x else "left", ox + MARGIN_MM)]
    d, k = min(cand, key=lambda c: c[1])
    return d, float(math.ceil(k))


def _applied(base_spec: dict, new_spec: dict, idx: int, direction: str) -> float:
    try:
        a = base_spec["features"][idx]["at"]
        b = new_spec["features"][idx]["at"]
    except (KeyError, IndexError, TypeError):
        return 0.0
    dx, dz = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
    return {"up": dz, "down": -dz, "right": dx, "left": -dx}[direction]


def from_engine(rows: list, base_spec: dict, new_spec: dict | None, base_n: int | None) -> list[dict]:
    """foundation's nudges_from_cad rows ({a, b, a_part, b_part, move: {feature, by_mm}, to_at, nudge}) in this
    module's nudge shape, with `resolved` when the tweak already moves the feature that far."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            out.append({"message": str(r), "source": "normalize.nudges_from_cad"})
            continue
        mv = r.get("move") or {}
        fid = mv.get("feature")
        idx = _feature_index(base_spec, fid)
        k = mv.get("by_mm")
        face = ((base_spec.get("features") or [{}])[idx] or {}).get("face") if idx is not None else None
        d = "up" if face in ("front", "rear", "left", "right", None) else "back"
        done = bool(new_spec is not None and idx is not None and k and d == "up"
                    and _applied(base_spec, new_spec, idx, "up") >= float(k))
        msg = str(r.get("nudge") or "").rstrip(".") + "."
        if done:
            msg += " Your change already moves it that far; the full collision check runs when you apply."
        out.append({"parts": [r.get("a_part"), r.get("b_part")], "labels": [r.get("a"), r.get("b")], "feature": fid,
                    "spec_path": f"features[{idx}]" if idx is not None else None, "move_mm": k, "direction": d,
                    "to_at": r.get("to_at"), "resolved": done, "message": msg,
                    "source": f"v{base_n} normalize.nudges_from_cad" if base_n else "normalize.nudges_from_cad"})
    return out


def base_nudges(cad_checks: Any, components: Any, base_spec: dict, new_spec: dict | None = None,
                base_n: int | None = None) -> list[dict]:
    """Failed collision / clearance pairs of the base version's CAD, each with a nudge computed from the
    components.json boxes ('Night light touches the speaker clamp ring: move it up 7 mm or more')."""
    items = (cad_checks or {}).get("checks") if isinstance(cad_checks, dict) else cad_checks
    comps = components.get("components") if isinstance(components, dict) and isinstance(
        components.get("components"), dict) else components
    if not isinstance(items, list) or not isinstance(comps, dict):
        return []
    pairs: list[tuple[str, str, str, float | None]] = []
    for it in items:
        if not isinstance(it, dict) or it.get("status") != "failed":
            continue
        data = it.get("data") or {}
        if it.get("id") == "collisions":
            for x in data.get("intersections") or []:
                ps = x.get("parts") or []
                if len(ps) == 2 and float(x.get("intersection_mm3") or 0) > VOL_EPS:
                    pairs.append((ps[0], ps[1], "touches", float(x.get("intersection_mm3") or 0)))
        elif it.get("id") == "feature_module_clearance":
            for x in data.get("pairs") or []:
                ps = x.get("parts") or []
                if len(ps) == 2 and float(x.get("gap_mm") if x.get("gap_mm") is not None else 99) < CLEAR_MIN:
                    pairs.append((ps[0], ps[1], "sits too close to", None))
    out: list[dict] = []
    seen: set[tuple] = set()
    for a, b, verb, vol in pairs:
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        ca, cb = comps.get(a) or {}, comps.get(b) or {}
        fa, fb = ca.get("feature_id"), cb.get("feature_id")
        if fa and fb and fa == fb:
            continue
        la, lb = ca.get("label") or a, cb.get("label") or b

        def vol_of(c: dict) -> float:
            bb = c.get("bbox") if isinstance(c.get("bbox"), list) and len(c.get("bbox")) == 6 else None
            return (bb[1] - bb[0]) * (bb[3] - bb[2]) * (bb[5] - bb[4]) if bb else float("inf")

        if fa and fb:   # move the smaller part's feature (the night light, not the speaker)
            first = vol_of(ca) <= vol_of(cb)
        else:
            first = bool(fa)
        mover, other, cm, co = (a, b, ca, cb) if first else (b, a, cb, ca)
        fid = cm.get("feature_id")
        idx = _feature_index(base_spec, fid)
        row = {"parts": [a, b], "labels": [la, lb], "feature": fid, "spec_path": f"features[{idx}]" if idx is not None
               else None, "move_mm": None, "direction": None, "resolved": False,
               "source": f"v{base_n} cad/digital-checks.json" if base_n else "cad/digital-checks.json"}
        flabel = None
        if idx is not None:
            f = base_spec["features"][idx]
            flabel = f.get("label") or fid
        mv = None
        if idx is not None and co.get("feature_id") and isinstance(cm.get("bbox"), list) and isinstance(co.get("bbox"), list):
            mv = _move_for(cm["bbox"], co["bbox"], (base_spec["features"][idx] or {}).get("face"))
        what = f"{flabel or cm.get('label') or mover} {verb} the {co.get('label') or other}"
        if vol:
            what += f" ({vol:.1f} mm³ overlap)"
        if mv:
            d, k = mv
            row.update(move_mm=k, direction=d)
            done = new_spec is not None and _applied(base_spec, new_spec, idx, d) >= k
            row["resolved"] = bool(done)
            row["message"] = (f"{what}: move it {d} {k:g} mm or more."
                              + (" Your change already moves it that far; the full collision check runs when you "
                                 "apply." if done else ""))
        else:
            row["message"] = f"{what}. The full collision check runs when you apply."
        out.append(row)
    return out
