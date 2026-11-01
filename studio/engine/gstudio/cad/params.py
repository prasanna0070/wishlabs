"""Adapter: (spec, library, electrical) -> params dict, the single self-contained input of the geometry builder.

params.json is what cad/gadget_cad.py reads, so the delivered CAD source rebuilds without the engine, the library
or the spec models. This is the only CAD module that touches foundation objects (duck-typed: pydantic models or dicts).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from . import refs_provisional as P
from .modules import describe, fasteners

PARAMS_SCHEMA = "gadget-cad-params/1"
STUDIO = Path(__file__).resolve().parents[3]


def as_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "to_json_dict"):
        return obj.to_json_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if isinstance(obj, dict):
        return obj
    return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))


class LibAccess:
    """Uniform access to the library: gstudio.library.Library, a list of dicts, or the JSON files on disk."""

    def __init__(self, lib: Any = None):
        self.lib = lib
        self._by_id: dict[str, dict] = {}
        self._bp: Optional[dict] = None
        if lib is not None and hasattr(lib, "get") and not isinstance(lib, dict):
            return
        mods = []
        if isinstance(lib, list):
            mods = lib
        elif isinstance(lib, dict) and "modules" in lib:
            mods = lib["modules"]
        else:
            d = Path(os.environ.get("GS_LIBRARY_DIR", STUDIO / "library"))
            if (d / "modules.json").exists():
                data = json.loads((d / "modules.json").read_text())
                mods = data.get("modules", data) if isinstance(data, dict) else data
            else:
                for f in ("modules-a.json", "modules-b.json"):
                    if (d / f).exists():
                        mods += json.loads((d / f).read_text())
            if (d / "backplane-v1.json").exists():
                self._bp = json.loads((d / "backplane-v1.json").read_text())
        self._by_id = {m["id"]: m for m in mods}
        self._ci = {k.lower(): k for k in self._by_id}

    def get(self, mid: str) -> Optional[dict]:
        if self.lib is not None and hasattr(self.lib, "get") and not isinstance(self.lib, dict):
            try:
                return as_dict(self.lib.get(mid))
            except Exception:
                return None
        k = mid if mid in self._by_id else self._ci.get(str(mid).lower())
        return self._by_id.get(k) if k else None

    @property
    def backplane(self) -> Optional[dict]:
        if self.lib is not None and hasattr(self.lib, "backplane"):
            try:
                return as_dict(self.lib.backplane)
            except Exception:
                pass
        return self._bp


def _colors(c: dict) -> dict:
    from .geom import mix, rgb

    body = rgb(c.get("body", "#8CC9C0"))
    accent = rgb(c.get("accent", "#F4F1EA"))
    detail = rgb(c.get("detail", "#2B3A42"))
    return {
        "body": body, "accent": accent, "detail": detail,
        "blush": mix(body, (0.96, 0.52, 0.55), 0.62),
        "print_inner": mix(body, (0.92, 0.92, 0.90), 0.35),
        "button": accent, "button_cap": mix(accent, (1, 1, 1), 0.3),
        "diffuser": (0.97, 0.96, 0.92),
        "hex": {"body": c.get("body"), "accent": c.get("accent"), "detail": c.get("detail")},
    }


def _bp_descriptor(raw: Optional[dict]) -> dict:
    if not raw:
        return {"provisional": True, **P.BACKPLANE}
    out = dict(raw)
    out["provisional"] = False
    return out


ROLE_PREFIX = {"display": "DISP", "speaker": "SPK", "snap_button": "BTN", "tactile": "BTN", "led": "LED", "led_ring": "LED",
               "mic": "MIC", "touch": "TCH", "sensor_window": "SEN", "sensor": "SEN", "generic": "MOD", "amp": "AMP",
               "storage": "SD", "core": "U"}


def build_params(spec: Any, lib: Any = None, electrical: Optional[dict] = None, options: Optional[dict] = None) -> dict:
    sd = as_dict(spec)
    L = LibAccess(lib)
    warnings: list[str] = []
    derived = sd.get("derived") or {}
    instances = derived.get("instances") or []
    harness = derived.get("harness") or []
    by_feature = {i.get("feature"): i for i in instances if i.get("feature")}
    if electrical and electrical.get("instances"):
        for i in electrical["instances"]:
            if i.get("feature"):
                by_feature.setdefault(i["feature"], {}).update({k: v for k, v in i.items() if v is not None})
    bp_raw = L.backplane
    bp = _bp_descriptor(bp_raw)
    slots = {s["id"]: s for s in (bp.get("slots") or [])} if isinstance(bp.get("slots"), list) else (bp.get("slots") or {})
    used_slots: set[str] = set()
    counters: dict[str, int] = {}
    feats = []
    provisional_modules = []
    for i, f in enumerate(sd.get("features") or []):
        entry = L.get(f["module"])
        if entry is None:
            warnings.append(f"features[{i}]: module '{f['module']}' not in the library; using provisional geometry")
            entry = {"id": f["module"], "name": f["module"], "role": "generic"}
        desc = describe(entry)
        if desc.get("provisional"):
            provisional_modules.append({"id": desc["id"], "fields": desc["provisional_fields"]})
        inst = by_feature.get(f["id"], {})
        prefix = desc.get("ref_prefix") or ROLE_PREFIX.get(desc["kind"], "MOD")
        ref = inst.get("ref")
        if not ref:
            counters[prefix] = counters.get(prefix, 0) + 1
            ref = f"{prefix}{counters[prefix]}"
        slot = inst.get("slot")
        if not slot:
            cands = ((entry.get("backplane_v1") or {}).get("slots") or [])
            slot = next((c for c in cands if c not in used_slots), cands[0] if cands else None)
        if slot:
            used_slots.add(slot)
        h = next((x for x in harness if x.get("ref") == ref), None)
        cab = (entry.get("backplane_v1") or {}).get("cable") or {}
        if h is None:
            pins = cab.get("pins") or 0
            length = int((entry.get("backplane_harness") or {}).get("length_mm") or 150)
            fam = cab.get("family", "JST-PH 2.0")
            h = {"id": f"H_{ref}", "ref": ref, "slot": slot, "cable": f"{fam.split(' ')[0]} {pins}p {length} mm" if pins else "lead",
                 "pins": pins, "length_mm": length}
        feats.append({
            "id": f["id"], "module": entry.get("id", f["module"]), "face": f.get("face", "front"), "at": list(f["at"]),
            "label": f.get("label") or desc["name"], "grille": f.get("grille"), "rotate": f.get("rotate", 0),
            "ref": ref, "slot": slot, "harness": h, "desc": desc, "spec_path": f"features[{i}]",
        })
    # core + socketed internal modules
    core_id = (sd.get("electronics") or {}).get("core") or "CORE_ESP32S3_DEVKITC1_N8R8"
    core_entry = L.get(core_id) or {"id": core_id, "name": core_id, "role": "core"}
    internal = []
    have_spk = any(f["desc"]["kind"] == "speaker" for f in feats)
    tmpl = ((sd.get("behaviour") or {}).get("template") or "")
    implied = [i for i in instances if i.get("source") in ("implied", "extra")]
    want = []
    if implied:
        for i in implied:
            if i.get("role") in ("amp", "storage") or i.get("slot") in ("A1", "S1"):
                want.append((i.get("module"), i.get("ref"), i.get("slot")))
    else:
        if have_spk:
            want.append(("AMP_MAX98357A_I2S", "AMP1", "A1"))
        if tmpl == "story_player":
            want.append(("MICROSD_SPI_MODULE_5V", "SD1", "S1"))
    for mid, ref, slot in want:
        e = L.get(mid) or {"id": mid, "name": mid, "role": "amp" if slot == "A1" else "storage"}
        internal.append({"module": e.get("id", mid), "ref": ref or ("AMP1" if slot == "A1" else "SD1"), "slot": slot, "desc": describe(e)})
    extra = []
    for k, mid in enumerate((sd.get("electronics") or {}).get("extra_modules") or []):
        e = L.get(mid)
        if e is None:
            continue
        inst = next((i for i in instances if i.get("module") == e["id"] and i.get("source") == "extra"), {})
        extra.append({"module": e["id"], "ref": inst.get("ref") or f"{e.get('ref_prefix') or 'MOD'}{k + 1}", "slot": inst.get("slot"),
                      "desc": describe(e), "spec_path": f"electronics.extra_modules[{k}]"})
    hw = derived.get("hardware") or []
    params = {
        "schema": PARAMS_SCHEMA,
        "name": sd.get("name", "Gadget"), "slug": sd.get("slug", "gadget"),
        "colors_hex": sd.get("colors") or {}, "colors": _colors(sd.get("colors") or {}),
        "body": sd["body"], "decor": sd.get("decor") or [], "features": feats,
        "core": {"module": core_entry.get("id", core_id), "ref": next((i["ref"] for i in instances if i.get("source") == "core"), "U1"),
                 "desc": describe(core_entry)},
        "internal": internal, "extra": extra,
        "backplane": bp, "backplane_ref": next((i["ref"] for i in instances if i.get("role") == "backplane"), "BP1"),
        "fasteners": fasteners(L.get),
        "harness": harness,
        "hardware_modules": {h.get("id"): h.get("module") for h in hw if isinstance(h, dict)},
        "cartridge_hint": derived.get("cartridge"),
        "template": tmpl,
        "options": {"detail_fill": True, **(options or {})},
        "provenance": {"provisional_modules": provisional_modules, "backplane": "library/backplane-v1.json" if bp_raw else "provisional",
                       "fastener_sources": fasteners(L.get).get("sources", {})},
        "warnings": warnings,
    }
    return params


def jsonable(params: dict) -> dict:
    """Deep copy with tuples -> lists (params are already plain JSON types otherwise)."""
    return json.loads(json.dumps(params, default=lambda o: list(o) if isinstance(o, tuple) else str(o)))
