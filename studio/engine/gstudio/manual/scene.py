"""What the figure renderer needs to know about the model, as plain data (MAJ-27).

The stage builds a Scene from the classified CAD; `manual/figures.json` stores the same Scene, so the shipped
`manual/render_manual.py` renders the figures from the export with the code path the stage used.
No gstudio imports here: this module is copied into the standalone renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SceneComp:
    name: str
    color: tuple[float, float, float]
    bbox: Optional[tuple] = None
    qty: int = 1
    raw: dict = field(default_factory=dict)  # only "solid_index" / "base" are read by the renderer

    def to_json(self) -> dict:
        d: dict[str, Any] = {"color": [round(float(v), 4) for v in self.color], "qty": int(self.qty)}
        if self.bbox:
            d["bbox"] = [round(float(v), 4) for v in self.bbox]
        si = self.raw.get("solid_index")
        if si is not None:
            d["solid_index"] = list(si)
            d["base"] = self.raw.get("base")
        return d

    @classmethod
    def from_json(cls, name: str, d: dict) -> "SceneComp":
        raw = {}
        if d.get("solid_index") is not None:
            raw = {"solid_index": list(d["solid_index"]), "base": d.get("base")}
        return cls(name=name, color=tuple(d.get("color") or (0.7, 0.7, 0.7)), bbox=tuple(d["bbox"]) if d.get("bbox") else None,
                   qty=int(d.get("qty") or 1), raw=raw)


@dataclass
class Scene:
    comps: dict[str, SceneComp] = field(default_factory=dict)
    anchors: dict[str, tuple] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)  # anchors.json key -> component that carries it
    # REVIEW-1 MAJ-32: lit screens (cad-summary info.screens): {component, c, u, v, n, aa_w, aa_h, texture (path from the root)}
    screens: list = field(default_factory=list)

    def comp(self, name: str) -> Optional[SceneComp]:
        return self.comps.get(name)

    def qty(self, name: str) -> int:
        c = self.comps.get(name)
        return int(c.qty) if c else 1

    def owner(self, key: str) -> Optional[str]:
        return self.owners.get(key)

    def to_json(self) -> dict:
        return {"comps": {k: v.to_json() for k, v in sorted(self.comps.items())},
                "anchors": {k: [round(float(x), 4) for x in v] for k, v in sorted(self.anchors.items())},
                "owners": dict(sorted(self.owners.items())), "screens": list(self.screens)}

    @classmethod
    def from_json(cls, d: dict) -> "Scene":
        return cls(comps={k: SceneComp.from_json(k, v) for k, v in (d.get("comps") or {}).items()},
                   anchors={k: tuple(v) for k, v in (d.get("anchors") or {}).items()},
                   owners=dict(d.get("owners") or {}), screens=list(d.get("screens") or []))
