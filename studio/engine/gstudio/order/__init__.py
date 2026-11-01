"""Order stage: parts list, print slicing and the delivered price.

    run(ctx) -> summary

Writes
  build.json                 purchased[] and printed[] (merged into any existing build.json)
  references/listings/       the raw vendor listing behind every "seen" price, with its sha256 (MAJ-25)
  print/slice-stats.json     per part grams, hours, support use (PrusaSlicer 2.7 on the delivered STLs)
  print/slice.ini            the exact profile used, so the numbers can be reproduced
  print/slice/<part>.slice.txt  the slicer's own summary block for each part (evidence)
  order/quote.json           four scenarios with p5/p50/p95, list price incl. GST, margin, status, lead time

The parts list is written even when the CAD failed; the quote is not, and the stage then fails honestly.
When the CAD's collision check fails (MAJ-6 gate) the quote is HELD: quote.json names the colliding pairs and carries
no price.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import bom, pricecheck, slicer
from .quote import make_quote

STAGE = "order"


def _read(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


def _spec_dict(ctx) -> dict:
    s = ctx.spec
    if hasattr(s, "to_json_dict"):
        return s.to_json_dict(True)
    if hasattr(s, "model_dump"):
        return s.model_dump(mode="json", by_alias=True)
    return dict(s)


def merge_build_json(out: Path, updates: dict) -> dict:
    """Update top-level keys of build.json, keeping keys other stages wrote (steps, wiring, tests)."""
    try:
        from gstudio.version import BUILD_SCHEMA
    except ImportError:  # pragma: no cover
        BUILD_SCHEMA = "gadget-build/1"
    p = out / "build.json"
    cur = _read(p, {}) or {}
    cur["schema"] = BUILD_SCHEMA
    cur.update(updates)
    _write(p, cur)
    return cur


def assembly_bbox(components: dict) -> list[float] | None:
    """Overall [x, y, z] size of the non-REFERENCE components (mm)."""
    boxes = [c.get("bbox") for n, c in (components or {}).items()
             if isinstance(c, dict) and not n.startswith("REFERENCE_") and isinstance(c.get("bbox"), (list, tuple)) and len(c["bbox"]) == 6]
    if not boxes:
        return None
    lo = [min(b[i] for b in boxes) for i in (0, 2, 4)]
    hi = [max(b[i] for b in boxes) for i in (1, 3, 5)]
    return [round(hi[i] - lo[i], 2) for i in range(3)]


def _check(cid: str, title: str, ok: bool | None, detail: str, qb: str | None = None, warn: bool = False,
           artifact: str | None = None, status: str | None = None) -> dict:
    status = status or ("not_run" if ok is None else ("passed" if ok else ("warning" if warn else "failed")))
    return {"id": cid, "title": title, "status": status, "detail": detail, **({"qb": qb} if qb else {}),
            "artifact": artifact or "build.json"}


def words(xs) -> str:
    """Plain comma-joined words for check details (MIN-28): never a Python list repr."""
    return ", ".join(str(x) for x in xs) if xs else "none"


def _library_root(lib) -> Path | None:
    p = getattr(lib, "_backplane_path", None)
    if p:
        return Path(p).parent
    d = Path(__file__).resolve().parents[3] / "library"
    return d if d.exists() else None


def collision_gate(out: Path) -> dict:
    """MAJ-6: {buildable, pairs[]} from the CAD's own collision check (cad/digital-checks.json); buildable is None when
    the CAD has not reported one."""
    d = _read(out / "cad" / "digital-checks.json", None)
    cs = (d or {}).get("checks") if isinstance(d, dict) else None
    c = next((x for x in cs or [] if isinstance(x, dict) and x.get("id") == "collisions"), None)
    if c is None:
        return {"buildable": None, "pairs": []}
    pairs = [{"parts": list(i.get("parts") or []), "mm3": i.get("intersection_mm3")} for i in ((c.get("data") or {}).get("intersections") or [])
             if isinstance(i, dict)]
    return {"buildable": c.get("status") != "failed", "pairs": pairs, "detail": c.get("detail")}


def pairs_text(pairs: list[dict]) -> str:
    def mm3(v) -> str:
        v = float(v or 0)
        return f"{v:.1f}" if v >= 0.1 else f"{v:.3f}"
    return "; ".join(f"{' / '.join(p['parts'])} ({mm3(p['mm3'])} mm3)" for p in pairs) or "see cad/digital-checks.json"


def run(ctx) -> dict:
    from gstudio.errors import StageError

    t0 = time.perf_counter()
    out = Path(ctx.out)
    spec = _spec_dict(ctx)
    fast = bool(ctx.options.get("fast"))
    components = _read(out / "cad" / "components.json", {}) or {}
    cad_rec = _read(out / ".gstudio" / "stages" / "cad.json", {}) or {}
    # a CAD stage that failed in THIS build leaves partial, unvalidated print files: list the parts, but never price them
    cad_failed_now = bool(cad_rec.get("state") == "failed" and os.environ.get("GSTUDIO_RUN")
                          and cad_rec.get("engine_run") == os.environ.get("GSTUDIO_RUN"))
    checks: list[dict] = []

    electrical = getattr(ctx, "electrical", None) or _read(out / "electrical.json", None)

    # 1. parts list ------------------------------------------------------------------------------
    ctx.progress(STAGE, "building the parts list", 5)
    purchased, problems = bom.build_purchased(spec, ctx.lib, components, electrical,
                                              consolidate=bool(ctx.options.get("consolidate_vendors", True)))
    for p in problems:
        ctx.warn(STAGE, p)
    # MAJ-22: the backplane is a populated board priced from the PCB stage's pcba.json; the USB-C receptacle is one of its lines
    pcba_reasons = bom.apply_pcba(purchased, _read(out / "pcb" / "pcba.json", None), _read(out / "pcb" / "pcb-summary.json", None))
    # MAJ-25: a "seen" price stays seen only when its listing is re-read now; the raw response ships with the delivery
    ctx.progress(STAGE, "re-reading the vendor listings behind the seen prices", 8)
    shutil.rmtree(out / "references" / "listings", ignore_errors=True)
    shutil.rmtree(out / "references" / "library", ignore_errors=True)
    lib_root = _library_root(ctx.lib)
    price_report = pricecheck.check_rows(purchased, out, mode=str(ctx.options.get("price_check") or "live"), library_root=lib_root)
    price_report["library_snapshots"] = pricecheck.copy_library_snapshots(purchased, out, lib_root)  # MIN-23

    # 2. slicing ---------------------------------------------------------------------------------
    pdir = out / "print"
    stls = [] if cad_failed_now else sorted(pdir.glob("PRINT_*.stl")) + sorted(pdir.glob("TEST_*.stl"))
    prof = slicer.read_profile(slicer.INI)
    stats_parts: dict = {}
    slicer_name = None
    if stls:
        ctx.progress(STAGE, f"slicing {len(stls)} print file(s)", 15)
        exe = slicer.find_slicer()
        slicer_name = slicer.slicer_version(exe) if exe else None
        work = out / ".gstudio" / "slice"
        stats_parts = slicer.slice_all(stls, work, slicer.INI, timeout=600 if fast else 900)
        ev = pdir / "slice"
        ev.mkdir(parents=True, exist_ok=True)
        for stem, st in stats_parts.items():
            src = work / f"{stem}.slice.txt"
            if src.exists():
                shutil.copyfile(src, ev / src.name)
            st["evidence"] = f"print/slice/{stem}.slice.txt" if src.exists() else None
            ov = st.get("profile_override")
            if isinstance(ov, dict) and (work / str(ov.get("file"))).exists():  # this part's own profile, so its numbers reproduce
                shutil.copyfile(work / str(ov["file"]), ev / str(ov["file"]))
                ov["file"] = f"print/slice/{ov['file']}"
        shutil.copyfile(slicer.INI, pdir / "slice.ini")
        bed = list(slicer.bed_from_profile(prof))
        prints = {k: v for k, v in stats_parts.items() if k.startswith("PRINT_")}
        ok_prints = {k: v for k, v in prints.items() if v.get("ok")}
        printer = slicer.printer_profile(prof)
        slice_stats = {
            "schema": "gadget-slice-stats/1",
            "slicer": slicer_name,
            "profile": slicer.profile_summary(prof),
            "profile_file": "print/slice.ini",
            "material": slicer.profile_material(prof),
            "printer": printer,
            "parts": stats_parts,
            "totals_print_parts": {
                "parts": len(prints), "sliced": len(ok_prints),
                "filament_g": round(sum(v["filament_g"] for v in ok_prints.values()), 2),
                "print_h": round(sum(v["print_h"] for v in ok_prints.values()), 3),
                "support_g": round(sum(v.get("support_g", 0) for v in ok_prints.values()), 2),
            },
            "limits": ["Slicer estimates for the named printer profile, not a measured print.",
                       "The slicer does not report wall thickness; it is not checked here."],
        }
        _write(pdir / "slice-stats.json", slice_stats)
        for stem, st in stats_parts.items():
            size = " x ".join(f"{float(v):.1f}" for v in st.get("size_mm") or []) or "unknown"
            o = st.get("orientation") if isinstance(st.get("orientation"), dict) else {}
            title = f"{stem} fits the {printer['class']} ({bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm)"
            if st.get("tipped"):  # fits only tipped onto a side: say so plainly, as a warning (the CAD planned it upright)
                checks.append(_check(f"bed-fit:{stem}", title, None,
                                     f"size {size} mm as exported, too big to print that way; it "
                                     + (o.get("sentence") or f"prints on its side, {o.get('label')}"),
                                     "QB-10", artifact="print/slice-stats.json", status="warning"))
            else:
                checks.append(_check(f"bed-fit:{stem}", title, st.get("fits_bed"),
                                     f"size {size} mm" + (f", turned {st['rotate_z_deg']} deg about Z" if st.get("rotate_z_deg") else ""),
                                     "QB-10", artifact="print/slice-stats.json"))
            checks.append(_check(f"on-bed:{stem}", f"{stem} rests on Z=0", st.get("on_bed"), "STL minimum Z within 0.05 mm of the bed", "QB-10",
                                 artifact=f"print/{stem}.stl"))
            checks.append(_check(f"sliced:{stem}", f"{stem} slices", bool(st.get("ok")),
                                 (f"{st.get('filament_g')} g, {st.get('print_time')}, supports {'yes' if st.get('supports') else 'no'}"
                                  + (f", sliced {o.get('label')}" if st.get("tipped") else "")
                                  if st.get("ok") else str(st.get("error"))), "QB-11", artifact="print/slice-stats.json"))
    printed = bom.build_printed(stats_parts, components, spec, slicer.profile_material(prof), cad_rec.get("summary"), prof)

    # 3. purchased-list checks (QB-22, QB-23, QB-28) ---------------------------------------------
    def drawn(r: dict) -> bool:
        return bool(r.get("made_to_drawing") or (r.get("factory_make") or {}).get("drawing"))
    miss_url = [r["name"] for r in purchased if not r.get("url") and not drawn(r)]
    miss_vendor = [r["name"] for r in purchased if not r.get("vendor") and not drawn(r)]
    miss_sku = [r["name"] for r in purchased if not r.get("sku")]
    bad = miss_url or miss_vendor or miss_sku
    checks.append(_check("purchased-fields", "every purchased row has name, quantity, pack size, SKU, and a URL and vendor (or a factory drawing)",
                         not bad, "all present" if not bad else "; ".join(x for x in (
                             f"missing URL: {words(miss_url)}" if miss_url else "", f"missing vendor: {words(miss_vendor)}" if miss_vendor else "",
                             f"missing SKU: {words(miss_sku)}" if miss_sku else "") if x), "QB-22"))
    unknown_pack = [r["name"] for r in purchased if r.get("packSize") is None and int(r.get("quantity") or 1) > 1]
    checks.append(_check("pack-size-known", "every row bought in quantity has a recorded pack size", not unknown_pack,
                         "all recorded" if not unknown_pack else f"pack size not recorded (priced as one set): {words(unknown_pack)}", "QB-22"))
    tiers = {t: sum(1 for r in purchased if r["evidence_tier"] == t) for t in ("seen", "estimate", "not_confirmed")}
    seen_rows = [r for r in purchased if r["evidence_tier"] == "seen"]
    bad_rc = [f"{r['name']}: {why}" for r in seen_rows for ok, why in [pricecheck.verify_row(r, out)] if not ok]
    checks.append(_check("price-receipts-signed", "every seen price has a signed receipt over a saved listing snapshot", not bad_rc,
                         (f"{len(seen_rows)} seen rows signed ({price_report['mode']}, {price_report['seconds']:.1f} s); "
                          f"{tiers['estimate']} estimate, {tiers['not_confirmed']} not confirmed"
                          + (f"; re-read failed, now estimates: {words(price_report['dropped'])}" if price_report["dropped"] else "")
                          + (f"; does not verify: {words(bad_rc)}" if bad_rc else "")), "QB-23", artifact="references/listings"))
    # QB-28: we deliver built devices, so PCBA, factory pre-wiring and harnesses made to drawing are fine; only maker soldering fails
    maker = [r["name"] for r in purchased if r.get("solderedBy") == "maker"]
    work = bom.factory_work(purchased, electrical, ctx.lib)
    unknown = [r["name"] for r in purchased if r.get("requiresSoldering") is None]
    fitted = {w["name"] for w in work if w.get("kind") == "header_fit"}
    no_fit = [n for n in unknown if n not in fitted]
    detail = ("maker soldering: " + words(maker) if maker else "no maker soldering") \
        + (f"; done before delivery: {words(sorted({w['name'] + ' (' + w['by'] + ')' for w in work if w.get('kind') != 'header_fit'}))}"
           if work else "") \
        + (f"; soldering not stated by the library (the factory checks the headers on receipt): {words(unknown)}" if unknown else "") \
        + (f"; no factory header check for: {words(no_fit)}" if no_fit else "")
    checks.append(_check("no-soldering", "no part needs soldering by the maker", None, detail, "QB-28",
                         status="failed" if (maker or no_fit) else ("warning" if unknown else "passed")))

    merge_build_json(out, {"purchased": purchased, "printed": printed, "factory_work": work})
    # authoritative copy: the checks stage re-assembles build.json from these fragments (no lost updates)
    _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed, "factory_work": work})

    # 4. quote -----------------------------------------------------------------------------------
    prints_ok = {k: v for k, v in stats_parts.items() if k.startswith("PRINT_") and v.get("ok")}
    prints_bad = {k: v for k, v in stats_parts.items() if k.startswith("PRINT_") and not v.get("ok")}

    summary = {"purchased": len(purchased), "printed": len(printed), "slicer": slicer_name,
               "print_g": round(sum(v["filament_g"] for v in prints_ok.values()), 1),
               "print_h": round(sum(v["print_h"] for v in prints_ok.values()), 2),
               "tipped": {k: {"label": (v.get("orientation") or {}).get("label"), "supports": v.get("supports")}
                          for k, v in stats_parts.items() if v.get("tipped")},
               "problems": problems, "checks": checks}
    if cad_failed_now or not prints_ok or prints_bad:
        why = ("the CAD stage failed in this build, so its print files are partial and unvalidated" if cad_failed_now
               else "no print/PRINT_*.stl files (the CAD stage did not deliver print parts)" if not stls
               else f"{len(prints_bad)} print part(s) could not be sliced: " + "; ".join(f"{k}: {v.get('error')}" for k, v in prints_bad.items())
               if prints_bad else "no PRINT_ part sliced")
        checks.append(_check("quote", "delivered price computed", False, why, artifact="order/quote.json"))
        nq = {"list_price_inr": None, "status": "NOT_QUOTED", "reason": why}
        merge_build_json(out, {"order": nq})
        _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed, "factory_work": work, "order": nq})
        (out / "order" / "quote.json").unlink(missing_ok=True)  # never leave a stale price from an earlier run
        raise StageError(f"parts list written, but no quote: {why}", {"summary": summary})
    ctx.progress(STAGE, "pricing four scenarios", 70)
    q = make_quote(purchased, prints_ok, spec, ctx.lib, bbox_mm=assembly_bbox(components), draws=4000 if fast else 20000,
                   extra_reasons=pcba_reasons)
    q["price_check"] = price_report
    gate = collision_gate(out)
    summary["price_check"] = price_report
    if gate["buildable"] is False:  # MAJ-6: never offer a price for a design the CAD shows cannot be assembled
        held = {"schema": q["schema"], "currency": "INR", "status": "HELD", "held": True, "binding": False, "complete": False,
                "status_label": "Quote held: the CAD shows parts colliding, so this version is not buildable",
                "held_because": gate["pairs"], "delivered_price_inr": None,
                "reasons": [f"collisions: {pairs_text(gate['pairs'])}", "revise the design (move or resize the feature) and rebuild for a price"],
                "parts_total_inr": q["parts_total_inr"], "inbound_shipping": q["inbound_shipping"], "price_check": price_report,
                "limits": q["limits"]}
        _write(out / "order" / "quote.json", held)
        checks.append(_check("quote", "delivered price computed", None, f"held: not buildable ({pairs_text(gate['pairs'])})",
                             "QB-24", artifact="order/quote.json"))
        order_block = {"list_price_inr": None, "status": "HELD", "held_because": gate["pairs"], "quote": "order/quote.json",
                       "parts_total_inr": q["parts_total_inr"]["one_off"]}
        merge_build_json(out, {"order": order_block})
        _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed, "factory_work": work, "order": order_block})
        summary.update({"status": "HELD", "held_because": gate["pairs"], "seconds": round(time.perf_counter() - t0, 2),
                        "say": f"Quote held: the CAD shows {len(gate['pairs'])} colliding pair(s) ({pairs_text(gate['pairs'])[:200]}).",
                        "message": f"{len(purchased)} purchased rows; quote HELD (not buildable: {len(gate['pairs'])} collision pair(s))"})
        ctx.artifacts["order.quote"] = str(out / "order" / "quote.json")
        ctx.progress(STAGE, "done (quote held)", 100)
        return summary
    _write(out / "order" / "quote.json", q)
    checks.append(_check("quote", "delivered price computed", q["complete"],
                         f"pilot one-off ₹{q['delivered_price_inr']['pilot_one_off']:,}, steady state ₹{q['delivered_price_inr']['steady_state']:,} "
                         f"incl. GST; parts ₹{q['parts_total_inr']['one_off']:,}; inbound shipping from {q['inbound_shipping']['vendor_count']} "
                         f"vendor(s); status {q['status']}" + ("" if q["complete"] else f"; missing prices: {words(q['missing_prices'])}"), "QB-24",
                         warn=True, artifact="order/quote.json"))
    order_block = {"list_price_inr": q["delivered_price_inr"]["pilot_one_off"],
                   "steady_state_price_inr": q["delivered_price_inr"]["steady_state"],
                   "parts_total_inr": q["parts_total_inr"]["one_off"],
                   "status": q["status"], "lead_time_days": [q["lead_time_days"]["low"], q["lead_time_days"]["high"]],
                   "quote": "order/quote.json"}
    merge_build_json(out, {"order": order_block})
    _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed, "factory_work": work, "order": order_block})
    summary.update({"delivered_price_inr": q["delivered_price_inr"], "status": q["status"], "parts_total_inr": q["parts_total_inr"],
                    "lead_time_days": [q["lead_time_days"]["low"], q["lead_time_days"]["high"]],
                    "seconds": round(time.perf_counter() - t0, 2),
                    "message": (f"{len(purchased)} purchased rows, {len(prints_ok)} print part(s) {summary['print_g']:.0f} g / "
                                f"{summary['print_h']:.1f} h, one-off ₹{q['delivered_price_inr']['pilot_one_off']:,} ({q['status']})"
                                + (f"; prints on its side: {words(sorted(summary['tipped']))}" if summary["tipped"] else ""))})
    ctx.artifacts["order.quote"] = str(out / "order" / "quote.json")
    ctx.artifacts["print.slice_stats"] = str(pdir / "slice-stats.json")
    ctx.progress(STAGE, "done", 100)
    return summary
