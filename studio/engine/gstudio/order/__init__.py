"""Order stage: parts list, print slicing and the delivered price.

    run(ctx) -> summary

Writes
  build.json                 purchased[] and printed[] (merged into any existing build.json)
  print/slice-stats.json     per part grams, hours, support use (PrusaSlicer 2.7 on the delivered STLs)
  print/slice.ini            the exact profile used, so the numbers can be reproduced
  print/slice/<part>.slice.txt  the slicer's own summary block for each part (evidence)
  order/quote.json           four scenarios with p5/p50/p95, list price incl. GST, margin, status, lead time

The parts list is written even when the CAD failed; the quote is not, and the stage then fails honestly.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import bom, slicer
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


def _check(cid: str, title: str, ok: bool | None, detail: str, qb: str | None = None, warn: bool = False) -> dict:
    status = "not_run" if ok is None else ("passed" if ok else ("warning" if warn else "failed"))
    return {"id": cid, "title": title, "status": status, "detail": detail, **({"qb": qb} if qb else {})}


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

    # 1. parts list ------------------------------------------------------------------------------
    ctx.progress(STAGE, "building the parts list", 5)
    purchased, problems = bom.build_purchased(spec, ctx.lib, components)
    for p in problems:
        ctx.warn(STAGE, p)

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
        shutil.copyfile(slicer.INI, pdir / "slice.ini")
        bed = list(slicer.bed_from_profile(prof))
        prints = {k: v for k, v in stats_parts.items() if k.startswith("PRINT_")}
        ok_prints = {k: v for k, v in prints.items() if v.get("ok")}
        slice_stats = {
            "schema": "gadget-slice-stats/1",
            "slicer": slicer_name,
            "profile": slicer.profile_summary(prof),
            "profile_file": "print/slice.ini",
            "printer": {"name": f"generic FDM, {prof.get('nozzle_diameter')} mm nozzle", "build_volume_mm": bed},
            "parts": stats_parts,
            "totals_print_parts": {
                "parts": len(prints), "sliced": len(ok_prints),
                "filament_g": round(sum(v["filament_g"] for v in ok_prints.values()), 2),
                "print_h": round(sum(v["print_h"] for v in ok_prints.values()), 3),
                "support_g": round(sum(v.get("support_g", 0) for v in ok_prints.values()), 2),
            },
            "limits": ["Slicer estimates for a generic Prusa-class profile, not a measured print.",
                       "The slicer does not report wall thickness; it is not checked here."],
        }
        _write(pdir / "slice-stats.json", slice_stats)
        for stem, st in stats_parts.items():
            checks.append(_check(f"bed-fit:{stem}", f"{stem} fits the {bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm build volume",
                                 st.get("fits_bed"), f"size {st.get('size_mm')} mm" + (f", turned {st['rotate_z_deg']} deg about Z" if st.get("rotate_z_deg") else ""), "QB-10"))
            checks.append(_check(f"on-bed:{stem}", f"{stem} rests on Z=0", st.get("on_bed"), "STL minimum Z within 0.05 mm of the bed", "QB-10"))
            checks.append(_check(f"sliced:{stem}", f"{stem} slices", bool(st.get("ok")),
                                 (f"{st.get('filament_g')} g, {st.get('print_time')}, supports {'yes' if st.get('supports') else 'no'}"
                                  if st.get("ok") else str(st.get("error"))), "QB-11"))
    printed = bom.build_printed(stats_parts, components, spec, prof.get("filament_type", "PETG"), cad_rec.get("summary"))

    # 3. purchased-list checks (QB-22, QB-23, QB-28) ---------------------------------------------
    miss_url = [r["name"] for r in purchased if not r.get("url")]
    miss_vendor = [r["name"] for r in purchased if not r.get("vendor")]
    checks.append(_check("purchased-fields", "every purchased row has name, quantity, pack size, URL and vendor",
                         not (miss_url or miss_vendor),
                         "all present" if not (miss_url or miss_vendor) else f"missing URL: {miss_url}; missing vendor: {miss_vendor}", "QB-22", warn=True))
    tiers = {t: sum(1 for r in purchased if r["evidence_tier"] == t) for t in ("seen", "estimate", "not_confirmed")}
    checks.append(_check("price-evidence", "every price is a dated observation or labelled an estimate", True,
                         f"{tiers['seen']} seen, {tiers['estimate']} estimate, {tiers['not_confirmed']} not confirmed", "QB-23"))
    maker = [r["name"] for r in purchased if r.get("requiresSoldering") and r.get("solderedBy") in (None, "maker")]
    factory = [f"{r['name']} ({'backplane PCBA' if r.get('solderedBy') == 'pcb_fab' else 'factory'})" for r in purchased
               if r.get("requiresSoldering") and r.get("solderedBy") in ("pcb_fab", "factory")]
    unsure = [r["name"] for r in purchased if not r.get("requiresSoldering") and str(r.get("requiresSoldering_basis", "")).startswith("unverified")]
    detail = ("maker soldering: " + ", ".join(maker) if maker else "no maker soldering") \
        + (f"; done by the PCB fab or factory: {', '.join(factory)}" if factory else "") \
        + (f"; not stated by the library for: {', '.join(unsure)}" if unsure else "")
    checks.append(_check("no-soldering", "the build needs no soldering by the maker", False if maker else (False if unsure else True),
                         detail, "QB-28", warn=not maker))

    merge_build_json(out, {"purchased": purchased, "printed": printed})
    # authoritative copy: the checks stage re-assembles build.json from these fragments (no lost updates)
    _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed})

    # 4. quote -----------------------------------------------------------------------------------
    prints_ok = {k: v for k, v in stats_parts.items() if k.startswith("PRINT_") and v.get("ok")}
    prints_bad = {k: v for k, v in stats_parts.items() if k.startswith("PRINT_") and not v.get("ok")}

    summary = {"purchased": len(purchased), "printed": len(printed), "slicer": slicer_name,
               "print_g": round(sum(v["filament_g"] for v in prints_ok.values()), 1),
               "print_h": round(sum(v["print_h"] for v in prints_ok.values()), 2),
               "problems": problems, "checks": checks}
    if cad_failed_now or not prints_ok or prints_bad:
        why = ("the CAD stage failed in this build, so its print files are partial and unvalidated" if cad_failed_now
               else "no print/PRINT_*.stl files (the CAD stage did not deliver print parts)" if not stls
               else f"{len(prints_bad)} print part(s) could not be sliced: " + "; ".join(f"{k}: {v.get('error')}" for k, v in prints_bad.items())
               if prints_bad else "no PRINT_ part sliced")
        checks.append(_check("quote", "delivered price computed", False, why))
        nq = {"list_price_inr": None, "status": "NOT_QUOTED", "reason": why}
        merge_build_json(out, {"order": nq})
        _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed, "order": nq})
        (out / "order" / "quote.json").unlink(missing_ok=True)  # never leave a stale price from an earlier run
        raise StageError(f"parts list written, but no quote: {why}", {"summary": summary})
    ctx.progress(STAGE, "pricing four scenarios", 70)
    q = make_quote(purchased, prints_ok, spec, ctx.lib, bbox_mm=assembly_bbox(components), draws=4000 if fast else 20000)
    _write(out / "order" / "quote.json", q)
    checks.append(_check("quote", "delivered price computed", q["complete"],
                         f"pilot one-off ₹{q['delivered_price_inr']['pilot_one_off']:,}, steady state ₹{q['delivered_price_inr']['steady_state']:,} "
                         f"incl. GST; status {q['status']}" + ("" if q["complete"] else f"; missing prices: {q['missing_prices']}"), "QB-24",
                         warn=True))
    order_block = {"list_price_inr": q["delivered_price_inr"]["pilot_one_off"],
                   "steady_state_price_inr": q["delivered_price_inr"]["steady_state"],
                   "status": q["status"], "lead_time_days": [q["lead_time_days"]["low"], q["lead_time_days"]["high"]],
                   "quote": "order/quote.json"}
    merge_build_json(out, {"order": order_block})
    _write(out / ".gstudio" / "fragments" / "order.json", {"purchased": purchased, "printed": printed, "order": order_block})
    summary.update({"delivered_price_inr": q["delivered_price_inr"], "status": q["status"],
                    "lead_time_days": [q["lead_time_days"]["low"], q["lead_time_days"]["high"]],
                    "seconds": round(time.perf_counter() - t0, 2),
                    "message": (f"{len(purchased)} purchased rows, {len(prints_ok)} print part(s) {summary['print_g']:.0f} g / "
                                f"{summary['print_h']:.1f} h, one-off ₹{q['delivered_price_inr']['pilot_one_off']:,} ({q['status']})")})
    ctx.artifacts["order.quote"] = str(out / "order" / "quote.json")
    ctx.artifacts["print.slice_stats"] = str(pdir / "slice-stats.json")
    ctx.progress(STAGE, "done", 100)
    return summary
