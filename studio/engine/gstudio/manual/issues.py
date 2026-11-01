"""Known design issues from the digital checks (BLK-7): failing collision and clearance pairs become a known-issue
line and a stop rule on every step that handles either part, and any failed CAD check opens the guide with a
do-not-print banner. Read from checks/digital-checks.json (with each row's data from its source file), else from
cad/digital-checks.json when the checks stage has not run yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .data import ManualData

PAIR_CHECKS = ("collisions", "feature_module_clearance", "clearances", "cartridge_clearance")


@dataclass
class Issue:
    check: str
    parts: tuple[str, str]
    kind: str  # "overlap" | "gap"
    value: Optional[float] = None  # overlap volume mm³ or gap mm
    limit: Optional[float] = None

    def line(self, names: dict[str, str]) -> str:
        pa, pb = self.parts
        a = f"{names[pa]} (`{pa}`)" if names.get(pa) and names[pa] != pa else f"`{pa}`"
        b = f"{names[pb]} (`{pb}`)" if names.get(pb) and names[pb] != pb else f"`{pb}`"
        if self.kind == "overlap":
            v = self.value
            return (f"The CAD shows {a} overlapping {b}"
                    + ("" if v is None else " by less than 0.1 mm³" if v < 0.05 else f" by about {v:.1f} mm³"))
        if not self.value:
            return f"The CAD shows {a} touching {b}; the check wants a gap of at least {self.limit:g} mm"
        return f"The CAD shows {a} only {self.value:.1f} mm from {b}; the check wants at least {self.limit:g} mm"

    def stop(self, names: dict[str, str]) -> str:
        a, b = (names.get(p, p) for p in self.parts)
        return f"{a} will not seat against {b}, or either part needs force: stop and photograph both from two sides"


@dataclass
class Issues:
    pairs: list[Issue] = field(default_factory=list)
    failed_cad: list[dict] = field(default_factory=list)  # CAD check rows with status failed
    source: Optional[str] = None

    @property
    def banner(self) -> bool:
        return bool(self.failed_cad)

    def for_parts(self, parts: list[str]) -> list[Issue]:
        ps = set(parts)
        return [i for i in self.pairs if i.parts[0] in ps or i.parts[1] in ps]


def _pairs(row: dict) -> list[Issue]:
    data = row.get("data") or {}
    out: list[Issue] = []
    cid = row.get("id") or ""
    if cid == "collisions":
        for it in data.get("intersections") or []:
            ps = it.get("parts") or []
            if len(ps) == 2:
                v = it.get("intersection_mm3")
                out.append(Issue(cid, (ps[0], ps[1]), "overlap", float(v) if v is not None else None))
    else:
        limit = 0.5 if cid == "feature_module_clearance" else 0.2
        for it in data.get("pairs") or []:
            ps = it.get("parts") or []
            g = it.get("gap_mm")
            if len(ps) != 2 or g is None or it.get("contact") or it.get("note"):
                continue
            if float(g) < limit:
                out.append(Issue(cid, (ps[0], ps[1]), "gap", float(g), limit))
    return out


def load_issues(data: ManualData) -> Issues:
    rows = data.digital_checks()
    src = "checks/digital-checks.json"
    if not rows:
        rows = [dict(c, stage="cad", artifact="cad/digital-checks.json") for c in data.cad_checks()]
        src = "cad/digital-checks.json" if rows else None
    out = Issues(source=src)
    seen = set()
    for r in rows:
        if r.get("stage") not in (None, "cad") or r.get("status") != "failed":
            continue
        out.failed_cad.append(r)
        if r.get("id") in PAIR_CHECKS:
            for i in _pairs(r):
                key = tuple(sorted(i.parts))
                if key not in seen:
                    seen.add(key)
                    out.pairs.append(i)
    return out
