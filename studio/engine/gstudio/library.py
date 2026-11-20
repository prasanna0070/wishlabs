"""Module library access (ARCHITECTURE §4). Owned by foundation.

    lib = Library()                      # studio/library/modules.json, or $GS_LIBRARY (file or directory)
    lib.get("OLED_096_SSD1306_I2C")      # dict; aliases resolve; unknown ids raise UnknownModule (with suggestions)
    lib.by_role("display")               # canonical roles (see library/README.md)
    lib.aperture_modules()               # modules that may be a spec feature on backplane-v1
    lib.summary_for_llm()                # compact text table for the agent prompt
    lib.catalog_id_for_price(id)         # ../sim/spec/catalog.json id, or None
    lib.backplane                        # library/backplane-v1.json as a dict
    lib.slot("J_I2C1")                   # one backplane slot dict

Path resolution: explicit `path` > env GS_LIBRARY > <package>/../../library (repo layout: studio/engine/gstudio ->
studio/library) > <package>/library (wheel layout). The price catalog: env GS_SIM_CATALOG > studio/../sim/spec/catalog.json.
"""

from __future__ import annotations

import difflib
import json
import os
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Optional

_PKG = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------------------------- harness families
# REVIEW-1 BLK-2/BLK-3: each design harness row has ONE module-end termination family.
CABLE_FAMILIES = ("jst_ph", "jst_xh", "dupont_2.54", "spade_2.8")
_FAMILY_OF_CONNECTOR = {"jst_ph_2.0": "jst_ph", "jst_ph": "jst_ph", "jst_xh_2.5": "jst_xh", "jst_xh": "jst_xh",
                        "header_2.54": "dupont_2.54", "dupont_2.54": "dupont_2.54", "spade_2.8": "spade_2.8"}
_PORT_FAMILY = {"JST-PH 2.0": "jst_ph", "JST-XH 2.5": "jst_xh"}


def cable_family_for(module_connector_type: Optional[str]) -> Optional[str]:
    """Harness module-end family that mates a module connector type: 'jst_ph_2.0' -> 'jst_ph', 'jst_xh_2.5' -> 'jst_xh',
    'header_2.54' -> 'dupont_2.54' (female Dupont housing on the module's pin header), 'spade_2.8' -> 'spade_2.8'.
    None when no stocked harness family mates it (solder pads, bare leads, qwiic, none): such a module needs factory
    pre-wiring or a made-to-drawing lead."""
    return _FAMILY_OF_CONNECTOR.get(str(module_connector_type or "").strip().lower())


def port_family(slot: dict) -> Optional[str]:
    """Connector family of a backplane port slot ('JST-PH 2.0' -> 'jst_ph', 'JST-XH 2.5' -> 'jst_xh')."""
    return _PORT_FAMILY.get(str(slot.get("connector") or ""))


def module_end_family(m: dict) -> Optional[str]:
    """Module-end family a harness for this library module must have: 'factory_prewired' when the factory solders a lead
    to the module (m['prewire'].lead), else cable_family_for(module_connector.type)."""
    if (m.get("prewire") or {}).get("lead"):
        return "factory_prewired"
    return cable_family_for((m.get("module_connector") or {}).get("type"))


class UnknownModule(KeyError):
    def __init__(self, mid: str, suggestions: list[str]):
        self.mid = mid
        self.suggestions = suggestions
        msg = f"module '{mid}' is not in the library"
        if suggestions:
            msg += f"; did you mean {', '.join(suggestions)}?"
        super().__init__(msg)

    def __str__(self) -> str:  # KeyError quotes its arg; keep it readable
        return self.args[0]


def _resolve_dir(path: Optional[str | os.PathLike]) -> Path:
    cands: list[Path] = []
    if path:
        cands.append(Path(path))
    env = os.environ.get("GS_LIBRARY")
    if env:
        cands.append(Path(env))
    cands += [_PKG.parent.parent / "library", _PKG / "library", Path.cwd() / "library", Path.cwd().parent / "library"]
    for c in cands:
        if c.is_file():
            return c.parent
        if (c / "modules.json").is_file():
            return c
    raise FileNotFoundError("library/modules.json not found; set GS_LIBRARY. Tried: " + ", ".join(map(str, cands)))


def _resolve_file(path: Optional[str | os.PathLike]) -> Path:
    if path and Path(path).is_file():
        return Path(path)
    env = os.environ.get("GS_LIBRARY")
    if env and Path(env).is_file() and not path:
        return Path(env)
    return _resolve_dir(path) / "modules.json"


class Library:
    def __init__(self, path: Optional[str | os.PathLike] = None, backplane_path: Optional[str | os.PathLike] = None):
        self.path = _resolve_file(path)
        self.dir = self.path.parent
        self._backplane_path = Path(backplane_path) if backplane_path else self.dir / "backplane-v1.json"
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("modules", [])
        # CATALOG-PLAN 0.5.3: rows of a pack that is off are invisible (packs.enabled_set() is read once per Library)
        from . import packs as _packs

        self.packs_on: frozenset = _packs.enabled_set(self.dir)
        self.all_modules: list[dict] = list(data)
        self.modules: list[dict] = [m for m in self.all_modules if _packs.visible(m, on=self.packs_on)]
        self._by_id: dict[str, dict] = {}
        for m in self.modules:
            self._by_id[m["id"]] = m
        self._alias: dict[str, str] = {}
        for m in self.modules:
            for a in m.get("aliases") or []:
                self._alias.setdefault(a, m["id"])
        self._ci = {k.lower(): k for k in self._by_id}

    # ------------------------------------------------------------------ lookup
    def resolve_id(self, mid: str) -> Optional[str]:
        if mid in self._by_id:
            return mid
        if mid in self._alias:
            return self._alias[mid]
        return self._ci.get(str(mid).lower())

    def has(self, mid: str) -> bool:
        return self.resolve_id(mid) is not None

    def get(self, mid: str) -> dict:
        rid = self.resolve_id(mid)
        if rid is None:
            raise UnknownModule(mid, self.suggest(mid))
        return self._by_id[rid]

    def suggest(self, mid: str, n: int = 3, role: Optional[str] = None) -> list[str]:
        pool = [m["id"] for m in self.modules if role is None or m.get("role") == role]
        hits = difflib.get_close_matches(str(mid).upper(), pool, n=n, cutoff=0.3)
        if not hits:
            toks = [t for t in str(mid).upper().replace("-", "_").split("_") if len(t) > 2]
            hits = [i for i in pool if any(t in i for t in toks)][:n]
        return hits

    def __contains__(self, mid: str) -> bool:
        return self.has(mid)

    def __iter__(self):
        return iter(self.modules)

    def __len__(self) -> int:
        return len(self.modules)

    def ids(self) -> list[str]:
        return [m["id"] for m in self.modules]

    def by_role(self, role: str | Iterable[str]) -> list[dict]:
        roles = {role} if isinstance(role, str) else set(role)
        return [m for m in self.modules if m.get("role") in roles]

    def role(self, mid: str) -> str:
        return self.get(mid).get("role", "")

    def supported(self, mid: str) -> bool:
        return bool((self.get(mid).get("backplane_v1") or {}).get("supported"))

    def aperture_modules(self) -> list[dict]:
        """Modules that may be bound to a spec feature (face aperture) and connect on backplane-v1."""
        return [m for m in self.modules if (m.get("aperture") or {}).get("feature_allowed")
                and (m.get("backplane_v1") or {}).get("supported")]

    def internal_modules(self) -> list[dict]:
        """Modules allowed in electronics.extra_modules (no aperture, connect on backplane-v1). The bare SG90 row
        (role servo) is not offered: no app drives it and it gets no CAD mount; the motion pack adds feature rows."""
        ok = {"imu", "rtc", "hall", "endstop", "touch", "temp_humidity", "pressure", "light_sensor"}
        return [m for m in self.modules if (m.get("role") in ok and not m.get("merge") or _pack_placed(m, "internal"))
                and (m.get("backplane_v1") or {}).get("supported")]

    def harness_rows(self, slot: Optional[str] = None, length_mm: Optional[int] = None) -> list[dict]:
        """Design harness rows (generated CABLE_* rows with a module_end table), optionally for one port slot and length."""
        out = []
        for m in self.modules:
            if m.get("role") != "harness" or not m.get("design_harness"):
                continue
            if slot is not None and slot not in ((m.get("backplane_end") or {}).get("slots") or []):
                continue
            if length_mm is not None and int(m.get("length_mm") or 0) != int(length_mm):
                continue
            out.append(m)
        return out

    def requires(self, mid: str) -> list[dict]:
        """Parts this module needs bought with it (e.g. MICROSD_SPI_MODULE_5V -> the storage card): [{module, qty, why}]."""
        return [dict(r) for r in (self.get(mid).get("requires") or []) if isinstance(r, dict) and r.get("module")]

    def price_range(self, mid: str) -> Optional[tuple[float, float]]:
        p = self.get(mid).get("price_inr_planning")
        return (float(p[0]), float(p[1])) if isinstance(p, (list, tuple)) and len(p) == 2 else None

    # ------------------------------------------------------------------ backplane
    @cached_property
    def backplane(self) -> dict:
        return json.loads(self._backplane_path.read_text(encoding="utf-8"))

    def slot(self, sid: str) -> dict:
        for s in self.backplane["slots"]:
            if s["id"] == sid:
                return s
        raise KeyError(f"backplane slot '{sid}' not found")

    # ------------------------------------------------------------------ pricing map
    @cached_property
    def _catalog(self) -> Optional[dict]:
        cands = []
        if os.environ.get("GS_SIM_CATALOG"):
            cands.append(Path(os.environ["GS_SIM_CATALOG"]))
        cands += [self.dir.parent.parent / "sim" / "spec" / "catalog.json", self.dir / "catalog.json"]
        for c in cands:
            if c.is_file():
                try:
                    return json.loads(c.read_text(encoding="utf-8")).get("items", {})
                except (OSError, json.JSONDecodeError):
                    return None
        return None

    def catalog_id_for_price(self, mid: str) -> Optional[str]:
        """Map a module to a sim price-engine catalog id: its own catalog_id if the catalog has it, else the catalog item
        with the same blueprint row, else None. Without a catalog file, returns the module's catalog_id (unchecked)."""
        m = self.get(mid)
        cid = m.get("catalog_id")
        cat = self._catalog
        if cat is None:
            if cid:
                return cid
            # variants carry catalog_id null; the row's primary module carries the sim id (library A notes)
            row = m.get("catalog_row")
            return next((x["catalog_id"] for x in self.modules if x.get("catalog_row") == row and x.get("catalog_id")), None)
        if cid and cid in cat:
            return cid
        row = m.get("catalog_row")
        for k, v in cat.items():
            if v.get("row") == row:
                return k
        return None

    def agent_visible(self, m: dict) -> bool:
        """True when the agent's parts table lists this row as usable (supported on backplane-v1)."""
        return _agent_role(m) and bool((m.get("backplane_v1") or {}).get("supported"))

    def role_available(self, role: str) -> bool:
        """True when the agent can design with some row of this role (pack rows only while their pack is on)."""
        return any(self.agent_visible(m) for m in self.by_role(role))

    # ------------------------------------------------------------------ LLM table
    def summary_for_llm(self, include_unsupported: bool = False) -> str:
        """Compact table: id | role | name | dims | aperture | price ₹ (+ how it connects). Only parts a spec may name."""
        lines = ["id | role | name | dims mm | aperture | price ₹ | use"]
        for m in self.modules:
            if not _agent_role(m):
                continue
            pv1 = m.get("backplane_v1") or {}
            if not pv1.get("supported") and not include_unsupported:
                continue
            d = m.get("dims_mm") or {}
            dims = "x".join("?" if d.get(k) is None else f"{float(d[k]):g}" for k in ("x", "y", "z"))
            ap = m.get("aperture") or {}
            aps = ap.get("needs", "none")
            size = ap.get("size_mm") or {}
            if aps != "none" and size.get("w"):
                aps += f" {float(size['w']):g}x{float(size.get('h') or 0):g}"
            pr = m.get("price_inr_planning") or ["?", "?"]
            role = m.get("role")
            if role == "core":
                use = "electronics.core"
            elif role in ("amp", "storage"):
                use = "implied (never list)"
            elif ap.get("feature_allowed"):
                use = "feature"
            else:
                use = "extra_modules"
            if not pv1.get("supported"):
                use = "NOT SUPPORTED"
            name = str(m.get("name", ""))[:48]
            lines.append(f"{m['id']} | {role} | {name} | {dims} | {aps} | {pr[0]}-{pr[1]} | {use}")
        return "\n".join(lines)


# roles of core rows the agent's table lists; `servo` is left out on purpose (the bare SG90 row has no app and no CAD
# mount: CATALOG-PLAN E1.6); pack rows are listed when their merge block says designable
AGENT_ROLES = {"core", "display", "led", "mic", "speaker", "buzzer", "button", "touch", "light_sensor", "temp_humidity",
               "pressure", "distance", "motion", "imu", "hall", "rtc", "endstop", "encoder", "fan", "amp", "storage"}


def _agent_role(m: dict) -> bool:
    mg = m.get("merge")
    return bool(mg.get("designable")) if isinstance(mg, dict) else m.get("role") in AGENT_ROLES


def _pack_placed(m: dict, placement: str) -> bool:
    """A pack row (CATALOG-PLAN 0.5.1) the agent may design with, placed as `placement` ("feature" or "internal")."""
    mg = m.get("merge")
    return isinstance(mg, dict) and bool(mg.get("designable")) and mg.get("placement") == placement


__all__ = ["Library", "UnknownModule", "CABLE_FAMILIES", "cable_family_for", "module_end_family", "port_family"]
