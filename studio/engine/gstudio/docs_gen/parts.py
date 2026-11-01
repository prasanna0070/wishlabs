"""PARTS_EVIDENCE.md: per part the chosen source, the alternatives, the price evidence tier and the dimensional basis."""
from __future__ import annotations

from typing import Any

from .util import Bundle, inr, table

TIER_TEXT = {
    "seen": ("seen: a retail price re-read from the vendor's listing at build time; the raw listing is saved in "
             "`references/listings/` and the row carries a signed receipt (not reserved stock)"),
    "estimate": "estimate: a planning range only; confirm before ordering",
    "not_confirmed": "not confirmed: no price recorded",
}


def _module(lib: Any, mid: str | None) -> dict:
    from gstudio.order.bom import module

    return module(lib, mid)


def _evidence_text(m: dict) -> str:
    ev = m.get("evidence")
    if isinstance(ev, str):
        return ev
    if isinstance(ev, dict):
        bits = []
        for k in ("dims", "dimensions", "dims_source", "datasheet", "source", "pinout", "notes"):
            if ev.get(k):
                bits.append(f"{k}: {ev[k]}")
        return "; ".join(bits) or ", ".join(f"{k}: {v}" for k, v in ev.items() if isinstance(v, (str, int, float)))[:600]
    if isinstance(ev, list):
        return "; ".join(str(e.get("what") or e.get("url") or e) if isinstance(e, dict) else str(e) for e in ev)[:600]
    return ""


def _uncertain_text(m: dict) -> str:
    u = m.get("uncertain")
    if not u:
        return ""
    if isinstance(u, list):
        return "; ".join(str(x) for x in u)
    if isinstance(u, dict):
        return "; ".join(f"{k}: {v}" for k, v in u.items())
    return str(u)


def solder_word(r: dict) -> str:
    if r.get("requiresSoldering") is None:
        return "unknown: factory checks on receipt"
    return "yes" if r.get("requiresSoldering") else "no"


def render(b: Bundle, lib: Any = None) -> str:
    sp = b.spec
    rows = b.build.get("purchased") or []
    L: list[str] = [f"# {sp.get('name', 'Gadget')}: parts, sources and evidence", ""]
    L.append("Every part comes from the Gadget Studio module library; no other part may appear in a design. Sources are "
             "local-first for India. Nothing has been ordered. Retail prices and stock are snapshots, not reserved inventory, "
             "and a price check is not evidence of a seller's reliability.")

    seen = [r for r in rows if r.get("evidence_tier") == "seen"]
    est = [r for r in rows if r.get("evidence_tier") == "estimate"]
    nc = [r for r in rows if r.get("evidence_tier") == "not_confirmed"]
    # MAJ-23: GST-inclusive figures only; one parts total, the quote's
    seen_total = sum((r.get("price_inr_incl_gst") or r.get("price_inr_seen") or 0) * (r.get("packsToBuy") or 1) for r in seen)
    est_lo = sum((r["price_inr_planning"][0] if r.get("price_inr_planning") else 0) * (r.get("packsToBuy") or 1) for r in est)
    est_hi = sum((r["price_inr_planning"][1] if r.get("price_inr_planning") else 0) * (r.get("packsToBuy") or 1) for r in est)
    unknown_gst = [r["name"] for r in seen if r.get("gst_basis") == "unknown"]
    q = b.quote or {}
    pt = q.get("parts_total_inr") or {}
    L += ["", "## Costs at a glance", ""]
    if pt.get("one_off") is not None:
        L.append(f"- **Parts total for one unit: {inr(pt['one_off'])}** (GST-inclusive; the figure the quote and the Parts tab use). "
                 f"{pt.get('basis', '')}.")
    L.append(f"- {len(seen)} item(s) have a re-read retail price: {inr(seen_total)} incl. GST for the packs this build buys"
             + (f" ({len(unknown_gst)} of them state no GST basis and are taken as ex-GST plus 18%: {', '.join(unknown_gst)})" if unknown_gst else "")
             + ".")
    L.append(f"- {len(est)} item(s) are estimates: {inr(est_lo)} to {inr(est_hi)} from planning ranges.")
    if nc:
        L.append(f"- {len(nc)} item(s) have no price: {', '.join(r['name'] for r in nc)}.")
    pdl = q.get("price_deltas") or {}
    if pdl.get("rows"):
        below = [x["name"] for x in pdl["rows"] if x.get("position") == "below"]
        above = [x["name"] for x in pdl["rows"] if x.get("position") == "above"]
        L.append(f"- Against the planning ranges, the seen prices come to {inr(abs(pdl.get('total_one_off_inr') or 0))} "
                 f"{'more' if (pdl.get('total_one_off_inr') or 0) >= 0 else 'less'} for a one-off"
                 + (f"; seen above the range: {', '.join(above)}" if above else "") + (f"; seen below it: {', '.join(below)}" if below else "") + ".")
    inb = q.get("inbound_shipping") or {}
    if inb.get("vendor_count"):
        cons = (inb.get("consolidation") or {}).get("summary")
        L.append(f"- Parts come from {inb['vendor_count']} vendor(s); inbound shipping to the factory is about {inr(inb.get('one_off_inr'))} for a "
                 "one-off (assumed fees, not checked per vendor)" + (f". Vendor consolidation: {cons}" if cons else "") + ".")
    dp = q.get("delivered_price_inr")
    L.append("- The delivered price (printing, assembly, test, courier, reserves, fees and GST) is in `order/quote.json`"
             + (f": {inr(dp['pilot_one_off'])} one-off, {inr(dp['steady_state'])} at steady state ({q['status_label'].lower()})."
                if isinstance(dp, dict) else (f": {q.get('status_label', 'not quoted')}." if q else " (not quoted in this version).")))

    L += ["", "## Price evidence tiers", ""] + [f"- **{k}**: {v.split(': ', 1)[1]}" for k, v in TIER_TEXT.items()]

    L += ["", "## Parts list", ""]
    L.append(table(["Item", "Qty", "Pack", "Chosen source", "Price evidence", "Soldering", "CAD part"],
                   [[r["name"], r.get("quantity"), r.get("packSize") if r.get("packSize") is not None else "set (size not recorded)",
                     f"{r.get('vendor') or 'none'}" + (f" {r['sku']}" if r.get("sku") else ""),
                     (f"{inr(r.get('price_inr_incl_gst') or r['price_inr_seen'])} incl. GST, read {str(r.get('seen_on'))[:10]}" if r.get("evidence_tier") == "seen" else
                      (f"estimate {inr(r['price_inr_planning'][0])}-{inr(r['price_inr_planning'][1])}" if r.get("price_inr_planning") else
                       "not confirmed")),
                     solder_word(r), r.get("modelPart") or ""] for r in rows]))

    L += ["", "## Per-part evidence", ""]
    for r in rows:
        m = _module(lib, r.get("module_id")) if r.get("module_id") else {}
        L.append(f"### {r['name']}")
        L.append("")
        meta = []
        if r.get("module_id"):
            meta.append(f"library `{r['module_id']}`")
        if r.get("catalog_row"):
            meta.append(f"blueprint catalog row {r['catalog_row']}")
        if r.get("refs"):
            meta.append("refs " + ", ".join(r["refs"]))
        if r.get("use"):
            meta.append(f"used for {r['use']}")
        L.append(f"- Quantity {r.get('quantity')}" + (f" ({r.get('packsToBuy')} pack(s) of {r['packSize']})" if (r.get("packSize") or 1) > 1 else "")
                 + (f"; {'; '.join(meta)}." if meta else "."))
        if r.get("url"):
            L.append(f"- Chosen source: [{r.get('vendor') or r['url']}{(' ' + r['sku']) if r.get('sku') else ''}]({r['url']}).")
        else:
            L.append("- Chosen source: none recorded in the library (a sourcing gap, not a substitute).")
        tier = r.get("evidence_tier")
        plan = (f"planning range {inr(r['price_inr_planning'][0])}-{inr(r['price_inr_planning'][1])}"
                + (f" ({r['price_basis']})" if r.get("price_basis") and not str(r["price_basis"]).startswith("library") else "")
                if r.get("price_inr_planning") else "no planning range")
        if tier == "seen":
            lst = r.get("listing") or {}
            L.append(f"- Price: **seen** {inr(r['price_inr_seen'])} at {r.get('vendor')} ({r.get('gst_basis')} GST as listed; "
                     f"{inr(r.get('price_inr_incl_gst'))} incl. GST), re-read {r['seen_on']} by {lst.get('read_method') or 'the listing'}; "
                     f"raw listing `{lst.get('snapshot')}` (sha256 {str(lst.get('snapshot_sha256'))[:12]}...), signed receipt in build.json. "
                     f"A retail snapshot, not reserved stock; {plan}.")
        elif tier == "estimate":
            L.append(f"- Price: **estimate**, {plan}. Confirm before ordering.")
        else:
            L.append("- Price: **not confirmed**: no price is recorded, and this item is missing from the quote.")
        lst = r.get("listing") or {}
        if lst.get("notes"):
            L.append(f"- Source note: {lst['notes']}")
        alts = r.get("alternatives") or []
        if alts:
            L.append("- Alternatives compared: " + "; ".join(
                f"{a.get('vendor') or a.get('url') or 'unnamed'}" + (f" {inr(a['price_inr'])}" if a.get("price_inr") else "")
                + (f" ({a['seen_on']})" if a.get("seen_on") else "") for a in alts) + ".")
        elif r.get("kind") == "module":
            L.append("- Alternatives compared: none recorded (only one source in the library).")
        ev = _evidence_text(m)
        if ev:
            L.append(f"- Dimensional and pinout evidence: {ev}")
        un = _uncertain_text(m)
        if un:
            L.append(f"- Not verified: {un}")
        who = {"factory": "done at the factory", "pcb_fab": "done at PCB assembly", "maker": "by the maker"}.get(r.get("solderedBy"))
        L.append(f"- Soldering: {'not stated by the library' if r.get('requiresSoldering') is None else ('required' if r.get('requiresSoldering') or r.get('solderedBy') else 'none')}"
                 + (f", {who}" if who else "") + f" ({r.get('requiresSoldering_basis', 'library')}).")
        if r.get("modelPart"):
            L.append(f"- CAD: `{r['modelPart']}`" + (f" and {', '.join('`' + x + '`' for x in r.get('modelParts', [])[1:])}" if len(r.get("modelParts", [])) > 1 else "")
                     + ", a simplified envelope from the published dimensions, not factory CAD.")
        L.append("")

    fw = b.build.get("factory_work") or []
    L += ["## Done before delivery", ""]
    if fw:
        L.append("We deliver built devices. These joints are made at PCB assembly or at the factory, not by the maker:")
        L.append("")
        L.append(table(["Part", "Refs", "Kind", "Done by", "What"], [[w.get("name"), ", ".join(w.get("refs") or []), (w.get("kind") or "").replace("_", " "),
                                                                    w.get("by"), w.get("what")] for w in fw]))
    else:
        L.append("No part needs soldering or pre-wiring.")
    maker = [r["name"] for r in rows if r.get("solderedBy") == "maker"]
    if maker:
        L += ["", f"Soldering the maker would have to do (QB-28 fails until these are pre-wired or replaced): {', '.join(maker)}."]
    L.append("")

    pr = b.build.get("printed") or []
    if pr:
        L += ["## Printed parts", ""]
        L.append(table(["Part", "File", "Material", "Grams", "Hours", "Supports", "Orientation"],
                       [[p["name"], p.get("file"), f"{p.get('material')} {p.get('color_name') or ''} {p.get('color')}".strip(),
                         p.get("grams"), p.get("hours"), "yes" if p.get("supports") else "no", p.get("orientation_note")] for p in pr]))
        sl = b.slice or {}
        L += ["", f"Grams and hours are PrusaSlicer estimates ({sl.get('slicer', 'PrusaSlicer')}, {sl.get('profile', '')}) from "
                  "`print/slice-stats.json`; the slicer's own summary for each part is in `print/slice/`."]
    L.append("")
    return "\n".join(L)
