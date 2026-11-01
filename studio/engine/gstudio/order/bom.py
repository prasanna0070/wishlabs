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


NO_SOLDER_ROLES = {"cable", "harness", "usb_cable", "fastener", "screws", "inserts", "feet", "ties", "mechanical", "consumable",
                   "label", "backplane", "cartridge", "gasket", "diffuser", "magnet", "panel", "servo_bracket", "servo_horn", "adapter"}
SOCKET_ROLES = {"controller", "core", "amp", "storage"}
# rows the blueprint catalog prices per set or kit ("M2/M2.5 screw set", "Heat-set inserts", "Adhesive feet / pads", ...)
SET_ROLES = {"fastener", "screws", "inserts", "feet", "ties", "consumable", "gasket", "magnet", "label"}
OPEN_END = re.compile(r"open (other )?end|bare (wire|end)|pigtail|unterminated|tinned end", re.I)
PLUG_CONNECTORS = ("jst", "ph", "xh", "grove", "qwiic", "stemma", "none", "socket", "usb")


def requires_soldering(m: dict, src: dict | None = None) -> tuple[Optional[bool], str]:
    """(value, basis). The library has no explicit field yet, so the basis says how the value was decided."""
    for k in ("requires_soldering", "requiresSoldering", "soldering_required"):
        if isinstance(m.get(k), bool):
            return m[k], "library" + (f": {m['requires_soldering_basis']}" if m.get("requires_soldering_basis") else "")
    mount = m.get("mount") if isinstance(m.get("mount"), dict) else {}
    for k in ("requires_soldering", "soldering"):
        if isinstance(mount.get(k), bool):
            return mount[k], "library mount"
    role = str(m.get("role") or "").lower()
    conn = str((m.get("module_connector") or {}).get("type") or "").lower() if isinstance(m.get("module_connector"), dict) else ""
    note = str(first(src or {}, "notes", default=""))
    if OPEN_END.search(note):
        return True, f"the chosen source lists an open other end ({note[:80]}): terminating it needs crimping or soldering"
    if role in NO_SOLDER_ROLES:
        return False, f"no electrical joint ({role})"
    if role in SOCKET_ROLES:
        return False, "plugs into a backplane socket on its own pin headers; check the headers are fitted on receipt"
    if conn and any(c in conn for c in PLUG_CONNECTORS):
        return False, f"plug-in connector ({conn})"
    if conn.startswith("header") or "2.54" in conn:
        return False, ("unverified: the library does not say whether this module's pin header comes fitted; "
                       "generic modules can ship with loose headers. Check on receipt (physical check)")
    return False, "unverified: library has no soldering field"


def soldered_by(m: dict, solder: bool, basis: str) -> Optional[str]:
    """Who makes the joint: 'pcb_fab' (part mounted on our backplane PCBA), 'factory' (library says the factory solders
    it), else 'maker'. None when no soldering is needed."""
    if not solder:
        return None
    bp = m.get("backplane_v1") if isinstance(m.get("backplane_v1"), dict) else {}
    if bp.get("port") == "on_board":
        return "pcb_fab"
    if "factory" in str(m.get("requires_soldering_basis") or basis).lower():
        return "factory"
    return "maker"


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
    return first(src, "seen_on", "checked_at", "checkedAt", "date", "observed_on", "retrieved")


def page_read(src: dict) -> bool:
    """A price read on the vendor's own page (library status `page_read`), not just a search-result snippet."""
    st = str(first(src, "status", default="")).lower()
    return st not in ("search_snippet", "snippet", "unverified")


def out_of_stock(src: dict) -> bool:
    t = f"{first(src, 'stock', default='')} {first(src, 'notes', default='')}".lower()
    return "out of stock" in t or "out_of_stock" in t or "sold out" in t


def choose_source(m: dict) -> tuple[dict, list[dict]]:
    """(chosen, alternatives) from `sourcing_in[]`. Keeps the library's order (its preference) except that an
    explicitly selected row wins, and rows without a URL, out of stock, or with only a search-snippet price drop back."""
    rows = [as_dict(s) for s in (m.get("sourcing_in") or []) if s]
    if not rows:
        return {}, []

    def score(s: dict) -> tuple:
        sel = bool(first(s, "selected", "preferred", "primary", default=False))
        return (not sel, not bool(first(s, "url")), out_of_stock(s), not page_read(s), price_seen(s) is None)
    ranked = sorted(rows, key=score)
    return ranked[0], ranked[1:]


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
         model_parts: list[str], kind: str, use: str = "", extra_notes: list[str] | None = None, lib: Any = None) -> dict:
    src, alts = choose_source(m)
    pack = pack_size(src, m)
    seen = price_seen(src)
    when = seen_on(src)
    rng = planning_range(m)
    basis, cat_id = (f"library {module_id} price_inr_planning" if rng else None), None
    if rng is None:
        rng, basis, cat_id = catalog_range(lib, module_id, catalog_row or first(m, "catalog_row"))
    if seen is not None and when and page_read(src):
        tier, estimate = "seen", False
    elif rng is not None or seen is not None:
        tier, estimate = "estimate", True
    else:
        tier, estimate = "not_confirmed", True
    notes = list(extra_notes or [])
    if tier == "seen":
        notes.append(f"Price seen at {first(src, 'vendor', 'seller', default='the listed vendor')} on {when}: a retail snapshot, "
                     "not reserved stock or a signed receipt.")
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
        "requiresSoldering": bool(sold),
        "requiresSoldering_basis": sold_basis,
        "solderedBy": soldered_by(m, bool(sold), sold_basis),
        "url": first(src, "url"),
        "vendor": first(src, "vendor", "seller", "store"),
        "sku": first(src, "sku", "SKU", "product_code"),
        "price_inr_planning": rng,
        "price_basis": basis,
        "price_catalog_id": cat_id,
        "price_inr_seen": seen if tier == "seen" else None,
        "seen_on": when if tier == "seen" else None,
        "estimate": estimate,
        "evidence_tier": tier,
        "listing": ({"url": first(src, "url"), "vendor": first(src, "vendor", "seller"), "sku": first(src, "sku"),
                     "packQuantity": pack, "seen_on": when, "status": first(src, "stock", "status", default="seen"),
                     "price_inr_seen": seen, "notes": first(src, "notes"), "signed": False} if src else None),
        "alternatives": [{"vendor": first(a, "vendor", "seller"), "url": first(a, "url"), "sku": first(a, "sku"),
                          "price_inr": price_seen(a), "seen_on": seen_on(a), "status": first(a, "status"),
                          "pack": pack_size(a, m), "notes": first(a, "notes")} for a in alts],
        "notes": " ".join(notes),
        "modelPart": model_parts[0] if model_parts else None,
    }
    if len(model_parts) > 1:
        row["modelParts"] = model_parts
    return row


def build_purchased(spec: dict, lib: Any, components: dict | None) -> tuple[list[dict], list[str]]:
    """Return (purchased rows, problems). `spec` is the normalized spec dict with `derived`."""
    d = spec.get("derived") or {}
    comps = components or {}
    rows: list[dict] = []
    problems: list[str] = []

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
        uses = sorted({f for f in g["features"]})
        rows.append(_row(name, g["qty"], m, module_id=mid, catalog_row=None, refs=[r for r in g["refs"] if r],
                         model_parts=parts, kind="module", use=", ".join(uses), lib=lib))

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
                         extra_notes=notes, lib=lib))

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
        rows.append(_row(name, qty, m, module_id=mid, catalog_row=g["row"], refs=[],
                         model_parts=sorted(parts), kind="hardware", use=use, lib=lib, extra_notes=notes))
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


def build_printed(slice_parts: dict, components: dict | None, spec: dict, material: str, cad_summary: dict | None = None) -> list[dict]:
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
        row = {
            "name": stem,
            "file": f"print/{st.get('file', stem + '.stl')}",
            "kind": kind,
            "material": first(cp, "material") or material,
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
            "orientation_note": note or "Print as exported: the STL already rests on the bed at Z=0 in its print orientation.",
            "orientation_basis": "cad" if note else "default (the CAD stage gave no orientation reason)",
        }
        if not st.get("ok"):
            row["error"] = st.get("error")
        if kind == "test_coupon":
            row["note"] = "Fit coupon: print first with the same material and settings; not a part of the gadget."
        rows.append(row)
    return rows
