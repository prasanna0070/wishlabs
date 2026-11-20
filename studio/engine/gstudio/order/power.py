"""+5V supply basis (MAJ-5) and +5V voltage-drop budget (MIN-2). Pure functions, standard library only.

    supply_basis(power, limits, adapter=None) -> dict
        The +5V current limit is min(PTC fuse budget, supply rating). Also rates the design on a default USB port.
    drop_budget(power, backplane, get_module, instances, adapter=None, assumptions=None) -> dict
        Vrail_min = Vsrc_min - Ipeak x (Rcable + Rcontacts + Rptc + Rtrace), compared with the highest module minimum
        supply voltage on +5V. Every term carries its basis; anything that is not from a datasheet says so.
    adapter_policy(spec, power) -> (in_box: bool, reason: str)

foundation may call the first two from electrical.py and store them under electrical.json `power.supply` and
`power.drop_budget`; the checks stage computes the same dicts when electrical.json does not carry them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

DATA = Path(__file__).resolve().parent / "data" / "power-assumptions.json"

# USB default-power sources a buyer might plug in instead of the adapter (USB 2.0: 500 mA, USB 3.x: 900 mA).
DEFAULT_USB_SOURCES = (
    {"source": "USB 2.0 computer port (default power)", "ma": 500,
     "basis": "USB 2.0 default port current; backplane-v1 power.limits note: 'a computer port may give only 500-900 mA'"},
    {"source": "USB 3.x computer port (default power)", "ma": 900,
     "basis": "USB 3.x default port current; backplane-v1 power.limits note: 'a computer port may give only 500-900 mA'"},
)


def load_assumptions(path: Path = DATA) -> dict:
    return json.loads(Path(path).read_text())


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def capped_peak(power: dict) -> Optional[float]:
    return _num(power.get("total_ma_max_capped", power.get("total_ma_max")))


def uncapped_peak(power: dict) -> Optional[float]:
    for k in ("total_ma_max_uncapped", "uncapped_ma_max"):
        if _num(power.get(k)) is not None:
            return _num(power[k])
    if "total_ma_max_capped" in power:
        return _num(power.get("total_ma_max"))
    return None


def adapter_rating_ma(adapter: Optional[dict]) -> Optional[float]:
    """Rated output current of an adapter library row (electrical.current_ma_max, else '3 A' / '3000 mA' in its name)."""
    if not adapter:
        return None
    el = adapter.get("electrical") if isinstance(adapter.get("electrical"), dict) else {}
    v = _num(el.get("current_ma_max"))
    if v:
        return v
    m = re.search(r"(\d+(?:\.\d+)?)\s*A\b", str(adapter.get("name") or ""))
    return float(m.group(1)) * 1000 if m else None


def adapter_volts(adapter: Optional[dict]) -> Optional[float]:
    if not adapter:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*V\b", str(adapter.get("name") or ""))
    return float(m.group(1)) if m else None


def supply_basis(power: dict, limits: dict, adapter: Optional[dict] = None) -> dict:
    """{limit_ma, basis, fuse_budget_ma, supply_rating_ma, supply, sources[]} for the +5V rail."""
    fuse = _num(limits.get("limit_5v_ma")) or _num(power.get("limit_ma"))
    fuse_why = str(limits.get("limit_5v_why") or "PTC fuse budget")
    rating = adapter_rating_ma(adapter)
    if rating:
        supply = f"{adapter.get('name')} (library {adapter.get('id')}, in the box)"
        rating_basis = f"library {adapter.get('id')} electrical.current_ma_max"
    else:
        rating = _num(limits.get("usb_source_ma"))
        supply = "a 5 V 3 A USB-C adapter the buyer supplies (not in the box)"
        rating_basis = f"backplane-v1 power.limits.usb_source_ma: {limits.get('usb_source_note') or 'approved adapter rating'}"
    terms = [v for v in (fuse, rating) if v]
    limit = min(terms) if terms else None
    cap, unc = capped_peak(power), uncapped_peak(power)
    sources = []
    if rating:
        sources.append({"source": supply, "ma": rating, "basis": rating_basis})
    sources += [dict(s) for s in DEFAULT_USB_SOURCES]
    for s in sources:
        s["capped_peak_ma"] = cap
        s["ok_capped"] = None if cap is None else cap <= min(s["ma"], fuse or s["ma"])
        s["ok_uncapped"] = None if unc is None else unc <= min(s["ma"], fuse or s["ma"])
    return {
        "limit_ma": limit,
        "basis": (f"min(PTC fuse budget {fuse:.0f} mA, supply rating {rating:.0f} mA)" if fuse and rating
                  else "PTC fuse budget only (no supply rating known)" if fuse else "supply rating only"),
        "fuse_budget_ma": fuse, "fuse_budget_why": fuse_why,
        "supply_rating_ma": rating, "supply_rating_basis": rating_basis, "supply": supply,
        "adapter_in_box": bool(adapter),
        "capped_peak_ma": cap, "uncapped_peak_ma": unc,
        "ok": None if cap is None or limit is None else cap <= limit,
        "ok_uncapped": None if unc is None or limit is None else unc <= limit,
        "sources": sources,
        "limits": ["Currents are calculated from module data and firmware caps, not measured."],
    }


_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)")


def min_supply_v(module: dict) -> tuple[Optional[float], str]:
    """Lowest supply voltage a module states (electrical.supply_v 'a-b'), with the text it came from."""
    el = module.get("electrical") if isinstance(module.get("electrical"), dict) else {}
    txt = str(el.get("supply_v") or "")
    m = _RANGE.search(txt)
    if m:
        return float(m.group(1)), txt
    return None, txt


def drop_budget(power: dict, backplane: dict, get_module: Callable[[str], Optional[dict]], instances: list,
                adapter: Optional[dict] = None, assumptions: Optional[dict] = None) -> dict:
    """+5V rail minimum under the capped peak. Fails (ok False) when it is below the highest module minimum."""
    a = assumptions or load_assumptions()
    peak_ma = capped_peak(power)
    unc_ma = uncapped_peak(power)
    if adapter:
        src = a["source_min_v"]["adapter"]
        cab = dict(a["cable_ohm"]["adapter_captive"])
        notes = str(((adapter.get("dims_mm") or {}).get("notes")) or "")
        mlen = re.search(r"(\d+(?:\.\d+)?)\s*m\b", notes)
        mawg = re.search(r"(\d{2})\s*AWG", notes)
        if mlen and mawg and mawg.group(1) in a["awg_ohm_per_m"]:
            length = float(mlen.group(1))
            per_m = float(a["awg_ohm_per_m"][mawg.group(1)])
            cab = {"value": round(2 * length * per_m, 4),
                   "basis": f"2 x {length:g} m of {mawg.group(1)} AWG copper ({per_m} ohm/m, {a['awg_basis']}); "
                            f"length and gauge from library {adapter.get('id')} dims_mm.notes"}
    else:
        src = a["source_min_v"]["unknown_usb_c"]
        cab = a["cable_ohm"]["usb_c_spec_max"]
    terms = {"cable": cab, "contacts": a["contacts_ohm"], "ptc": a["ptc_ohm"], "trace": a["trace_ohm"]}
    r_total = sum(float(t["value"]) for t in terms.values())
    rail_mods = []
    for i in instances or []:
        i = i if isinstance(i, dict) else getattr(i, "__dict__", {})
        mid = i.get("module")
        m = get_module(mid) if mid else None
        if not m:
            continue
        el = m.get("electrical") if isinstance(m.get("electrical"), dict) else {}
        if el.get("supply_rail") != "+5V":
            continue
        vmin, txt = min_supply_v(m)
        if vmin is not None:
            rail_mods.append({"ref": i.get("ref"), "module": mid, "min_v": vmin, "supply_v": txt})
    need = max(rail_mods, key=lambda r: r["min_v"]) if rail_mods else None
    vsrc = float(src["value"])
    out = {
        "rail": "+5V",
        "source_min_v": vsrc, "source_basis": src["basis"],
        "peak_ma": peak_ma, "peak_basis": "electrical.json power total_ma_max_capped (with firmware caps)",
        "resistances_ohm": {k: {"value": float(t["value"]), "basis": t["basis"]} for k, t in terms.items()},
        "r_total_ohm": round(r_total, 4),
        "modules_on_rail": rail_mods,
        "required_min_v": need["min_v"] if need else None,
        "required_by": f"{need['ref']} {need['module']} (supply_v '{need['supply_v']}')" if need else None,
    }
    if peak_ma is None:
        out.update({"drop_v": None, "rail_min_v": None, "ok": None, "detail": "no capped peak current in electrical.json"})
        return out
    drop = peak_ma / 1000.0 * r_total
    vmin = vsrc - drop
    out.update({"drop_v": round(drop, 3), "rail_min_v": round(vmin, 3),
                "margin_v": round(vmin - need["min_v"], 3) if need else None,
                "ok": (vmin >= need["min_v"]) if need else None})
    if unc_ma is not None:
        out["rail_min_v_uncapped"] = round(vsrc - unc_ma / 1000.0 * r_total, 3)
    rs = " + ".join("%.3f" % float(t["value"]) for t in terms.values())
    out["equation"] = (f"{vsrc:.2f} V - {peak_ma / 1000:.3f} A x ({rs}) ohm = {vmin:.3f} V"
                       + (f" vs {need['min_v']:.2f} V needed by {need['ref']}" if need else ""))
    out["limits"] = ["Resistances marked ASSUMPTION are not from a datasheet of the parts that will be fitted; "
                     "the rail voltage under load is a physical check still needed."]
    return out


def adapter_policy(spec: dict, power: Optional[dict] = None, limit_ma: Optional[float] = None) -> tuple[bool, str]:
    """MAJ-5: ship the adapter in the box for child-directed designs, or when the uncapped draw is over 80% of the limit."""
    if spec.get("kind") == "printed":  # catalog package A: a printed-only object has no power input at all
        return False, "printed-only design: no electronics, no power input"
    ps = ((spec.get("electronics") or {}).get("power") or {})
    if ps.get("adapter_in_box"):
        return True, "spec electronics.power.adapter_in_box is true"
    aud = spec.get("audience") or {}
    flags = set((spec.get("safety") or {}).get("flags") or [])
    if aud.get("child_directed") or "CHILD_DIRECTED" in flags:
        return True, "child-directed design: the approved 5 V 3 A adapter ships in the box (MAJ-5 rule)"
    unc = uncapped_peak(power or {})
    lim = limit_ma or _num((power or {}).get("limit_ma"))
    if unc is not None and lim and unc > 0.8 * lim:
        return True, f"uncapped draw {unc:.0f} mA is over 80% of the {lim:.0f} mA +5V limit (MAJ-5 rule)"
    return False, "not child-directed and the uncapped draw is within 80% of the limit: the buyer supplies 5 V 3 A"
