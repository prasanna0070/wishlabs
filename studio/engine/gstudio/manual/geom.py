"""Pure vector and bounding-box helpers (no gstudio imports), shared by the planner and the standalone renderer.

Coordinates are millimetres, +Z up, front faces -Y, +X is the gadget's left (ARCHITECTURE §0).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

Vec = tuple[float, float, float]

AXES: dict[Vec, str] = {
    (1.0, 0.0, 0.0): "+X (the gadget's left)",
    (-1.0, 0.0, 0.0): "-X (the gadget's right)",
    (0.0, -1.0, 0.0): "-Y (front)",
    (0.0, 1.0, 0.0): "+Y (rear)",
    (0.0, 0.0, 1.0): "+Z (up)",
    (0.0, 0.0, -1.0): "-Z (down)",
}


def vsub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vadd(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vscale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def vnorm(a: Vec) -> Vec:
    n = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def vdot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def dominant_axis(v: Vec) -> Vec:
    i = max(range(3), key=lambda k: abs(v[k]))
    out = [0.0, 0.0, 0.0]
    out[i] = 1.0 if v[i] >= 0 else -1.0
    return (out[0], out[1], out[2])


def axis_phrase(v: Vec) -> str:
    return AXES[dominant_axis(v)]


def axis_short(v: Vec) -> str:
    return axis_phrase(v).split(" ", 1)[0]


def rear_view(view_dir: Vec) -> bool:
    """A view whose camera sits behind the gadget (positive Y component): left and right appear swapped."""
    return vnorm(view_dir)[1] > 0.2


REAR_NOTE = "seen from the rear: the gadget's left (+X) is on your left"


def bbox_union(boxes: Iterable[Optional[tuple]]) -> Optional[tuple]:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return (min(b[0] for b in bs), max(b[1] for b in bs), min(b[2] for b in bs),
            max(b[3] for b in bs), min(b[4] for b in bs), max(b[5] for b in bs))


def bbox_offset(b: tuple, d: Vec) -> tuple:
    return (b[0] + d[0], b[1] + d[0], b[2] + d[1], b[3] + d[1], b[4] + d[2], b[5] + d[2])


def bbox_center(b: tuple) -> Vec:
    return ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)


def bbox_size(b: tuple) -> Vec:
    return (b[1] - b[0], b[3] - b[2], b[5] - b[4])


def bbox_gap(a: tuple, b: tuple) -> float:
    """Euclidean gap between two boxes (0 if they touch or overlap)."""
    dx = max(0.0, max(a[0], b[0]) - min(a[1], b[1]))
    dy = max(0.0, max(a[2], b[2]) - min(a[3], b[3]))
    dz = max(0.0, max(a[4], b[4]) - min(a[5], b[5]))
    return math.sqrt(dx * dx + dy * dy + dz * dz)
