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


SEEN_WIDTH = 0.06   # a seen price is priced as a narrow range (6% wide, mode at the seen price): snapshot drift allowance
INBOUND = "inbound-shipping.json"


def part_range(row: dict, lib: Any = None, frac: Optional[float] = None) -> tuple[Optional[list[float]], str, Optional[str]]:
    """(per-unit [low, high] INR, basis text, sim catalog id) for one purchased row (set by bom._row).

    MAJ-23: with `frac` (the engine's mode position for a one-off or batch scenario) a row with a verified seen price is
    priced as a narrow range whose mode is its GST-inclusive seen price; without `frac`, and for estimates, the planning
    range is used (steady state)."""
    seen = row.get("price_inr_incl_gst") if row.get("evidence_tier") == "seen" else None
    if frac is not None and seen is not None:
        w = SEEN_WIDTH * float(seen)
        lo = float(seen) - frac * w
        return [round(lo, 4), round(lo + w, 4)], (f"seen {row.get('vendor')} {row.get('seen_on', '')[:10]}: "
                                                 f"₹{float(seen):,.2f} incl. GST ({row.get('gst_basis')} as listed), ±{SEEN_WIDTH:.0%} drift"), None
    if frac is None and row.get("price_inr_planning_volume"):  # steady state: a volume price where the row has one (BP1 PCBA)
        lo, hi = row["price_inr_planning_volume"]
        return [float(lo), float(hi)], (row.get("price_basis") or "") + " (steady state: volume batch range)", row.get("price_catalog_id")
    if row.get("price_inr_planning"):
        lo, hi = row["price_inr_planning"]
        return [float(lo), float(hi)], row.get("price_basis") or "library price_inr_planning", row.get("price_catalog_id")
    return None, "no planning range in the library or the blueprint catalog", None


def unit_value(rng: list[float], frac: float) -> float:
    """The deterministic per-unit value the engine centres on (the triangular mode)."""
    return float(rng[0]) + frac * (float(rng[1]) - float(rng[0]))


def pack_qty(r: dict) -> tuple[float, float]:
    """(one-off qty in whole packs, volume qty as a share of packs) for one row."""
    qty = int(r.get("quantity") or 1)
    if r.get("packSize") is None:  # set-priced row (pack size unknown): one set per build
        return 1, 1
    pack = max(1, int(r.get("packSize") or 1))
    return (math.ceil(qty / pack) if pack > 1 else qty), (qty / pack if pack > 1 else qty)


def inbound_shipping(purchased: list[dict], fracs: dict, ga_inbound: dict) -> dict:
    """MIN-26: one inbound line per vendor for the one-off and the batch (ASSUMPTION fees until each vendor is checked)."""
    d = ga_inbound["default"]
    by: dict[str, list[dict]] = {}
    unsourced = []
    for r in purchased:
        if r.get("vendor"):
            by.setdefault(r["vendor"], []).append(r)
        else:
            unsourced.append(r["name"])
    vendors = []
    for v, rows in sorted(by.items()):
        cfg = dict(d, **(ga_inbound.get("vendors") or {}).get(v, {}))
        sub = 0.0
        for r in rows:
            rng, _b, _c = part_range(r, None, fracs["1"])
            if rng:
                sub += unit_value(rng, fracs["1"]) * pack_qty(r)[0]
        fee = cfg["flat_fee_inr"]
        one = {k: (0.0 if sub >= cfg["free_above_inr"] else float(fee[k])) for k in ("low", "high")}
        bat = {k: (0.0 if sub * 10 >= cfg["free_above_inr"] else float(fee[k])) / 10.0 for k in ("low", "high")}
        one["value"] = round(unit_value([one["low"], one["high"]], fracs["1"]), 2)   # where the price engine centres it
        bat["value"] = round(unit_value([bat["low"], bat["high"]], fracs["10"]), 2)
        vendors.append({"vendor": v, "rows": [r["name"] for r in rows], "subtotal_one_off_inr": round(sub, 2),
                        "free_above_inr": cfg["free_above_inr"], "fee_one_off_inr": one, "fee_batch_unit_inr": {k: round(x, 2) for k, x in bat.items()},
                        "basis": cfg["basis"]})
    moves = [{"item": r["name"], "from": r.get("consolidated_from"), "to": r.get("vendor")} for r in purchased if r.get("consolidated_from")]
    cannot = [{"item": r["name"], "vendor": r.get("vendor"), "why": r["consolidation_note"]} for r in purchased if r.get("consolidation_note")]
    return {"vendor_count": len(vendors), "vendors": vendors,
            "one_off_inr": round(sum(x["fee_one_off_inr"]["value"] for x in vendors), 2),
            "batch_unit_inr": round(sum(x["fee_batch_unit_inr"]["value"] for x in vendors), 2),
            "steady_state": "not modelled: volume buying is assumed to reach free-shipping thresholds",
            "unsourced_rows": unsourced,
            "consolidation": {"moves": moves, "cannot_move": cannot,
                              "summary": (f"{len(moves)} item(s) moved to a vendor already in the list" if moves else
                                          "no item could move to a vendor already in the list")
                              + (f"; {len(cannot)} single-item vendor(s) stay: see cannot_move" if cannot else "")},
            "basis": d["basis"]}


PRINTED_OPS_BASIS = ("printed-only design (catalog package A): no core, cables, inserts, closing, flashing or functional test; "
                     "one module_install for fitting the adhesive feet when it has feet, a QC photo and packing (ASSUMPTION: "
                     "support removal is inside the print cost)")


def assembly_ops(spec: dict, purchased: list[dict]) -> dict:
    d = spec.get("derived") or {}
    if spec.get("kind") == "printed":
        feet = any("feet" in f"{h.get('id', '')} {h.get('name', '')}".lower() for h in d.get("hardware") or [] if isinstance(h, dict))
        return {"module_install": 1 if feet else 0, "qc_photo": 1, "pack": 1}
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


def lead_time(print_hours: float, ga: dict, pcb_needed: bool = True, parts_needed: bool = True) -> dict:
    lt = ga["lead_time"]
    pdays = math.ceil(print_hours / lt["print_hours_per_day"]["value"]) if print_hours > 0 else 0
    proc = lt["parts_procurement_days"]
    pcb = lt["pcb_fabrication_days"]
    asm, buf, cour = lt["assembly_flash_test_days"], lt["remake_buffer_days"], lt["courier_days"]
    if not pcb_needed:  # printed-only (catalog package A): parts are only the feet; no PCB, flashing or test
        lo = (proc["low"] if parts_needed else 0) + pdays + buf["low"] + cour["low"] + 1
        hi = (proc["high"] if parts_needed else 0) + pdays + buf["high"] + cour["high"] + 1
        return {
            "low": int(lo), "high": int(hi), "applies_to": pe.SCENARIOS[0].name, "status": "ESTIMATE",
            "reason": ((f"Parts ({proc['low']}-{proc['high']} days), then " if parts_needed else "")
                       + f"{pdays} printing day(s) for {print_hours:.1f} h of sliced print time, 1 day for finishing"
                       + (", feet" if parts_needed else "") + f" and a check, a {buf['low']}-{buf['high']} day remake buffer, and "
                       f"{cour['low']}-{cour['high']} courier days. No PCB, flashing or test: a printed-only design. Every duration "
                       "is an assumption, not a supplier commitment; the courier window needs the delivery pincode."),
            "steady_state": {"low": int(pdays + buf["low"] + cour["low"] + 1), "high": int(pdays + buf["high"] + cour["high"] + 1),
                             "applies_to": pe.SCENARIOS[2].name, "reason": "stocked feet: printing, finishing, buffer and courier only"},
            "breakdown": {"print_days": pdays, "parts_procurement_days": proc if parts_needed else None, "pcb_fabrication_days": None,
                          "finish_and_check_days": 1, "remake_buffer_days": buf, "courier_days": cour},
            "basis": {k: v.get("basis") for k, v in lt.items() if isinstance(v, dict)},
        }
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
               bbox_mm: Optional[list[float]] = None, draws: int = 20000, seed: int = 7,
               extra_reasons: Optional[list[str]] = None) -> dict:
    """print_parts: {PRINT_name: slice stats with filament_g, print_h, model_volume_cm3}. Returns quote.json."""
    t0 = time.perf_counter()
    assumptions = copy.deepcopy(_j("sim-assumptions.json"))
    ga = _j("gstudio-assumptions.json")
    ga_inb = _j(INBOUND)

    # ---- parts: one-off and batch price verified seen rows at their GST-inclusive seen price (MAJ-23); steady state and
    #      estimates use the planning range. One-off buys whole packs, volume pays its share of a pack.
    fracs = {k: float(v) for k, v in assumptions["parts_mode_by_qty"]["value"].items()}
    items, mods_one, mods_bat, mods_vol, parts_rec, missing, deltas = {}, [], [], [], [], [], []
    tot = {"one_off": 0.0, "batch_unit": 0.0, "steady_state_unit": 0.0}
    for r in purchased:
        key = r.get("module_id") or r.get("hardware_id") or r["name"]
        rng_v, basis_v, _cid = part_range(r, lib)
        rng_1, basis_1, _ = part_range(r, lib, fracs["1"])
        rng_10, _b10, _ = part_range(r, lib, fracs["10"])
        rec = {"name": r["name"], "module_id": r.get("module_id"), "quantity": r.get("quantity"), "packSize": r.get("packSize", 1),
               "price_range_inr": rng_v, "basis": basis_v, "one_off_basis": basis_1, "evidence_tier": r.get("evidence_tier"),
               "gst_basis": r.get("gst_basis")}
        parts_rec.append(rec)
        if rng_1 is None or rng_v is None:
            missing.append(r["name"])
            continue
        q_one, q_vol = pack_qty(r)
        rec["qty_one_off"], rec["qty_volume"] = q_one, round(q_vol, 4)
        items[f"{key}@1"] = {"row": r.get("catalog_row") or "", "name": r["name"], "low": rng_1[0], "high": rng_1[1]}
        items[f"{key}@10"] = {"row": r.get("catalog_row") or "", "name": r["name"], "low": rng_10[0], "high": rng_10[1]}
        items[f"{key}@100"] = {"row": r.get("catalog_row") or "", "name": r["name"], "low": rng_v[0], "high": rng_v[1]}
        mods_one.append({"catalog_id": f"{key}@1", "qty": q_one})
        mods_bat.append({"catalog_id": f"{key}@10", "qty": q_one})
        mods_vol.append({"catalog_id": f"{key}@100", "qty": q_vol})
        u1, u10, u100 = unit_value(rng_1, fracs["1"]), unit_value(rng_10, fracs["10"]), unit_value(rng_v, fracs["100"])
        rec["unit_one_off_inr"] = round(u1, 2)
        tot["one_off"] += u1 * q_one
        tot["batch_unit"] += u10 * q_one
        tot["steady_state_unit"] += u100 * q_vol
        if r.get("evidence_tier") == "seen" and r.get("price_inr_incl_gst") is not None and r.get("price_inr_planning") \
                and not str(r.get("price_basis") or "").startswith("estimate from"):
            plan_u = unit_value(r["price_inr_planning"], fracs["1"])
            deltas.append({"name": r["name"], "seen_incl_gst_inr": float(r["price_inr_incl_gst"]), "gst_basis": r.get("gst_basis"),
                           "planning_inr": r["price_inr_planning"], "planning_one_off_unit_inr": round(plan_u, 2),
                           "position": ("above" if float(r["price_inr_incl_gst"]) > r["price_inr_planning"][1] else
                                        "below" if float(r["price_inr_incl_gst"]) < r["price_inr_planning"][0] else "inside"),
                           "delta_one_off_inr": round((float(r["price_inr_incl_gst"]) - plan_u) * q_one, 2)})
    inb = inbound_shipping(purchased, fracs, ga_inb)
    for v in inb["vendors"]:
        if v["fee_one_off_inr"]["high"] > 0:
            items[f"INBOUND:{v['vendor']}@1"] = {"row": "", "name": f"inbound shipping from {v['vendor']}", "low": v["fee_one_off_inr"]["low"],
                                                  "high": v["fee_one_off_inr"]["high"]}
            mods_one.append({"catalog_id": f"INBOUND:{v['vendor']}@1", "qty": 1})
        if v["fee_batch_unit_inr"]["high"] > 0:
            items[f"INBOUND:{v['vendor']}@10"] = {"row": "", "name": f"inbound shipping from {v['vendor']} (batch share)",
                                                   "low": v["fee_batch_unit_inr"]["low"], "high": v["fee_batch_unit_inr"]["high"]}
            mods_bat.append({"catalog_id": f"INBOUND:{v['vendor']}@10", "qty": 1})

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
    extra = list(extra_reasons or [])
    if child and "LOCAL_VOICE_CAPTURE" in flags:
        extra.append("local voice capture in a child's room: privacy review of capture, indicator and storage before any sale (blueprint §11)")
    if missing:
        extra.append(f"{len(missing)} purchased item(s) have no planning price and are NOT in this total: {', '.join(missing)}")
    delta_total = round(sum(x["delta_one_off_inr"] for x in deltas), 2)
    if deltas:
        n_above = sum(1 for x in deltas if x["position"] == "above")
        n_below = sum(1 for x in deltas if x["position"] == "below")
        extra.append(f"{len(deltas)} part(s) are priced from GST-inclusive seen prices for the one-off and the batch: "
                     f"₹{abs(delta_total):,.0f} {'more' if delta_total >= 0 else 'less'} than their planning ranges would give "
                     f"({n_above} seen above the range, {n_below} below it); steady state still uses the planning ranges")
    in_box_adapter = any(str(r.get("role") or "").lower() == "adapter" and r.get("in_box") for r in purchased)
    if spec.get("kind") == "printed":  # catalog package A: nothing to power
        extra.append("printed-only design: no electronics, so no power supply, PCB, firmware or functional test is priced")
    elif not in_box_adapter:
        extra.append("no USB-C power adapter in the box: the buyer supplies a 5 V 3 A USB-C supply")
    variant = {"modules": mods_one, "assembly_ops": ops, "cloud": None, "child_directed": child, "extra_reasons": extra,
               "launch_status": "REVIEW_REQUIRED"}
    eng_spec = {"variants": {"one_off": variant, "batch": dict(variant, modules=mods_bat), "volume": dict(variant, modules=mods_vol)}}
    eng = pe.Engine({"items": items}, eng_spec, assumptions, sl)

    scen = []
    for sc in pe.SCENARIOS:
        q = eng.quote({"1": "one_off", "10": "batch"}.get(sc.qty_key, "volume"), sc, n=draws, seed=seed)
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
    is_printed = spec.get("kind") == "printed"
    lt = lead_time(hours, ga, pcb_needed=not is_printed, parts_needed=bool(purchased))
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
            "includes": ["purchased parts (GST-inclusive seen prices for the one-off and batch, planning ranges at steady state)",
                         "inbound shipping from each vendor (one-off and batch, ASSUMPTION fees)",
                         "printed parts from the sliced grams and hours",
                         "finishing, feet, a QC photo and packing (no electronics)" if is_printed else "assembly, flashing and functional test",
                         "one-time setup share (one-off and batch only)", "courier within India (estimate, pincode unknown)",
                         "warranty and returns reserve", "payment gateway fee", "GST at an assumed 18%"]
                        + (["the 5 V 3 A USB-C adapter (in the box)"] if in_box_adapter else []),
            "excludes": ["import duties (all parts assumed bought in India)"]
                        + ([f"parts with no price: {', '.join(missing)}"] if missing else [])  # plain text (MIN-27)
                        + ([] if in_box_adapter or is_printed else ["USB-C power adapter"]),
        },
        "parts_total_inr": {"one_off": round(tot["one_off"]), "batch_unit": round(tot["batch_unit"]),
                            "steady_state_unit": round(tot["steady_state_unit"]), "complete": not missing,
                            "basis": ("sum over purchased rows of the per-unit value the price engine centres on x packs bought: "
                                      "GST-inclusive seen prices for the one-off and batch, the planning range at the "
                                      f"{fracs['1']:.0%}/{fracs['10']:.0%}/{fracs['100']:.0%} position otherwise; parts only "
                                      "(no inbound shipping, print, labour or margin)")},
        "price_deltas": {"rows": deltas, "total_one_off_inr": delta_total,
                         "basis": "GST-inclusive seen price minus the planning range at its one-off position, times packs bought"},
        "inbound_shipping": inb,
        "lead_time_days": lt,
        "scenarios": scen,
        "inputs": {
            "parts": parts_rec,
            "seen_above_planning": [x for x in deltas if x["position"] == "above"],
            "print": {"grams": round(grams, 2), "hours": round(hours, 3), "parts": print_parts and {k: {kk: v.get(kk) for kk in ("filament_g", "print_h", "supports", "support_g")} for k, v in print_parts.items()},
                      "source": "print/slice-stats.json (PrusaSlicer run on these STLs)"},
            "assembly_ops": ops,
            "assembly_ops_basis": PRINTED_OPS_BASIS if is_printed else ga["assembly_ops_rule"]["basis"],
            "logistics": {"box_cm": assumptions["logistics"]["box_cm"], "packed_weight_kg": assumptions["logistics"]["packed_weight_kg"]},
            "assumptions_files": ["gstudio/order/data/sim-assumptions.json", "gstudio/order/data/gstudio-assumptions.json",
                                  "gstudio/order/data/inbound-shipping.json"],
            "draws": draws, "seed": seed,
        },
        "cross_checks": {
            "bureau_fdm_inr": {"low": round(vol_cm3 * bureau["low"]), "high": round(vol_cm3 * bureau["high"]),
                               "model_volume_cm3": round(vol_cm3, 1), "basis": bureau["basis"]},
        },
        "engine": {"module": "gstudio.order.price_engine", "vendored_from": "sim/price_engine.py", "seconds": round(time.perf_counter() - t0, 2)},
        "limits": [
            "Every rupee is a dated retail price, a planning range or a labelled assumption, not a supplier quote; no contract or rate card exists yet (blueprint §7.1).",
            "Part prices are GST-inclusive retail prices (no input-tax credit assumed); rows whose GST basis is not stated are taken as ex-GST plus 18%.",
            "Courier cost and delivery window assume an Indian destination; the pincode is unknown.",
            "GST is applied at 18% as an assumption; the product's HSN classification needs a tax adviser (blueprint §8.7).",
            "Print cost uses the sliced grams and hours of a generic 0.4 mm FDM profile; a print bureau's own slicer will differ.",
        ],
    }
