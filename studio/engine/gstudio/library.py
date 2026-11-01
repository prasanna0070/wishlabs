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
        self.modules: list[dict] = list(data)
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
        """Modules allowed in electronics.extra_modules (no aperture, connect on backplane-v1)."""
        ok = {"imu", "rtc", "hall", "endstop", "servo", "touch", "temp_humidity", "pressure", "light_sensor"}
        return [m for m in self.modules if m.get("role") in ok and (m.get("backplane_v1") or {}).get("supported")]

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

    # ------------------------------------------------------------------ LLM table
    def summary_for_llm(self, include_unsupported: bool = False) -> str:
        """Compact table: id | role | name | dims | aperture | price ₹ (+ how it connects). Only parts a spec may name."""
        spec_roles = {"core", "display", "led", "mic", "speaker", "buzzer", "button", "touch", "light_sensor", "temp_humidity",
                      "pressure", "distance", "motion", "imu", "hall", "rtc", "servo", "endstop", "encoder", "fan", "amp", "storage"}
        lines = ["id | role | name | dims mm | aperture | price ₹ | use"]
        for m in self.modules:
            if m.get("role") not in spec_roles:
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


__all__ = ["Library", "UnknownModule"]
