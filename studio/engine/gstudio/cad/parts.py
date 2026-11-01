"""Component and fastener records shared by the builders (no OCC work here)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cadquery as cq
import numpy as np

from .geom import Frame


@dataclass
class Part:
    name: str
    kind: str  # PRINT | PURCHASE | REFERENCE
    label: str
    color: tuple
    subparts: list = field(default_factory=list)  # [(subname, cq.Shape, color)]
    ref: Optional[str] = None
    module_id: Optional[str] = None
    feature_id: Optional[str] = None
    spec_path: Optional[str] = None
    mass_g: Optional[float] = None
    mass_source: str = "estimate"
    print_info: Optional[dict] = None  # {"frame": Frame|None, "up": "+z"|"-z"|..., "reason": str, "material": str}
    alpha: float = 1.0
    notes: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # merged into components.json (qty, size, items...)

    @property
    def shape(self) -> cq.Shape:
        shapes = [s for _, s, _ in self.subparts]
        if len(shapes) == 1:
            return shapes[0]
        return cq.Compound.makeCompound(shapes)

    def add(self, sub: str, shape: cq.Shape, color=None):
        if shape is None:
            return
        self.subparts.append((sub, shape, tuple(color) if color is not None else tuple(self.color)))


@dataclass
class Fastener:
    """A screw or insert. Points are world coords; axis points from the head toward the tip (insert: into the hole)."""

    kind: str  # screw | insert
    group: str
    head: np.ndarray
    axis: np.ndarray
    length: float
    d: float
    label: str = ""
    hole_depth: Optional[float] = None  # screw: depth of the threaded insert hole beyond the joint face
    joint: Optional[np.ndarray] = None  # screw: point where the clamped part meets the boss (insert top face)
    insert_len: Optional[float] = None
    clamp_t: Optional[float] = None


@dataclass
class Kit:
    feature_id: str
    kind: str
    ref: str
    frame: Frame
    adds: list = field(default_factory=list)  # world solids united into the enclosure
    cuts: list = field(default_factory=list)  # world solids removed from the enclosure
    parts: list = field(default_factory=list)  # Part
    fasteners: list = field(default_factory=list)  # Fastener
    anchors: dict = field(default_factory=dict)
    cable_end: Optional[tuple] = None  # (point, outward direction for the plug)
    keepouts: list = field(default_factory=list)  # world solids (module envelopes) for layout tests
    info: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    grille_holes: int = 0
