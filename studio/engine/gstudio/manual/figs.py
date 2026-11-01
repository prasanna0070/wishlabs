"""Figure specifications: what each manual image shows. Pure data, no VTK.

Step planning builds these; `render3d` + `illustrate` turn them into PNGs. Keeping the plan separate
means the step text, the build.json camera hints and the images all come from one description.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .model import Vec, bbox_center, bbox_offset, bbox_size, bbox_union, vnorm

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
