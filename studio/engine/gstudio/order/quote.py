"""order/quote.json: the instant delivered price for one design, from the vendored blueprint price engine.

Inputs that are measured for this design: the purchased list (module counts), the sliced grams and hours of
every PRINT_ part, the assembly bounding box. Inputs that are planning ranges or labelled assumptions: part
prices (library `price_inr_planning`, else the blueprint §3.3 catalog row), labour, print rates, courier,
warranty, returns, GST. The quote is therefore never BINDING (blueprint §1.3).
"""
from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from . import price_engine as pe

DATA = Path(__file__).resolve().parent / "data"

STATUS_LABEL = {
    "ESTIMATE_REVIEW_REQUIRED": "Estimated total, manufacturing and safety review needed",
    "ESTIMATE": "Estimated total, manufacturing review needed",
    "ESTIMATE_INCOMPLETE": "Incomplete estimate: some part prices are missing",
    "UNSUPPORTED_AT_LAUNCH": "Not offered at launch",
}


def _j(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def part_range(row: dict, lib: Any = None) -> tuple[Optional[list[float]], str, Optional[str]]:
    """(per-unit [low, high] INR, basis text, sim catalog id) for one purchased row (set by bom._row)."""
    if row.get("price_inr_planning"):
        lo, hi = row["price_inr_planning"]
        return [float(lo), float(hi)], row.get("price_basis") or "library price_inr_planning", row.get("price_catalog_id")
    return None, "no planning range in the library or the blueprint catalog", None


def assembly_ops(spec: dict, purchased: list[dict]) -> dict:
    d = spec.get("derived") or {}
    insts = [i for i in d.get("instances") or [] if isinstance(i, dict)]
    mounted = [i for i in insts if (i.get("role") or "") not in ("core", "backplane") and i.get("source") != "core"]
    cables = sum(int(r.get("quantity") or 0) for r in purchased if r.get("kind") == "cable")
    inserts = sum(int(h.get("qty") or 0) for h in d.get("hardware") or []
                  if isinstance(h, dict) and "insert" in f"{h.get('id', '')} {h.get('name', '')}".lower())
    return {"core_prep": 1, "module_install": max(1, len(mounted)), "cable_route": max(1, math.ceil(cables / 3)),
            "insert_heatset": inserts, "shell_close": 1, "flash_provision": 1, "functional_test": 1, "qc_photo": 1, "pack": 1}


def box_cm(bbox_mm: Optional[list[float]], pad_mm: float) -> Optional[list[int]]:
    if not bbox_mm:
        return None
    return [int(math.ceil((float(s) + 2 * pad_mm) / 10.0)) for s in bbox_mm]


def lead_time(print_hours: float, ga: dict, pcb_needed: bool = True) -> dict:
    lt = ga["lead_time"]
    pdays = math.ceil(print_hours / lt["print_hours_per_day"]["value"]) if print_hours > 0 else 0
    proc = lt["parts_procurement_days"]
    pcb = lt["pcb_fabrication_days"]
    asm, buf, cour = lt["assembly_flash_test_days"], lt["remake_buffer_days"], lt["courier_days"]
    first_lo = max(proc["low"], pcb["low"] if pcb_needed else 0) + pdays + asm["low"] + buf["low"] + cour["low"]
    first_hi = max(proc["high"], pcb["high"] if pcb_needed else 0) + pdays + asm["high"] + buf["high"] + cour["high"]
    st_lo = pdays + asm["low"] + buf["low"] + cour["low"]
    st_hi = pdays + asm["high"] + buf["high"] + cour["high"]
    return {
        "low": int(first_lo), "high": int(first_hi), "applies_to": pe.SCENARIOS[0].name, "status": "ESTIMATE",
        "reason": (f"Parts ({proc['low']}-{proc['high']} days) and the backplane PCB fabrication ({pcb['low']}-{pcb['high']} days) in "
                   f"parallel, then {pdays} printing day(s) for {print_hours:.1f} h of sliced print time, "
                   f"{asm['low']}-{asm['high']} day(s) assembly, flashing and test, a {buf['low']}-{buf['high']} day remake buffer, "
                   f"and {cour['low']}-{cour['high']} courier days. Every duration is an assumption, not a supplier commitment; "
                   "the courier window needs the delivery pincode."),
        "steady_state": {"low": int(st_lo), "high": int(st_hi), "applies_to": pe.SCENARIOS[2].name,
                         "reason": "stocked parts and backplanes: printing, assembly and test, buffer and courier only"},
        "breakdown": {"print_days": pdays, "parts_procurement_days": proc, "pcb_fabrication_days": pcb,
                      "assembly_flash_test_days": asm, "remake_buffer_days": buf, "courier_days": cour},
        "basis": {k: v.get("basis") for k, v in lt.items() if isinstance(v, dict)},
    }


def _round(x: Any, nd: int = 1) -> Any:
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v, nd) for v in x]
    return x


def make_quote(purchased: list[dict], print_parts: dict, spec: dict, lib: Any = None, *,
               bbox_mm: Optional[list[float]] = None, draws: int = 20000, seed: int = 7) -> dict:
    """print_parts: {PRINT_name: slice stats with filament_g, print_h, model_volume_cm3}. Returns quote.json."""
    t0 = time.perf_counter()
    assumptions = copy.deepcopy(_j("sim-assumptions.json"))
    ga = _j("gstudio-assumptions.json")

    # ---- parts: per-unit ranges; one-off buys whole packs, volume pays its share of a pack
    items, mods_one, mods_vol, parts_rec, missing, above = {}, [], [], [], [], []
    for r in purchased:
        rng, basis, cid = part_range(r, lib)
        key = r.get("module_id") or r.get("hardware_id") or r["name"]
        rec = {"name": r["name"], "module_id": r.get("module_id"), "quantity": r.get("quantity"), "packSize": r.get("packSize", 1),
               "price_range_inr": rng, "basis": basis, "evidence_tier": r.get("evidence_tier")}
        parts_rec.append(rec)
        if rng is None:
            missing.append(r["name"])
            continue
        cat_key = f"{key}"
        items[cat_key] = {"row": r.get("catalog_row") or "", "name": r["name"], "low": rng[0], "high": rng[1]}
        qty = int(r.get("quantity") or 1)
        if r.get("packSize") is None:  # set-priced row (pack size unknown): one set per build
            q_one = q_vol = 1
        else:
            pack = max(1, int(r.get("packSize") or 1))
            q_one = math.ceil(qty / pack) if pack > 1 else qty
            q_vol = qty / pack if pack > 1 else qty
        rec["qty_one_off"], rec["qty_volume"] = q_one, round(q_vol, 4)
        mods_one.append({"catalog_id": cat_key, "qty": q_one})
        mods_vol.append({"catalog_id": cat_key, "qty": q_vol})
        if r.get("evidence_tier") == "seen" and r.get("price_inr_seen") is not None and rng[1] < float(r["price_inr_seen"]):
            above.append({"name": r["name"], "seen_inr": float(r["price_inr_seen"]), "planning_inr": rng,
                          "extra_per_unit_inr": round((float(r["price_inr_seen"]) - rng[1]) * q_one, 2)})

    # ---- print: only real gadget parts (TEST_ coupons are a maker's fit check, not part of a built unit)
    sl = {"parts": {k: {"filament_g": v["filament_g"], "print_h": v["print_h"]} for k, v in print_parts.items()}}
    grams = sum(v["filament_g"] for v in sl["parts"].values())
    hours = sum(v["print_h"] for v in sl["parts"].values())

    # ---- packaging from the real size
    pad = ga["packaging"]["padding_mm_per_side"]["value"]
    box = box_cm(bbox_mm, pad)
    if box:
        assumptions["logistics"]["box_cm"] = {"value": box, "basis": f"assembly bounding box {[round(float(b)) for b in bbox_mm]} mm + {pad} mm padding per side ({ga['packaging']['padding_mm_per_side']['basis']})"}
    bp, el = ga["packaging"]["box_and_padding_kg"], ga["packaging"]["electronics_kg"]
    assumptions["logistics"]["packed_weight_kg"] = {
        "low": round(bp["low"] + el["low"] + grams / 1000, 3), "mode": round(bp["mode"] + el["mode"] + grams / 1000, 3),
        "high": round(bp["high"] + el["high"] + grams / 1000, 3),
        "basis": f"sliced plastic {grams:.0f} g + electronics and box allowances (ASSUMPTION, see gstudio-assumptions.json)"}

    ops = assembly_ops(spec, purchased)
    flags = set((spec.get("safety") or {}).get("flags") or [])
    child = bool((spec.get("audience") or {}).get("child_directed")) or "CHILD_DIRECTED" in flags
    extra = []
    if child and "LOCAL_VOICE_CAPTURE" in flags:
        extra.append("local voice capture in a child's room: privacy review of capture, indicator and storage before any sale (blueprint §11)")
    if missing:
        extra.append(f"{len(missing)} purchased item(s) have no planning price and are NOT in this total: {', '.join(missing)}")
    if above:
        top = max(above, key=lambda a: a["extra_per_unit_inr"])
        extra.append(f"{len(above)} part(s) were seen at retail above their planning range (largest: {top['name']} at "
                     f"₹{top['seen_inr']:,.0f} vs ₹{top['planning_inr'][0]:,.0f}-{top['planning_inr'][1]:,.0f}); this quote uses the planning "
                     f"ranges, so at today's seen one-off prices the parts would cost about ₹{sum(a['extra_per_unit_inr'] for a in above):,.0f} more")
    if not (spec.get("electronics") or {}).get("power", {}).get("adapter_in_box", False):
        extra.append("no USB-C power adapter in the box: the buyer supplies a 5 V USB-C supply")
    variant = {"modules": mods_one, "assembly_ops": ops, "cloud": None, "child_directed": child, "extra_reasons": extra,
               "launch_status": "REVIEW_REQUIRED"}
    eng_spec = {"variants": {"one_off": variant, "volume": dict(variant, modules=mods_vol)}}
    eng = pe.Engine({"items": items}, eng_spec, assumptions, sl)

    scen = []
    for sc in pe.SCENARIOS:
        q = eng.quote("one_off" if sc.qty_key in ("1", "10") else "volume", sc, n=draws, seed=seed)
        d = asdict(q)
        d.pop("variant", None)
        d["name"] = d.pop("scenario")
        d["note"] = sc.note
        d["parts_basis"] = "whole packs" if sc.qty_key in ("1", "10") else "per-unit share of packs"
        scen.append(_round(d, 3))
    status = scen[0]["status"]
    if missing:
        status = "ESTIMATE_INCOMPLETE"
    reasons = scen[0]["reasons"]
    for s in scen:
        s["status"] = status
        for k in ("p5", "p50", "p95", "price_p50_pretax", "price_p95_floor_pretax"):
            s[k] = round(s[k])
        s["breakdown_mean"] = {k: round(v) if k not in ("filament_g", "print_h", "assembly_min", "billable_kg") else v
                               for k, v in s["breakdown_mean"].items()}
    vol_cm3 = sum(float(v.get("model_volume_cm3") or 0) for v in print_parts.values())
    bureau = assumptions["print"]["bureau_fdm_rate_per_cm3"]
    lt = lead_time(hours, ga)
    return {
        "schema": "gadget-quote/1",
        "currency": "INR",
        "status": status,
        "status_label": STATUS_LABEL.get(status, status),
        "binding": False,
        "reasons": reasons,
        "complete": not missing,
        "missing_prices": missing,
        "delivered_price_inr": {
            "pilot_one_off": scen[0]["list_price_incl_gst"],
            "steady_state": scen[2]["list_price_incl_gst"],
            "includes_gst": True,
            "status": status,
            "label": STATUS_LABEL.get(status, status),
            "includes": ["purchased parts (planning ranges)", "printed parts from the sliced grams and hours", "assembly, flashing and functional test",
                         "one-time setup share (one-off and batch only)", "courier within India (estimate, pincode unknown)",
                         "warranty and returns reserve", "payment gateway fee", "GST at an assumed 18%"],
            "excludes": ["import duties (all parts assumed bought in India)", "anything in missing_prices"]
                        + ([] if (spec.get("electronics") or {}).get("power", {}).get("adapter_in_box") else ["USB-C power adapter"]),
        },
        "lead_time_days": lt,
        "scenarios": scen,
        "inputs": {
            "parts": parts_rec,
            "seen_above_planning": above,
            "print": {"grams": round(grams, 2), "hours": round(hours, 3), "parts": print_parts and {k: {kk: v.get(kk) for kk in ("filament_g", "print_h", "supports", "support_g")} for k, v in print_parts.items()},
                      "source": "print/slice-stats.json (PrusaSlicer run on these STLs)"},
            "assembly_ops": ops,
            "assembly_ops_basis": ga["assembly_ops_rule"]["basis"],
            "logistics": {"box_cm": assumptions["logistics"]["box_cm"], "packed_weight_kg": assumptions["logistics"]["packed_weight_kg"]},
            "assumptions_files": ["gstudio/order/data/sim-assumptions.json", "gstudio/order/data/gstudio-assumptions.json"],
            "draws": draws, "seed": seed,
        },
        "cross_checks": {
            "bureau_fdm_inr": {"low": round(vol_cm3 * bureau["low"]), "high": round(vol_cm3 * bureau["high"]),
                               "model_volume_cm3": round(vol_cm3, 1), "basis": bureau["basis"]},
        },
        "engine": {"module": "gstudio.order.price_engine", "vendored_from": "sim/price_engine.py", "seconds": round(time.perf_counter() - t0, 2)},
        "limits": [
            "Every rupee is a planning range or a labelled assumption, not a supplier quote; no contract or rate card exists yet (blueprint §7.1).",
            "Courier cost and delivery window assume an Indian destination; the pincode is unknown.",
            "GST is applied at 18% as an assumption; the product's HSN classification needs a tax adviser (blueprint §8.7).",
            "Print cost uses the sliced grams and hours of a generic 0.4 mm FDM profile; a print bureau's own slicer will differ.",
        ],
    }
