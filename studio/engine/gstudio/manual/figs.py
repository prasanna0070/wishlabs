"""Figure specifications: what each manual image shows. Pure data, no VTK.

Step planning builds these; `render3d` + `illustrate` turn them into PNGs. Keeping the plan separate
means the step text, the build.json camera hints and the images all come from one description.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .geom import Vec, bbox_center, bbox_offset, bbox_size, bbox_union, vnorm

# Views: direction from the target toward the camera (+Z up, front is -Y, +X is the viewer's right).
FRONT_34: Vec = (0.32, -1.0, 0.42)
FRONT: Vec = (0.0, -1.0, 0.12)
REAR_34: Vec = (-0.45, 1.0, 0.55)
TOP_34: Vec = (0.25, -0.55, 1.0)
TOP: Vec = (0.0, -0.18, 1.0)
SIDE_34: Vec = (1.0, -0.65, 0.55)
BELOW_34: Vec = (0.4, -0.8, -0.75)


@dataclass
class LabelSpec:
    text: str  # '\n' separates lines
    comp: Optional[str] = None  # anchor: deepest visible point of this component
    anchor: Optional[str] = None  # anchor: anchors.json key (moves with its owner's offset)
    point: Optional[Vec] = None  # anchor: world point
    priority: int = 0
    # anchor: first hit of a ray on a component's surface: (comp, origin, direction, fallback comp)
    ray: Optional[tuple] = None


@dataclass
class MeshSpec:
    """An extra mesh file shown in a panel (print-bed STLs)."""

    path: str
    color: tuple[float, float, float]
    offset: Vec = (0.0, 0.0, 0.0)
    name: str = ""


@dataclass
class PanelSpec:
    subtitle: str
    show: dict[str, str] = field(default_factory=dict)  # comp -> "solid" | "focus" | "ghost"; absent = hidden
    offsets: dict[str, Vec] = field(default_factory=dict)
    view_dir: Vec = FRONT_34
    focus: list[str] = field(default_factory=list)  # comps (with offsets) the camera frames
    focus_meshes: bool = False  # frame the extra meshes instead
    pad: float = 1.06
    min_extent: float = 0.0  # mm; the framed box is at least this big (keeps context around small parts)
    labels: list[LabelSpec] = field(default_factory=list)
    meshes: list[MeshSpec] = field(default_factory=list)
    floor: bool = False
    label_room: float = 0.12  # fraction of the panel width kept free on each side for labels
    layout: str = ""  # "" (assembly coordinates) | "grid" (kit of parts, one solid per multi-part item)
    arrows: list[tuple[Vec, Vec]] = field(default_factory=list)  # explicit motion arrows (world start -> end)
    arrow_comps: list[str] = field(default_factory=list)  # displaced parts that get an arrow per solid, toward home


@dataclass
class FigureSpec:
    file: str  # path relative to the output root, e.g. "manual/step-05-cartridge.png"
    title: str
    panels: list[PanelSpec]
    footer: str
    alt: str
    caption: str
    kind: str = "3d"  # 3d | wiring | ports
    size: Optional[tuple[int, int]] = None

    @property
    def wh(self) -> tuple[int, int]:
        if self.size:
            return self.size
        return (2000, 1100) if len(self.panels) >= 2 else (1200, 1100)


def panel_focus_bbox(panel: PanelSpec, comp_bboxes: dict[str, tuple]) -> Optional[tuple]:
    names = panel.focus or [n for n, m in panel.show.items() if m in ("focus",)] or list(panel.show)
    boxes = []
    for n in names:
        b = comp_bboxes.get(n)
        if b:
            boxes.append(bbox_offset(b, panel.offsets.get(n, (0.0, 0.0, 0.0))))
    bb = bbox_union(boxes)
    if bb and panel.min_extent > 0:
        c, s = bbox_center(bb), bbox_size(bb)
        h = [max(s[i], panel.min_extent) / 2 for i in range(3)]
        bb = (c[0] - h[0], c[0] + h[0], c[1] - h[1], c[1] + h[1], c[2] - h[2], c[2] + h[2])
    return bb


def camera_hint(panel: PanelSpec, comp_bboxes: dict[str, tuple]) -> Optional[dict]:
    """{position, target, up} in model mm (+Z up) for the web 3D viewer, from a panel's framing."""
    bb = panel_focus_bbox(panel, comp_bboxes)
    if not bb:
        return None
    c = bbox_center(bb)
    s = bbox_size(bb)
    diag = math.sqrt(sum(v * v for v in s)) or 100.0
    d = vnorm(panel.view_dir)
    dist = diag * 1.9 + 60.0
    return {"position": [round(c[i] + d[i] * dist, 1) for i in range(3)],
            "target": [round(v, 1) for v in c], "up": [0, 0, 1]}


# --------------------------------------------------------------------------------------------- JSON (figures.json, MAJ-27)
def _vec(v) -> Optional[list]:
    return None if v is None else [round(float(x), 4) for x in v]


def figure_to_json(f: FigureSpec) -> dict:
    def lab(l: LabelSpec) -> dict:
        d = {"text": l.text, "priority": l.priority}
        if l.comp:
            d["comp"] = l.comp
        if l.anchor:
            d["anchor"] = l.anchor
        if l.point is not None:
            d["point"] = _vec(l.point)
        if l.ray:
            t, o, dr, fb = l.ray
            d["ray"] = [list(t) if not isinstance(t, str) else t, _vec(o), _vec(dr), fb]
        return d

    def panel(p: PanelSpec) -> dict:
        return {"subtitle": p.subtitle, "show": dict(p.show), "offsets": {k: _vec(v) for k, v in p.offsets.items()},
                "view_dir": _vec(p.view_dir), "focus": list(p.focus), "focus_meshes": p.focus_meshes, "pad": p.pad,
                "min_extent": p.min_extent, "labels": [lab(x) for x in p.labels],
                "meshes": [{"path": m.path, "color": _vec(m.color), "offset": _vec(m.offset), "name": m.name} for m in p.meshes],
                "floor": p.floor, "label_room": p.label_room, "layout": p.layout,
                "arrows": [[_vec(a), _vec(b)] for a, b in p.arrows], "arrow_comps": list(p.arrow_comps)}

    return {"file": f.file, "title": f.title, "panels": [panel(p) for p in f.panels], "footer": f.footer, "alt": f.alt,
            "caption": f.caption, "kind": f.kind, "size": list(f.size) if f.size else None}


def figure_from_json(d: dict, rebase=None) -> FigureSpec:
    """Inverse of figure_to_json. `rebase(path)` maps a mesh path saved in the bundle to a local path."""
    def tup(v):
        return tuple(v) if v is not None else None

    def lab(x: dict) -> LabelSpec:
        ray = None
        if x.get("ray"):
            t, o, dr, fb = x["ray"]
            ray = (t if isinstance(t, str) else list(t), tup(o), tup(dr), fb)
        return LabelSpec(text=x["text"], comp=x.get("comp"), anchor=x.get("anchor"), point=tup(x.get("point")),
                         priority=int(x.get("priority") or 0), ray=ray)

    def panel(p: dict) -> PanelSpec:
        return PanelSpec(subtitle=p["subtitle"], show=dict(p.get("show") or {}),
                         offsets={k: tup(v) for k, v in (p.get("offsets") or {}).items()}, view_dir=tup(p["view_dir"]),
                         focus=list(p.get("focus") or []), focus_meshes=bool(p.get("focus_meshes")), pad=float(p.get("pad", 1.06)),
                         min_extent=float(p.get("min_extent") or 0.0), labels=[lab(x) for x in p.get("labels") or []],
                         meshes=[MeshSpec(path=(rebase(m["path"]) if rebase else m["path"]), color=tup(m["color"]),
                                          offset=tup(m.get("offset") or (0, 0, 0)), name=m.get("name") or "") for m in p.get("meshes") or []],
                         floor=bool(p.get("floor")), label_room=float(p.get("label_room", 0.12)), layout=p.get("layout") or "",
                         arrows=[(tup(a), tup(b)) for a, b in p.get("arrows") or []], arrow_comps=list(p.get("arrow_comps") or []))

    return FigureSpec(file=d["file"], title=d["title"], panels=[panel(p) for p in d["panels"]], footer=d["footer"],
                      alt=d.get("alt", ""), caption=d.get("caption", ""), kind=d.get("kind") or "3d",
                      size=tuple(d["size"]) if d.get("size") else None)
