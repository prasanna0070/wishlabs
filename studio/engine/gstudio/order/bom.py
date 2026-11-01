"""build.json `purchased[]` and `printed[]` (QB-22 .. QB-28) from the normalized spec, the library and the CAD.

Sources, in order of authority:
  * what to buy: `spec.derived.instances` (modules), `spec.derived.harness` (cables), `spec.derived.hardware`;
  * names, sources, pack sizes, prices, soldering: the library module (`library/modules.json`);
  * the CAD join (`modelPart`): `cad/components.json` entries with a matching `module_id` / `ref`.

Price evidence tiers (never blurred, ARCHITECTURE §0 / contract §7):
  seen          the library records a price at a named vendor with a date (an unsigned retail snapshot);
  estimate      only a planning range (library `price_inr_planning` or the blueprint catalog row);
  not_confirmed no price at all. The quote then lists the item as missing instead of inventing one.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

DATA = Path(__file__).resolve().parent / "data"

# ------------------------------------------------------------------ tolerant accessors


def as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for m in ("to_json_dict", "model_dump"):
        f = getattr(obj, m, None)
        if callable(f):
            try:
                return f(mode="json", by_alias=True) if m == "model_dump" else f()
            except TypeError:
                return f()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def first(d: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def module(lib: Any, mid: Optional[str]) -> dict:
    if not mid or lib is None:
        return {}
    try:
        return as_dict(lib.get(mid))
    except Exception:  # noqa: BLE001 - unknown id: caller reports it
        return {}


def camel(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", str(s)) if p)


def planning_range(m: dict) -> Optional[list[float]]:
    v = first(m, "price_inr_planning", "planning_price_inr")
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return [float(v), float(v)]
    if isinstance(v, dict):
        lo, hi = first(v, "low", "lo", "min"), first(v, "high", "hi", "max")
        return [float(lo), float(hi)] if lo is not None and hi is not None else None
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return [float(v[0]), float(v[1])]
    return None


@lru_cache(maxsize=1)
def sim_catalog() -> dict:
    return json.loads((DATA / "sim-catalog.json").read_text())["items"]


def catalog_range(lib: Any, mid: Optional[str], catalog_row: Optional[str]) -> tuple[Optional[list[float]], Optional[str], Optional[str]]:
    """Blueprint §3.3 planning range via Library.catalog_id_for_price(mid), else the catalog row number."""
    cat = sim_catalog()
    cid = None
    if mid and lib is not None and hasattr(lib, "catalog_id_for_price"):
        try:
            cid = lib.catalog_id_for_price(mid)
        except Exception:  # noqa: BLE001
            cid = None
    if not cid and mid and lib is not None:
        try:
            cid = as_dict(lib.get(mid)).get("catalog_id")
        except Exception:  # noqa: BLE001
            cid = None
    if not cid and catalog_row:
        cid = {v.get("row"): k for k, v in cat.items()}.get(str(catalog_row).zfill(2))
    if cid and cid in cat:
        it = cat[cid]
        return [float(it["low"]), float(it["high"])], f"blueprint §3.3 row {it.get('row')} ({cid}: {it.get('name')})", cid
    return None, None, None


IN_BOX_ROLES = {"usb_cable", "adapter"}
NO_SOLDER_ROLES = {"cable", "harness", "usb_cable", "fastener", "screws", "inserts", "feet", "ties", "mechanical", "consumable",
                   "label", "backplane", "cartridge", "gasket", "diffuser", "magnet", "panel", "servo_bracket", "servo_horn", "adapter"}
SOCKET_ROLES = {"controller", "core", "amp", "storage"}
# rows the blueprint catalog prices per set or kit ("M2/M2.5 screw set", "Heat-set inserts", "Adhesive feet / pads", ...)
SET_ROLES = {"fastener", "screws", "inserts", "feet", "ties", "consumable", "gasket", "magnet", "label"}
OPEN_END = re.compile(r"open (other )?end|bare (wire|end)|pigtail|unterminated|tinned end", re.I)
PLUG_CONNECTORS = ("jst", "ph", "xh", "grove", "qwiic", "stemma", "none", "socket", "usb")


def requires_soldering(m: dict, src: dict | None = None) -> tuple[Optional[bool], str]:
    """(True | False | None, basis). None means the library does not say (MIN-4): it is never coerced to False."""
    for k in ("requires_soldering", "requiresSoldering", "soldering_required"):
        if isinstance(m.get(k), bool):
            return m[k], "library" + (f": {m['requires_soldering_basis']}" if m.get("requires_soldering_basis") else "")
    mount = m.get("mount") if isinstance(m.get("mount"), dict) else {}
    for k in ("requires_soldering", "soldering"):
        if isinstance(mount.get(k), bool):
            return mount[k], "library mount"
    pw = m.get("prewire") if isinstance(m.get("prewire"), dict) else {}
    if str(pw.get("by") or "").lower() in ("factory", "supplier", "vendor", "pcb_fab", "pcba"):
        return True, f"library prewire: {str(pw.get('note') or pw.get('lead') or 'joints made before delivery')[:160]}"
    role = str(m.get("role") or "").lower()
    mc = m.get("module_connector") if isinstance(m.get("module_connector"), dict) else {}
    conn = str(mc.get("type") or "").lower()
    loc = str(mc.get("location") or "").lower()
    note = str(first(src or {}, "notes", default=""))
    if OPEN_END.search(note):
        return True, f"the chosen source lists an open other end ({note[:80]}): terminating it needs crimping or soldering"
    if role in NO_SOLDER_ROLES:
        return False, f"no electrical joint ({role})"
    if "spade" in conn or "spade" in loc:
        return False, "push-on 2.8 mm spade terminals: no soldering"
    if role in SOCKET_ROLES:
        return None, ("unverified: plugs into a backplane socket on its own pin headers, and the library does not say "
                      "whether the headers come fitted. The factory checks them on receipt")
    if conn and any(c in conn for c in PLUG_CONNECTORS):
        return False, f"plug-in connector ({conn})"
    if conn.startswith("header") or "2.54" in conn:
        return None, ("unverified: the library does not say whether this module's pin header comes fitted; "
                      "generic modules can ship with loose headers. The factory checks it on receipt")
    return None, "unverified: library has no soldering field"


def soldered_by(m: dict, solder: bool, basis: str) -> Optional[str]:
    """Who makes the joint: 'pcb_fab' (part mounted on our backplane PCBA), 'factory' (library says the factory solders
    it), else 'maker'. None when no soldering is needed."""
    pw = m.get("prewire") if isinstance(m.get("prewire"), dict) else {}
    by = str(pw.get("by") or "").lower()
    if by in ("factory", "supplier", "vendor"):
        return "factory"  # pre-wired before the unit reaches the maker (we deliver built devices)
    if by in ("pcb_fab", "pcba"):
        return "pcb_fab"
    if by == "maker":
        return "maker"
    if not solder:
        return None
    if made_to_drawing(m):
        return "factory"  # a harness the factory makes to drawing (crimped and continuity-tested before delivery)
    bp = m.get("backplane_v1") if isinstance(m.get("backplane_v1"), dict) else {}
    if bp.get("port") == "on_board":
        return "pcb_fab"
    if "factory" in str(m.get("requires_soldering_basis") or basis).lower():
        return "factory"
    return "maker"


def made_to_drawing(m: dict) -> bool:
    """A library cable the factory makes to drawing (no stocked part matches it)."""
    if not isinstance(m, dict):
        return False
    if m.get("made_to_drawing") or m.get("make_to_drawing") or isinstance(m.get("factory_make"), dict) or m.get("factory_make"):
        return True
    me = m.get("module_end") if isinstance(m.get("module_end"), dict) else {}
    return bool(me.get("made_to_drawing"))


# module-end families (BLK-2): the module connector type a harness must terminate in
FAMILY_BY_TYPE = {"jst_ph_2.0": "jst_ph", "jst_ph": "jst_ph", "header_2.54": "dupont_2.54", "dupont_2.54": "dupont_2.54",
                  "spade_2.8": "spade_2.8"}


def cable_family_for(mc: Any) -> Optional[str]:
    """Module-end family a module's connector needs. Uses gstudio.library.cable_family_for when foundation provides it;
    otherwise the same mapping, reading the location text for microswitch spades."""
    t = mc.get("type") if isinstance(mc, dict) else mc
    try:
        from gstudio import library as _library

        fn = getattr(_library, "cable_family_for", None)
        if callable(fn):
            got = fn(t)
            if got:
                return str(got)
    except Exception:  # noqa: BLE001 - fall back to the local map
        pass
    t = str(t or "").lower()
    loc = str(mc.get("location") or "").lower() if isinstance(mc, dict) else ""
    if t.startswith("jst_xh"):
        return "jst_xh"
    if t in FAMILY_BY_TYPE:
        return FAMILY_BY_TYPE[t]
    if "spade" in t or "spade" in loc or "microswitch" in loc:
        return "spade_2.8"
    return None


NO_CAD_ROLES = {"storage_card"}  # fitted in the device but too small to model (BLK-5: the microSD card)


def cable_module_end(m: dict) -> dict:
    """{family, housings[], made_to_drawing, positions(flat module pins in order)} of a library cable row's module end.
    Old rows (no module_end) give family None: their module end is not stated, which the checks report."""
    me = m.get("module_end") if isinstance(m.get("module_end"), dict) else {}
    fam = me.get("family")
    housings = me.get("housings") if isinstance(me.get("housings"), list) else []
    flat = []
    for h in housings:
        for p in sorted((h.get("positions") or []) if isinstance(h, dict) else [], key=lambda p: (p.get("pos") or 0) if isinstance(p, dict) else 0):
            if isinstance(p, dict) and p.get("module_pin"):
                flat.append(str(p["module_pin"]))
    return {"family": fam, "housings": housings, "made_to_drawing": made_to_drawing(m), "positions": flat,
            "straight_through": me.get("straight_through", m.get("straight_through") if isinstance(m, dict) else None)}


def harness_by_ref(electrical: Optional[dict]) -> dict[str, dict]:
    out = {}
    for i in (electrical or {}).get("instances") or []:
        if isinstance(i, dict) and isinstance(i.get("harness"), dict):
            out[str(i.get("ref"))] = dict(i["harness"], _module=i.get("module"))
    return out


def port_order(h: dict) -> list[str]:
    """Module pins in backplane-port pin order (slot_pin 1, 2, ...) for one electrical harness."""
    ws = [w for w in h.get("wires") or [] if isinstance(w, dict) and w.get("module_pin")]
    return [str(w["module_pin"]) for w in sorted(ws, key=lambda w: (w.get("slot_pin") if isinstance(w.get("slot_pin"), int) else 99))]


def module_order(h: dict, mod: dict) -> list[str]:
    o = h.get("module_end_order")
    if isinstance(o, list) and o:
        return [str(x) for x in o]
    mc = mod.get("module_connector") if isinstance(mod.get("module_connector"), dict) else {}
    return [str(x) for x in mc.get("pin_order") or []]


def is_crossed(h: dict, mod: dict) -> bool:
    """True when the module's own pin order differs from the port order (a straight-through lead would swap pins)."""
    if isinstance(h.get("crossed"), bool):
        return h["crossed"]
    if isinstance(h.get("made_to_drawing"), bool) and h["made_to_drawing"]:
        return True
    po, mo = port_order(h), module_order(h, mod)
    wired = [p for p in mo if p in po]
    return bool(po and wired and wired != po)


def _lead_text(h: dict) -> Optional[str]:
    cab = str(h.get("cable") or "")
    ln = h.get("length_mm")
    if not ln:
        return None
    base = re.sub(r"\s*\d{2,4}\s*mm\s*$", "", cab).strip() or "JST-PH 2.0"
    return f"{base} {int(ln)} mm"


def factory_work(rows: list[dict], electrical: Optional[dict] = None, lib: Any = None) -> list[dict]:
    """Work done before the maker gets the parts (build.json `factory_work`): backplane PCBA, factory pre-wiring, harnesses
    made to drawing with their continuity tests (BLK-3, MAJ-4) and pin-header checks on receipt (MIN-4). Lead lengths come
    from electrical.json harness.length_mm (MIN-6)."""
    hb = harness_by_ref(electrical)
    out = []
    for r in rows:
        if r.get("solderedBy") not in ("factory", "pcb_fab") or r.get("kind") == "cable":
            continue
        pw = dict(r.get("prewire") or {})
        refs = r.get("refs") or []
        h = next((hb[x] for x in refs if x in hb), None)
        if h and pw.get("lead"):
            lead = _lead_text(h)
            if lead:
                pw["lead"] = lead
                pw["lead_basis"] = "electrical.json harness.length_mm"
        lead = pw.get("lead") or pw.get("leads")
        what = pw.get("what") or pw.get("note") or pw.get("notes") or pw.get("detail") or (f"pre-wired lead: {lead}" if lead else None) or (
            "soldered onto the backplane PCB at PCB assembly" if r["solderedBy"] == "pcb_fab" else r.get("requiresSoldering_basis"))
        what = what if isinstance(what, str) else str(what)
        if h and pw.get("lead"):
            what = re.sub(r"length = the design's harness length class, \d+ mm default", f"length {h.get('length_mm')} mm for this design", what)
        out.append({"name": r["name"], "module_id": r.get("module_id"), "refs": refs, "kind": "prewire" if pw.get("lead") else "solder",
                    "by": "PCB fab (backplane PCBA)" if r["solderedBy"] == "pcb_fab" else "factory",
                    "what": what, "prewire": pw or None})
    # harnesses made to drawing, and a continuity test for every crossed one
    for r in rows:
        if r.get("kind") != "cable":
            continue
        refs = r.get("refs") or []
        m = module(lib, r.get("module_id")) if r.get("module_id") else {}
        me = r.get("module_end") or cable_module_end(m)
        for ref in refs:
            h = hb.get(ref)
            if not h:
                continue
            mod = module(lib, h.get("_module")) if h.get("_module") else {}
            if (mod.get("prewire") or {}).get("lead") or h.get("factory_prewired"):
                continue  # the factory solders a stock lead to this module: its prewire entry covers it
            crossed = is_crossed(h, mod)
            to_drawing = bool(r.get("made_to_drawing")) or not r.get("vendor") or crossed
            if not to_drawing:
                continue
            fam = me.get("family") or "module end not stated in the library"
            pmap = [{"port_pin": w.get("slot_pin"), "port_signal": w.get("slot_pin_name"), "module_pin": w.get("module_pin"),
                     "net": w.get("net"), "colour": w.get("color")} for w in sorted(h.get("wires") or [], key=lambda w: w.get("slot_pin") or 0)
                    if isinstance(w, dict)]
            mo = module_order(h, mod)
            out.append({"name": r["name"], "module_id": r.get("module_id"), "refs": [ref], "kind": "harness_to_drawing", "by": "factory",
                        "what": (f"Make the {ref} harness to drawing: {h.get('cable') or 'JST-PH'} port end, {fam} module end"
                                 + (f" in the module's own order {', '.join(mo)}" if mo else "")
                                 + (" (crossed: NOT a straight-through lead)" if crossed else "")
                                 + f", {h.get('length_mm')} mm. Label it with {ref}."),
                        "drawing": {"id": r.get("drawing") or (r.get("factory_make") or {}).get("drawing"), "sku": r.get("sku"),
                                    "port_end": h.get("cable"), "module_end_family": me.get("family"),
                                    "module_end_housings": h.get("module_end_housings") or me.get("housings") or [],
                                    "module_end_order": mo, "length_mm": h.get("length_mm"), "pin_map": pmap},
                        "steps": (r.get("factory_make") or {}).get("steps"), "test": (r.get("factory_make") or {}).get("test"),
                        "crossed": crossed})
            if crossed:
                out.append({"name": r["name"], "module_id": r.get("module_id"), "refs": [ref], "kind": "continuity_test", "by": "factory",
                            "what": (f"Continuity-test the {ref} harness end to end before it ships: every port pin reaches the module pin "
                                     "named below and no other. " + "; ".join(f"port {p['port_pin']} ({p['port_signal']}) -> {p['module_pin']}"
                                                                           for p in pmap)),
                            "pin_map": pmap, "crossed": True})
    # factory-made non-cable rows (e.g. the per-device ID label): made to their drawing before delivery
    for r in rows:
        fm = r.get("factory_make") if isinstance(r.get("factory_make"), dict) else None
        if r.get("kind") == "cable" or not fm or r.get("included_in") or str(r.get("role") or "") == "backplane":
            continue
        out.append({"name": r["name"], "module_id": r.get("module_id"), "refs": r.get("refs") or [], "kind": "make_to_drawing",
                    "by": fm.get("by") or "factory", "what": fm.get("what") or f"Made to drawing {fm.get('drawing')} before delivery.",
                    "drawing": {"id": fm.get("drawing"), "sku": r.get("sku")}, "steps": fm.get("steps"), "test": fm.get("test")})
    # MIN-4: pin headers the library does not say are fitted are checked (and soldered if loose) by the factory
    for r in rows:
        if r.get("requiresSoldering") is None and r.get("kind") == "module":
            out.append({"name": r["name"], "module_id": r.get("module_id"), "refs": r.get("refs") or [], "kind": "header_fit", "by": "factory",
                        "what": ("Check the pin header is fitted on receipt (the library does not say whether it ships fitted); if it is loose "
                                 "the factory solders it before assembly. The maker never solders.")})
    return out


def pack_known(src: dict, m: dict) -> bool:
    """True when a pack size is recorded or written in the name/notes (else pack_size() returns 1 by default)."""
    if first(src, "pack", "pack_qty", "pack_size", "packQuantity", "pack_quantity") or first(m, "pack_size", "pack_qty", "pack"):
        return True
    texts = [str(first(src, "notes", default="")), str(m.get("name") or "")]
    rp = m.get("reference_product")
    if isinstance(rp, dict):
        texts.append(str(rp.get("mpn_or_model") or ""))
    return any(PACK_RE.search(t) for t in texts)


PACK_RE = re.compile(r"(?:pack|set|bag|lot)\s+of\s+(\d{1,4})|\((\d{1,4})\s*(?:pcs|pieces|nos)\b|(\d{1,4})\s*(?:pcs|pieces)\s+(?:pack|set)", re.I)


def pack_size(src: dict, m: dict) -> int:
    v = first(src, "pack", "pack_qty", "pack_size", "packQuantity", "pack_quantity") or first(m, "pack_size", "pack_qty", "pack")
    if v is None:
        texts = [str(first(src, "notes", default="")), str(m.get("name") or "")]
        rp = m.get("reference_product")
        if isinstance(rp, dict):
            texts.append(str(rp.get("mpn_or_model") or ""))
        for t in texts:
            mm = PACK_RE.search(t)
            if mm:
                v = next(g for g in mm.groups() if g)
                break
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return 1


def price_seen(src: dict) -> Optional[float]:
    v = first(src, "price_inr", "price_inr_seen", "price_seen_inr", "inr")
    if v is None and str(first(src, "currency", default="INR")).upper() == "INR":
        v = first(src, "price")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def seen_on(src: dict) -> Optional[str]:
    return first(src, "checked_at", "seen_on", "checkedAt", "date", "observed_on", "retrieved")


def page_read(src: dict) -> bool:
    """A price read on the vendor's own page (library status `page_read`), not just a search-result snippet."""
    st = str(first(src, "status", default="")).lower()
    return st not in ("search_snippet", "snippet", "unverified")


def out_of_stock(src: dict) -> bool:
    t = f"{first(src, 'stock', default='')} {first(src, 'notes', default='')}".lower()
    return "out of stock" in t or "out_of_stock" in t or "sold out" in t


def choose_source(m: dict, prefer_vendor: Optional[str] = None) -> tuple[dict, list[dict]]:
    """(chosen, alternatives) from `sourcing_in[]`. Keeps the library's order (its preference) except that an
    explicitly selected row wins, and rows without a URL, out of stock, or with only a search-snippet price drop back.
    `prefer_vendor` (vendor consolidation, MIN-26) wins over the library order when that vendor's row is a usable page price."""
    rows = [as_dict(s) for s in (m.get("sourcing_in") or []) if s]
    if not rows:
        return {}, []

    def score(s: dict) -> tuple:
        sel = bool(first(s, "selected", "preferred", "primary", default=False))
        pref = bool(prefer_vendor) and first(s, "vendor", "seller") == prefer_vendor and eligible_alternative(s)
        return (not pref, not sel, s.get("same_part") is False, not bool(first(s, "url")), out_of_stock(s), not page_read(s),
                price_seen(s) is None)
    ranked = sorted(rows, key=score)
    return ranked[0], ranked[1:]


def eligible_alternative(s: dict) -> bool:
    """A source that could replace the chosen one: the same part (never a clone: `same_part` False), a host that matches
    the vendor (`host_ok` not False), a page-read price, a URL, not out of stock."""
    if s.get("same_part") is False or s.get("host_ok") is False:
        return False
    return bool(first(s, "url")) and page_read(s) and not out_of_stock(s) and price_seen(s) is not None \
        and str(first(s, "status", default="")).lower() not in ("search_snippet", "snippet", "unverified")


SOURCE_FIELDS = ("stock", "stock_qty", "checked_at", "checked_at_precision", "snapshot", "sku_kind", "fulfilment_country",
                 "import_risk", "selection_reason", "retail", "host_ok", "same_part")


GST_RATE = 0.18


def gst_basis(src: dict) -> str:
    """'incl' | 'excl' | 'unknown' from a library source row (`gst`, else the words in its notes)."""
    g = str(first(src, "gst", "gst_basis", default="") or "").lower()
    if g in ("incl", "included", "inclusive", "incl_gst"):
        return "incl"
    if g in ("excl", "excluded", "exclusive", "excl_gst", "ex"):
        return "excl"
    note = str(first(src, "notes", default="")).lower()
    if re.search(r"\bincl\.?\s*(?:18%\s*)?gst\b|inclusive of gst|including gst", note):
        return "incl"
    if re.search(r"\bexcl\.?\s*(?:18%\s*)?gst\b|exclusive of gst|\+\s*gst|ex-?gst", note):
        return "excl"
    return "unknown"


def incl_gst(price: Optional[float], basis: str) -> Optional[float]:
    """GST-inclusive price (MAJ-23). Unknown basis is treated as ex-GST (+18%), the higher of the two readings."""
    if price is None:
        return None
    return round(float(price) if basis == "incl" else float(price) * (1 + GST_RATE), 2)


# ------------------------------------------------------------------ CAD join


HW_WORDS = {"gasket": ("gasket",), "screws": ("screw",), "fastener": ("screw", "nut"), "inserts": ("insert",),
            "feet": ("feet", "foot"), "ties": ("tie",), "label": ("label",), "magnet": ("magnet",)}
ALL_HW_WORDS = tuple(sorted({w for ws in HW_WORDS.values() for w in ws}))


def _is_hw_name(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in ALL_HW_WORDS)


def component_names(components: dict, *, module_id: str | None = None, refs: Iterable[str] = (),
                    hint: str | None = None, kind: str = "PURCHASE", role: str | None = None) -> list[str]:
    """CAD component names for one purchased row. Match by module_id; a component without a module_id joins by ref
    unless its name says it is hardware (a gasket ring carries the speaker's ref but is not the speaker); hardware rows
    also take module-less components whose name matches their role (PURCHASE_SpeakerGasket -> gasket strip)."""
    refs = set(refs or ())
    out = []
    words = HW_WORDS.get(str(role or "").lower(), ())
    for name, c in (components or {}).items():
        c = as_dict(c)
        k = str(first(c, "kind", default=name.split("_", 1)[0])).upper()
        if k != kind:
            continue
        cm = first(c, "module_id", "module")
        if module_id and cm == module_id:
            out.append(name)
        elif not cm and refs and first(c, "ref") in refs and not _is_hw_name(name):
            out.append(name)
        elif not cm and words and any(w in name.lower() for w in words):
            out.append(name)
    if not out and hint:
        want = f"{kind}_{camel(hint)}".lower()
        out = [n for n in (components or {}) if n.lower() == want]
    return sorted(out)


# roles whose CAD piece count is the number to buy; strips, kits and labels are cut or shared, so their count stays
COUNTED_ROLES = {"screws", "inserts", "feet", "fastener", "magnet"}


def cad_group_qty(c: dict) -> tuple[Optional[int], str]:
    """Items in one CAD fastener group: its `qty`, else the count in its own label ("... x17"), else its solid count."""
    q = c.get("qty")
    if isinstance(q, (int, float)) and q > 0:
        return int(q), "qty"
    m = re.search(r"[x\u00d7]\s*(\d{1,4})\s*$", str(c.get("label") or "").strip())
    sol = c.get("solids") if isinstance(c.get("solids"), int) and c.get("solids") > 0 else None
    if m and (sol is None or int(m.group(1)) == sol):  # "x8" could be a screw length: the solid count must agree
        return int(m.group(1)), "label"
    if sol is not None:
        return sol, "solids"
    return None, "none"


# ------------------------------------------------------------------ purchased[]


def _length_mm(m: dict) -> Optional[float]:
    for t in (str(m.get("id") or ""), str(m.get("name") or "")):
        mm = re.search(r"(\d{2,4})\s*MM\b", t, re.I)
        if mm:
            return float(mm.group(1))
    return None


def cable_module(lib: Any, h: dict) -> tuple[Optional[str], str]:
    """Adapter until normalize assigns cable modules: the library harness cable with the same pin count and a
    length at least the required class (shortest such). Returns (module id or None, how)."""
    pins, need = h.get("pins"), float(h.get("length_mm") or 0)
    if not pins or lib is None:
        return None, "none"
    try:
        mods = list(lib.by_role(["harness", "cable"]))
    except Exception:  # noqa: BLE001
        mods = [as_dict(x) for x in getattr(lib, "modules", [])]
    best = None
    for m in mods:
        m = as_dict(m)
        txt = f"{m.get('id', '')} {m.get('name', '')}"
        if not re.search(rf"(?<!\d){pins}\s*(?:P\b|-pin|pin\b|p\b)", txt, re.I) or "usb" in txt.lower():
            continue
        ln = _length_mm(m)
        if ln is None or ln < need:
            continue
        if best is None or ln < best[1]:
            best = (m.get("id"), ln)
    return (best[0], "by_pins") if best else (None, "none")


def _row(name: str, qty: int, m: dict, *, module_id: str | None, catalog_row: str | None, refs: list[str],
         model_parts: list[str], kind: str, use: str = "", extra_notes: list[str] | None = None, lib: Any = None,
         prefer_vendor: Optional[str] = None) -> dict:
    src, alts = choose_source(m, prefer_vendor)
    pack = pack_size(src, m)
    seen = price_seen(src)
    when = seen_on(src)
    gb = gst_basis(src) if src else "unknown"
    rng = planning_range(m)
    basis, cat_id = (f"library {module_id} price_inr_planning" if rng else None), None
    if rng is None:
        rng, basis, cat_id = catalog_range(lib, module_id, catalog_row or first(m, "catalog_row"))
    if rng is None and seen is not None:  # no planning range anywhere: the dated price (GST-normalised) is the estimate basis
        p = incl_gst(seen, gb)
        rng, basis = [round(p, 2), round(p * 1.15, 2)], f"estimate from the library's dated price {seen:g} INR ({gb} GST) + 15%"
    if seen is not None and when and page_read(src):
        tier, estimate = "seen", False
    elif rng is not None or seen is not None:
        tier, estimate = "estimate", True
    else:
        tier, estimate = "not_confirmed", True
    notes = list(extra_notes or [])
    if tier == "seen":
        notes.append(f"Price seen at {first(src, 'vendor', 'seller', default='the listed vendor')} on {when}: a retail snapshot, "
                     "not reserved stock.")
    elif tier == "estimate":
        if seen is not None:
            notes.append(f"{first(src, 'vendor', default='A vendor')} price {seen:g} INR was only seen in a search result on {when}, "
                         "not on the vendor page.")
        notes.append("Price estimate; confirm before ordering.")
    else:
        notes.append("Price not confirmed.")
    role = str(m.get("role") or "").lower()
    per_set = (kind == "hardware" or role in SET_ROLES) and not pack_known(src, m) and qty > 1
    if pack > 1:
        notes.append(f"Sold in packs of {pack}; this build uses {qty}.")
    elif per_set:
        notes.append(f"Pack size not recorded; priced as one set per build for the {qty} pieces used (the blueprint catalog "
                     "prices this kind of row per set). Confirm the pack holds enough.")
    sold, sold_basis = requires_soldering(m, src)
    if first(src, "notes"):
        notes.append(f"Source note: {first(src, 'notes')}")
    if seen is not None and gb == "unknown":
        notes.append("GST basis of this price is not stated: it is treated as ex-GST (+18%) for the totals.")
    row = {
        "name": name,
        "quantity": int(qty),
        "packSize": None if per_set else pack,
        "packsToBuy": 1 if per_set else (int(math.ceil(qty / pack)) if pack else int(qty)),
        "pack_basis": ("not recorded: one set per build assumed" if per_set else ("recorded" if pack_known(src, m) else "single item")),
        "kind": kind,
        "module_id": module_id,
        "catalog_row": catalog_row or first(m, "catalog_row"),
        "refs": refs,
        "use": use or None,
        "requiresSoldering": sold,
        "requiresSoldering_basis": sold_basis,
        "solderedBy": soldered_by(m, bool(sold), sold_basis),
        "prewire": m.get("prewire") if isinstance(m.get("prewire"), dict) else None,
        "url": first(src, "url"),
        "vendor": first(src, "vendor", "seller", "store"),
        "sku": first(src, "sku", "SKU", "product_code") or first(m, "sku"),
        "price_inr_planning": rng,
        "price_basis": basis,
        "price_catalog_id": cat_id,
        "price_inr_seen": seen if tier == "seen" else None,
        "gst_basis": gb if seen is not None else None,
        "price_inr_incl_gst": incl_gst(seen, gb) if tier == "seen" else None,
        "seen_on": when if tier == "seen" else None,
        "estimate": estimate,
        "evidence_tier": tier,
        "listing": ({"url": first(src, "url"), "vendor": first(src, "vendor", "seller"), "sku": first(src, "sku"),
                     "packQuantity": pack, "seen_on": when, "status": first(src, "status", default="seen"),
                     "stock": first(src, "stock"), "price_inr_seen": seen, "gst_basis": gb, "notes": first(src, "notes"),
                     "signed": False} if src else None),
        "alternatives": [{"vendor": first(a, "vendor", "seller"), "url": first(a, "url"), "sku": first(a, "sku"),
                          "price_inr": price_seen(a), "gst_basis": gst_basis(a), "price_inr_incl_gst": incl_gst(price_seen(a), gst_basis(a)),
                          "seen_on": seen_on(a), "status": first(a, "status"), "stock": a.get("stock"),
                          "same_part": a.get("same_part"), "host_ok": a.get("host_ok"),
                          "pack": pack_size(a, m), "notes": first(a, "notes")} for a in alts],
        "notes": " ".join(notes),
        "modelPart": model_parts[0] if model_parts else None,
    }
    if src:  # MAJ-24 / foundation: the chosen sourcing row's stock, check time, snapshot and selection reason travel with it
        for k in SOURCE_FIELDS:
            if src.get(k) is not None:
                row["listing"]["library_snapshot_path" if k == "snapshot" else k] = src[k]  # listing.snapshot = the build's re-read
        for k in ("stock", "checked_at", "sku_kind", "fulfilment_country", "import_risk", "selection_reason"):
            if src.get(k) is not None:
                row[k] = src[k]
    if kind != "cable" and isinstance(m.get("factory_make"), dict) and m["factory_make"].get("drawing"):
        # made by our factory to a drawing (e.g. the per-device ID label): no retail listing, the drawing id is the SKU
        row["factory_make"] = m["factory_make"]
        row["made_to_drawing"] = True
        row["sku"] = row.get("sku") or m.get("sku") or m["factory_make"]["drawing"]
        row["sku_kind"] = row.get("sku_kind") or "drawing"
    if kind == "cable":
        me = cable_module_end(m)
        row["module_end"] = {k: me[k] for k in ("family", "housings", "positions")}
        row["module_end"]["straight_through"] = m.get("straight_through", me.get("straight_through"))
        row["made_to_drawing"] = me["made_to_drawing"]
        for k in ("crossed", "drawing", "pinning", "length_mm"):
            if m.get(k) is not None:
                row[k] = m[k]
        if m.get("sku_kind") and not row.get("sku_kind"):
            row["sku_kind"] = m["sku_kind"]
        if isinstance(m.get("factory_make"), dict):
            row["factory_make"] = m["factory_make"]
    if len(model_parts) > 1:
        row["modelParts"] = model_parts
    return row


def primary_part(parts: list[str], components: dict, *, module_id: Optional[str] = None, refs: Iterable[str] = (),
                 groups: Iterable[str] = ()) -> list[str]:
    """MIN-8: order the CAD matches so the primary modelPart is chosen by role, never by sort order. Preference:
    the hardware group's own component (PURCHASE_<Group>), then a component carrying the row's module_id and one of its
    refs with a non-hardware name, then the rest alphabetically."""
    refs, groups = set(refs or ()), [g for g in groups or () if g]
    want = {f"PURCHASE_{camel(g)}".lower() for g in groups}

    def key(n: str) -> tuple:
        c = as_dict(components.get(n))
        return (n.lower() not in want,
                not (module_id and first(c, "module_id", "module") == module_id and (not refs or first(c, "ref") in refs)
                     and not _is_hw_name(n)),
                n)
    return sorted(set(parts), key=key)


def build_purchased(spec: dict, lib: Any, components: dict | None, electrical: dict | None = None,
                    consolidate: bool = True) -> tuple[list[dict], list[str]]:
    """Return (purchased rows, problems). `spec` is the normalized spec dict with `derived`. With `consolidate`, rows
    whose vendor serves only them move to a usable alternative at a vendor already in the list (MIN-26)."""
    rows, problems = _build_purchased(spec, lib, components, electrical, {})
    if not consolidate:
        return rows, problems
    prefer = consolidation_plan(rows)
    if not prefer:
        return rows, problems
    rows2, problems2 = _build_purchased(spec, lib, components, electrical, prefer)
    consolidation_plan(rows2)  # annotate the single-vendor rows that still cannot move (one round only)
    before = {r["name"]: r.get("vendor") for r in rows}
    for r in rows2:
        if r["name"] in prefer and before.get(r["name"]) != r.get("vendor"):
            r["consolidated_from"] = before.get(r["name"])
            r["notes"] = (r.get("notes") or "") + (f" Bought from {r.get('vendor')} instead of {before.get(r['name'])} so fewer vendors ship "
                                                   "to the factory (vendor consolidation).")
    return rows2, problems2


def vendor_counts(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        if r.get("vendor"):
            out[r["vendor"]] = out.get(r["vendor"], 0) + 1
    return out


def consolidation_plan(rows: list[dict], fee_inr: float = 80.0) -> dict[str, str]:
    """{row name: vendor} moves that drop a vendor used by one row only, when that row has a usable alternative at a
    vendor already in the list costing no more than the chosen price plus one inbound fee. Rows that cannot move get
    `consolidation_note` saying why."""
    counts = vendor_counts(rows)
    plan: dict[str, str] = {}
    for r in rows:
        v = r.get("vendor")
        if not v or counts.get(v, 0) != 1:
            continue
        cur = r.get("price_inr_incl_gst")
        best = None
        why = []
        for a in r.get("alternatives") or []:
            av = a.get("vendor")
            if not av or av == v:
                continue
            if counts.get(av, 0) < 1:
                why.append(f"{av} is not otherwise used")
                continue
            if a.get("same_part") is False or a.get("host_ok") is False:
                why.append(f"{av} sells a different part (same_part false) or its host does not match")
                continue
            if not a.get("url") or a.get("price_inr") is None or str(a.get("status") or "").lower() in ("search_snippet", "snippet", "unverified") \
                    or str(a.get("stock") or "").lower() == "out_of_stock" \
                    or "out of stock" in str(a.get("notes") or "").lower() or "sold out" in str(a.get("notes") or "").lower():
                why.append(f"{av} has no page-read, in-stock price")
                continue
            ap = a.get("price_inr_incl_gst")
            if cur is not None and ap is not None and ap > cur + fee_inr:
                why.append(f"{av} costs ₹{ap - cur:,.0f} more than one inbound fee")
                continue
            if best is None or (ap or 0) < (best[1] or 0):
                best = (av, ap)
        if best:
            plan[r["name"]] = best[0]
        else:
            r["consolidation_note"] = (f"{v} supplies only this item; no alternative can move it: " + "; ".join(why)) if why else \
                f"{v} supplies only this item and the library lists no alternative vendor"
    return plan


def _build_purchased(spec: dict, lib: Any, components: dict | None, electrical: dict | None,
                     prefer: dict[str, str]) -> tuple[list[dict], list[str]]:
    d = spec.get("derived") or {}
    comps = components or {}
    rows: list[dict] = []
    problems: list[str] = []
    hw_groups_of: dict[str, list[str]] = {}
    for hw in d.get("hardware") or []:
        hw = as_dict(hw)
        hw_groups_of.setdefault(hw.get("module") or hw.get("id") or hw.get("name"), []).append(hw.get("group"))

    # modules, grouped by library id (order: as the normalizer listed them)
    groups: dict[str, dict] = {}
    for inst in d.get("instances") or []:
        inst = as_dict(inst)
        mid = inst.get("module")
        if not mid:
            problems.append(f"instance {inst.get('ref')} has no module id")
            continue
        g = groups.setdefault(mid, {"qty": 0, "refs": [], "roles": set(), "names": [], "features": []})
        g["qty"] += int(inst.get("qty") or 1)
        g["refs"].append(inst.get("ref"))
        g["roles"].add(inst.get("role") or "")
        if inst.get("name"):
            g["names"].append(inst["name"])
        if inst.get("feature"):
            g["features"].append(inst["feature"])
    for mid, g in groups.items():
        m = module(lib, mid)
        if not m:
            problems.append(f"module {mid} is not in the library")
        name = str(first(m, "name", "title", default=(g["names"] or [mid])[0]))
        parts = component_names(comps, module_id=mid, refs=g["refs"], role=m.get("role"))
        on_board = isinstance(m.get("backplane_v1"), dict) and m["backplane_v1"].get("port") == "on_board"
        if not parts and on_board:  # soldered on the backplane PCBA: modelled as part of the backplane component
            parts = sorted(n for n, c in comps.items() if as_dict(c).get("ref") == "BP1"
                           and str(as_dict(c).get("kind") or n.split("_", 1)[0]).upper() == "PURCHASE") \
                or [n for n in comps if n == "PURCHASE_Backplane"]
        parts = primary_part(parts, comps, module_id=mid, refs=g["refs"])
        uses = sorted({f for f in g["features"]})
        rows.append(_row(name, g["qty"], m, module_id=mid, catalog_row=None, refs=[r for r in g["refs"] if r],
                         model_parts=parts, kind="module", use=", ".join(uses), lib=lib, prefer_vendor=prefer.get(name)))
        rows[-1]["role"] = m.get("role")
        if str(m.get("role") or "") in NO_CAD_ROLES:
            rows[-1]["no_cad_model"] = True
            rows[-1]["no_cad_model_basis"] = f"{m.get('role')}: fitted in the device but not modelled in the CAD"
        if on_board:
            rows[-1]["mounted_on"] = "backplane PCB"
        if str(m.get("role") or "").lower() in IN_BOX_ROLES:
            rows[-1]["in_box"] = True  # ships in the box, not inside the gadget: no CAD geometry expected

    # MAJ-5: the approved USB-C adapter ships in the box for child-directed designs (or a tight power budget)
    if not any(str(r.get("role") or "").lower() == "adapter" for r in rows):
        from .power import adapter_policy

        in_box, why = adapter_policy(spec, (electrical or {}).get("power"))
        psu = None
        if in_box and lib is not None:
            try:
                psu = next(iter(lib.by_role("adapter")), None)
            except Exception:  # noqa: BLE001
                psu = None
        if psu:
            m = as_dict(psu)
            name = str(first(m, "name", default=m.get("id")))
            rows.append(_row(name, 1, m, module_id=m.get("id"), catalog_row=None, refs=["PSU1"], model_parts=[], kind="module",
                             use="power supply in the box", lib=lib, prefer_vendor=prefer.get(name),
                             extra_notes=[f"In the box: {why}. Use only this 5 V 3 A USB-C adapter or an equivalent one. "
                                          "A mains adapter sold in India must carry BIS CRS registration (an R-number on its label); "
                                          "the chosen listing does not show one, so check the label before shipping."]))
            rows[-1].update({"role": "adapter", "in_box": True, "in_box_basis": why})
        elif in_box:
            problems.append(f"the adapter should ship in the box ({why}) but the library has no adapter row")

    # harness cables, grouped by (module or cable description)
    cables: dict[str, dict] = {}
    for h in d.get("harness") or []:
        h = as_dict(h)
        if str(h.get("kind", "jst_ph")) == "socket":
            continue  # plugs straight into a backplane socket; nothing to buy
        mid, how = h.get("module"), "normalize"
        if not mid:
            mid, how = cable_module(lib, h)
        key = mid or h.get("cable") or f"{h.get('kind')} {h.get('pins')}p {h.get('length_mm')} mm"
        g = cables.setdefault(key, {"qty": 0, "refs": [], "h": h, "mid": mid, "how": how, "lengths": []})
        g["qty"] += int(h.get("qty") or 1)
        g["refs"].append(h.get("ref"))
        g["lengths"].append(h.get("length_mm"))
    for key, g in cables.items():
        h, mid = g["h"], g["mid"]
        m = module(lib, mid) if mid else {}
        name = str(first(m, "name", default=h.get("cable") or key))
        notes = []
        if h.get("pins"):
            notes.append(f"Keyed cable, {h.get('pins')} pins; length class needed: {', '.join(f'{x} mm' for x in g['lengths'] if x)}.")
        if mid and g["how"] == "by_pins":
            notes.append(f"Matched to library cable {mid} by pin count and length (normalize gave no cable module).")
        if not mid:
            notes.append(f"No library cable with {h.get('pins')} pins exists yet: this item has no source or price.")
        rows.append(_row(name, g["qty"], m, module_id=mid, catalog_row=first(h, "catalog_row"), refs=[r for r in g["refs"] if r],
                         model_parts=[], kind="cable", use="harness to " + ", ".join(r for r in g["refs"] if r),
                         extra_notes=notes, lib=lib, prefer_vendor=prefer.get(name)))
        hb = harness_by_ref(electrical)
        crossed = sorted(r for r in g["refs"] if r in hb and is_crossed(hb[r], module(lib, hb[r].get("_module")) if hb[r].get("_module") else {}))
        lens = sorted({int(hb[r]["length_mm"]) for r in g["refs"] if r in hb and hb[r].get("length_mm")})
        if lens:  # the SKU's own length (library row length_mm) stays in length_mm; the plan's length is kept beside it
            rows[-1]["harness_length_mm"] = lens[0] if len(lens) == 1 else lens
            if rows[-1].get("length_mm") is None:
                ln = _length_mm(m) if m else None
                rows[-1]["length_mm"] = int(ln) if ln else None
        if crossed:
            rows[-1]["crossed_refs"] = crossed
            if not rows[-1].get("made_to_drawing"):
                rows[-1]["notes"] += (f" {', '.join(crossed)}: the module's pin order differs from the port order, so a "
                                      "straight-through lead would swap pins.")

    # hardware (fasteners, inserts, feet, ties), one row per library module
    hw_groups: dict[str, dict] = {}
    for hw in d.get("hardware") or []:
        hw = as_dict(hw)
        key = hw.get("module") or hw.get("id") or hw.get("name")
        g = hw_groups.setdefault(key, {"qty": 0, "items": [], "ids": [], "mid": hw.get("module"), "row": hw.get("catalog_row"),
                                       "name": hw.get("name") or hw.get("id")})
        g["qty"] += int(hw.get("qty") or 1)
        g["items"].append(f"{hw.get('qty')} x {hw.get('name') or hw.get('id')}" + (f" ({hw['use']})" if hw.get("use") else ""))
        g["ids"].append(hw.get("id"))
    for key, g in hw_groups.items():
        mid = g["mid"]
        m = module(lib, mid) if mid else {}
        name = str(first(m, "name", default=g["name"] or key))
        parts = component_names(comps, module_id=mid, role=m.get("role")) if mid else []
        for hid in g["ids"]:
            for n in component_names(comps, hint=hid):
                if n not in parts:
                    parts.append(n)
        parts = primary_part(parts, comps, module_id=mid, groups=hw_groups_of.get(key) or [])
        # quantities: the CAD's fastener groups are the source of truth (what the model and the manual show)
        qty, basis, use, notes = g["qty"], "normalizer hardware list (the CAD has no group for this part)", "; ".join(g["items"]), []
        counted = [(n, *cad_group_qty(as_dict(comps[n]))) for n in parts if n in comps]
        countable = str(m.get("role") or "").lower() in COUNTED_ROLES or (not m and any(w in key.lower() for w in ("screw", "insert", "feet", "foot")))
        if countable and counted and all(q for _n, q, _b in counted):
            cad_qty = sum(q for _n, q, _b in counted)
            how = sorted({b for _n, _q, b in counted})
            use = "; ".join(f"{q} x {as_dict(comps[n]).get('label') or n}" for n, q, _b in counted)
            if cad_qty != g["qty"]:
                notes.append(f"Quantity {cad_qty} from the CAD model ({use}); the normalizer's hardware list says {g['qty']}.")
            qty, basis = cad_qty, f"cad/components.json ({', '.join(how)})"
        if str(m.get("role") or "").lower() == "gasket" and len(parts) > 1:
            hosts = [re.sub(r"(?i)^purchase_|gasket$", "", n) for n in parts]
            use = f"one strip, cut into rings for: {', '.join(h for h in hosts if h)}"
        rows.append(_row(name, qty, m, module_id=mid, catalog_row=g["row"], refs=[],
                         model_parts=parts, kind="hardware", use=use, lib=lib, extra_notes=notes, prefer_vendor=prefer.get(name)))
        rows[-1]["role"] = m.get("role")
        rows[-1]["quantity_basis"] = basis
        if qty != g["qty"]:
            rows[-1]["quantity_normalizer"] = g["qty"]
        rows[-1]["hardware_id"] = g["ids"][0]
        if len(set(g["ids"])) > 1:
            rows[-1]["hardware_ids"] = sorted(set(i for i in g["ids"] if i))

    # duplicate names would break the wiring.parts[].item join (QB-18): disambiguate deterministically
    seen_names: dict[str, int] = {}
    for r in rows:
        n = r["name"]
        seen_names[n] = seen_names.get(n, 0) + 1
        if seen_names[n] > 1:
            r["name"] = f"{n} ({seen_names[n]})"
            problems.append(f"duplicate purchased name {n!r} renamed to {r['name']!r}")
    return rows, problems


# ------------------------------------------------------------------ printed[]


def hex_color(c: Any, default: str) -> str:
    if isinstance(c, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", c.strip()):
        return "#" + c.strip().lstrip("#").upper()
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        vals = [float(x) for x in c[:3]]
        if max(vals) <= 1.0:
            vals = [v * 255 for v in vals]
        return "#" + "".join(f"{int(round(max(0, min(255, v)))):02X}" for v in vals)
    return default


_SUPPORT_CLAIM = re.compile(r"(?i)[;,]?\s*(?:\bno supports?\b|\b(?:tree|organic|auto)\s+supports?\b[^;.]*|\bsupports?\s+(?:for|under|in|on)\b[^;.]*)")


def support_note(cad_note: Optional[str], st: dict, prof: Optional[dict] = None) -> tuple[str, str]:
    """MIN-12: (orientation note, support note) where the support words come from the slicer result, never from the CAD's
    expectation. A CAD note's support area is kept as 'the CAD expected supports for ...'."""
    cad_note = str(cad_note or "")
    expected = None
    m = re.search(r"(?i)supports?\s+(?:for|under|in|on)\s+([^;.]+)", cad_note)
    if m:
        expected = m.group(1).strip()
    orient = _SUPPORT_CLAIM.sub("", cad_note).strip(" ;,") or "as exported: the STL already rests on the bed at Z=0 in its print orientation"
    g = float(st.get("support_g") or 0)
    plate_only = (prof or {}).get("support_material_buildplate_only") == "1"
    if st.get("ok") is False:
        return orient, "supports unknown: the part did not slice"
    if g > 0:
        txt = (f"needs supports: {g:g} g of support material sliced (PrusaSlicer auto supports"
               + (", from the build plate only" if plate_only else "") + ")")
        if expected:
            txt += f"; the CAD expected supports for {expected}"
        elif re.search(r"(?i)\bno supports?\b", cad_note):
            txt += "; the CAD note expected none, so check the sliced preview for the supported overhang"
        return orient, txt
    return orient, "no supports: the slicer generated none"


def build_printed(slice_parts: dict, components: dict | None, spec: dict, material: str, cad_summary: dict | None = None,
                  prof: Optional[dict] = None) -> list[dict]:
    comps = components or {}
    colors = spec.get("colors") or {}
    orient = {}
    for src in ((cad_summary or {}).get("print") or {}, (cad_summary or {}).get("print_orientation") or {}):
        if isinstance(src, dict):
            for k, v in src.items():
                orient[k] = v.get("orientation_note") or v.get("note") or v.get("orientation") if isinstance(v, dict) else v
    rows = []
    for stem, st in slice_parts.items():
        c = as_dict(comps.get(stem))
        cp = c.get("print") if isinstance(c.get("print"), dict) else {}
        kind = "test_coupon" if stem.startswith("TEST_") else "print"
        note = first(cp, "orientation_note") or first(c, "orientation_note", "print_orientation", "orientation") or orient.get(stem)
        onote, snote = support_note(note if isinstance(note, str) else None, st, prof)
        cad_mat = first(cp, "material")
        row = {
            "name": stem,
            "file": f"print/{st.get('file', stem + '.stl')}",
            "kind": kind,
            "material": material,
            "material_basis": "print/slice.ini filament_type (the profile every part is sliced and priced with)"
                              + (f"; the CAD summary says {cad_mat}" if cad_mat and str(cad_mat).upper() != str(material).upper() else ""),
            "color": hex_color(first(c, "color"), colors.get("body", "#CCCCCC")),
            "color_name": colors.get("body_name") if kind == "print" else None,
            "grams": st.get("filament_g"),
            "hours": st.get("print_h"),
            "print_time": st.get("print_time"),
            "supports": st.get("supports"),
            "support_g": st.get("support_g"),
            "size_mm": st.get("size_mm"),
            "fits_bed": st.get("fits_bed"),
            "rotate_z_deg": st.get("rotate_z_deg") or 0,
            "sliced": bool(st.get("ok")),
            "orientation_note": f"{onote[:1].upper()}{onote[1:]}; {snote}.",
            "support_note": snote,
            "orientation_basis": ("cad orientation; supports from the slicer" if note else
                                  "default orientation (the CAD stage gave no reason); supports from the slicer"),
        }
        if not st.get("ok"):
            row["error"] = st.get("error")
        if kind == "test_coupon":
            row["note"] = f"Fit coupon: print it first in {material} with print/slice.ini; not a part of the gadget."
        rows.append(row)
    return rows


# ------------------------------------------------------------------ BP1 PCBA (REVIEW-1 MAJ-22, integration)
def apply_pcba(purchased: list[dict], pcba: Optional[dict], pcb_summary: Optional[dict] = None) -> list[str]:
    """Price the backplane as a populated board from pcb/pcba.json (bare board + every bom.csv line + assembly + shipping
    to India, a planning ESTIMATE) and count the USB-C receptacle once (it is the J_PWR line of that PCBA). Returns the
    quote reasons to add. No-op when the PCB stage wrote no pcba.json."""
    if not isinstance(pcba, dict) or not pcba.get("batches"):
        return []
    did = str(pcba.get("design_id") or (pcb_summary or {}).get("design_id") or "")
    drawing = f"BACKPLANE-V1-{did[:8].upper()}" if did else "BACKPLANE-V1"
    b5 = ((pcba.get("batches") or {}).get("5") or {}).get("inr_per_board") or {}
    b100 = ((pcba.get("batches") or {}).get("100") or {}).get("inr_per_board") or {}
    reasons = []
    for r in purchased:
        mid = str(r.get("module_id") or "")
        if mid == "BACKPLANE_V1" or str(r.get("role") or "") == "backplane":
            r["name"] = "Backplane PCBA BP1 (backplane-v1, made for this design)"
            r["sku"], r["sku_kind"] = drawing, "drawing"
            r["made_to_drawing"] = True
            r["factory_make"] = {
                "drawing": drawing, "by": "PCB fab (JLCPCB-class Economic PCBA: top-side SMT + manual through-hole)",
                "files": ["pcb/backplane-gerbers.zip", "pcb/bom.csv", "pcb/cpl.csv"],
                "steps": ["fabricate the 2-layer board from pcb/backplane-gerbers.zip",
                          "assemble every pcb/bom.csv line at its pcb/cpl.csv placement"],
                "test": "visual inspection at the fab; the DevKitC socket pins are checked against pcb/netlist.json on receipt",
                "price_basis": "pcb/pcba.json (status ESTIMATE, no fab quote taken)"}
            if b5.get("low") is not None and b5.get("high") is not None:
                r["price_inr_planning"] = [float(b5["low"]), float(b5["high"])]
                r["price_basis"] = (f"pcb/pcba.json: populated BP1 per board in a pilot batch of 5 (INR {b5['low']:,}-{b5['high']:,}, "
                                    f"{pcba.get('status', 'ESTIMATE')}; bare board, components, assembly, shipping to India; excl. GST)")
            if b100.get("low") is not None and b100.get("high") is not None:
                r["price_inr_planning_volume"] = [float(b100["low"]), float(b100["high"])]
            r["evidence_tier"], r["estimate"] = "estimate", True
            r["url"], r["vendor"] = None, None
            r["listing"] = None
            r["alternatives"] = []
            r["notes"] = ("Populated backplane made to this design's fab files (sku = drawing id; pcb/bom.csv, pcb/cpl.csv, "
                          "pcb/backplane-gerbers.zip). Price is the PCB stage's planning estimate (pcb/pcba.json), not a fab quote."
                          + (" Includes the USB-C receptacle (J_PWR)." if pcba.get("includes_usb_c_receptacle") else ""))
            reasons.append(f"BP1 PCBA priced from pcb/pcba.json (pilot batch of 5: INR {b5.get('low')}-{b5.get('high')} per board, "
                           f"volume batch of 100: INR {b100.get('low')}-{b100.get('high')}; planning estimate, no fab quote)")
            for x in pcba.get("excludes") or []:
                if "GST (the order stage" not in str(x):
                    reasons.append(f"BP1 PCBA excludes: {x}")
        elif pcba.get("includes_usb_c_receptacle") and str(r.get("role") or "") == "power_in" and r.get("mounted_on") == "backplane PCB":
            r["included_in"] = "BP1 PCBA"
            r["name"] = f"{r['name']} (fitted on the BP1 PCBA, counted there)"
            r["price_inr_planning"], r["price_inr_planning_volume"] = [0.0, 0.0], [0.0, 0.0]
            r["price_basis"] = "included in the BP1 PCBA line (pcb/bom.csv J_PWR); not bought separately"
            r["evidence_tier"], r["estimate"] = "estimate", True
            r["price_inr_seen"] = r["price_inr_incl_gst"] = None
            r["sku"] = r.get("sku") or "USB4105-GF-A"
            r["made_to_drawing"] = True
            r["factory_make"] = {"drawing": drawing, "by": "PCB fab (part of the BP1 PCBA)", "line": "J_PWR"}
            r["url"], r["vendor"], r["listing"], r["alternatives"] = None, None, None, []
            r["notes"] = "Soldered on the backplane by the PCB fab as J_PWR; its cost is inside the BP1 PCBA line, so it is counted once."
    return reasons
