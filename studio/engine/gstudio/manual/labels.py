"""Automatic leader-label placement with collision avoidance (pure geometry, no rendering).

Each label has a 2D anchor (a projected 3D point on the part) and a measured box size. Candidates are
the two side columns of the panel plus rings around the anchor. A greedy pass with several orderings
keeps only candidates that satisfy the hard rules, and the cheapest feasible layout wins:

  * no label box overlaps another label box;
  * no leader line crosses another leader or passes through another label's box;
  * no label box covers any anchor dot;
  * every box stays inside the panel.

Soft costs prefer short leaders, tidy side columns, and boxes that do not cover the highlighted parts.
`audit()` re-checks a finished layout independently; the manual summary reports its count (target 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

try:  # numpy is optional for the pure tests; the renderer always has it
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

Pt = tuple[float, float]


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def inflate(self, d: float) -> "Box":
        return Box(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def intersects(self, o: "Box", pad: float = 0.0) -> bool:
        return not (self.x1 + pad <= o.x0 or o.x1 + pad <= self.x0 or self.y1 + pad <= o.y0 or o.y1 + pad <= self.y0)

    def contains(self, p: Pt, pad: float = 0.0) -> bool:
        return self.x0 - pad <= p[0] <= self.x1 + pad and self.y0 - pad <= p[1] <= self.y1 + pad

    def inside(self, o: "Box") -> bool:
        return self.x0 >= o.x0 and self.y0 >= o.y0 and self.x1 <= o.x1 and self.y1 <= o.y1

    def clamp(self, p: Pt) -> Pt:
        return (min(max(p[0], self.x0), self.x1), min(max(p[1], self.y0), self.y1))


@dataclass
class LabelIn:
    anchor: Pt
    w: float
    h: float
    priority: int = 0
    key: str = ""


@dataclass
class Placed:
    idx: int
    box: Box
    anchor: Pt
    attach: Pt
    cost: float = 0.0
    forced: bool = False


@dataclass
class Layout:
    placed: list[Placed] = field(default_factory=list)
    cost: float = 0.0
    violations: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------- geometry
def _orient(a: Pt, b: Pt, c: Pt) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(p1: Pt, p2: Pt, q1: Pt, q2: Pt, eps: float = 1e-9) -> bool:
    """Proper or touching intersection of two segments, ignoring shared endpoints."""
    for a in (p1, p2):
        for b in (q1, q2):
            if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
                return False
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)):
        return True

    def on(a: Pt, b: Pt, c: Pt) -> bool:
        return min(a[0], b[0]) - eps <= c[0] <= max(a[0], b[0]) + eps and min(a[1], b[1]) - eps <= c[1] <= max(a[1], b[1]) + eps

    if abs(d1) <= eps and on(q1, q2, p1):
        return True
    if abs(d2) <= eps and on(q1, q2, p2):
        return True
    if abs(d3) <= eps and on(p1, p2, q1):
        return True
    if abs(d4) <= eps and on(p1, p2, q2):
        return True
    return False


def segment_hits_box(a: Pt, b: Pt, box: Box) -> bool:
    if box.contains(a) or box.contains(b):
        return True
    c = [(box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1)]
    return any(segments_cross(a, b, c[i], c[(i + 1) % 4]) for i in range(4))


def attach_point(box: Box, anchor: Pt) -> Pt:
    """Leader start: the point of the box closest to the anchor (straight leaders when aligned)."""
    return box.clamp(anchor)


def _dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --------------------------------------------------------------------------------------------- cost helpers
class MaskCost:
    """Fraction of a box covering 'important' pixels, via a summed-area table."""

    def __init__(self, mask: Optional["np.ndarray"], soft: Optional["np.ndarray"] = None):
        self.sat = self._sat(mask)
        self.sat_soft = self._sat(soft)

    @staticmethod
    def _sat(mask):
        if mask is None or np is None:
            return None
        m = np.asarray(mask, dtype=np.float64)
        return np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    @staticmethod
    def _frac(sat, box: Box) -> float:
        if sat is None:
            return 0.0
        h, w = sat.shape[0] - 1, sat.shape[1] - 1
        x0, x1 = int(max(0, min(w, box.x0))), int(max(0, min(w, box.x1)))
        y0, y1 = int(max(0, min(h, box.y0))), int(max(0, min(h, box.y1)))
        area = max(1, (x1 - x0) * (y1 - y0))
        s = sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]
        return float(s) / area

    def box(self, box: Box) -> tuple[float, float]:
        return self._frac(self.sat, box), self._frac(self.sat_soft, box)

    def line(self, a: Pt, b: Pt, n: int = 24) -> float:
        if self.sat is None:
            return 0.0
        hits = 0
        h, w = self.sat.shape[0] - 1, self.sat.shape[1] - 1
        for i in range(n):
            t = (i + 0.5) / n
            x, y = int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t)
            if 0 <= x < w and 0 <= y < h:
                v = self.sat[y + 1, x + 1] - self.sat[y, x + 1] - self.sat[y + 1, x] + self.sat[y, x]
                hits += 1 if v > 0 else 0
        return hits / n


# --------------------------------------------------------------------------------------------- placement
def candidates(lab: LabelIn, panel: Box, margin: float) -> list[tuple[Box, float]]:
    """(box, base_cost) candidates for one label."""
    out: list[tuple[Box, float]] = []
    w, h = lab.w, lab.h
    ax, ay = lab.anchor
    ymin, ymax = panel.y0 + margin, panel.y1 - margin - h
    xl, xr = panel.x0 + margin, panel.x1 - margin - w
    step = 9.0
    if ymax >= ymin:
        n = int((ymax - ymin) // step) + 1
        for side, x in (("L", xl), ("R", xr)):
            if xr < xl:
                continue
            for k in range(n):
                y = ymin + k * step
                out.append((Box(x, y, x + w, y + h), -0.10))
    for r in (46.0, 80.0, 120.0, 170.0, 230.0, 300.0, 380.0):
        for k in range(20):
            t = 2 * math.pi * k / 20
            dx, dy = math.cos(t), math.sin(t)
            # place the box so its nearest edge is r away from the anchor along (dx, dy)
            cx = ax + dx * (r + w / 2 * abs(dx))
            cy = ay + dy * (r + h / 2 * abs(dy))
            b = Box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            out.append((b, 0.0))
    return out


def _feasible(box: Box, anchor: Pt, idx: int, placed: Sequence[Placed], anchors: Sequence[Pt], panel: Box,
              margin: float, gap: float, dot: float) -> bool:
    if not box.inside(panel.inflate(-margin * 0.5)):
        return False
    for j, a in enumerate(anchors):
        if box.contains(a, pad=dot):
            return False
    att = attach_point(box, anchor)
    for p in placed:
        if box.intersects(p.box, pad=gap):
            return False
        if segment_hits_box(p.attach, p.anchor, box.inflate(2.0)):
            return False
        if segment_hits_box(att, anchor, p.box.inflate(2.0)):
            return False
        if segments_cross(att, anchor, p.attach, p.anchor):
            return False
    return True


def _cost(box: Box, lab: LabelIn, base: float, panel: Box, mc: MaskCost) -> float:
    att = attach_point(box, lab.anchor)
    L = _dist(att, lab.anchor)
    diag = math.hypot(panel.w, panel.h)
    hard, soft = mc.box(box)
    c = base + 1.0 * L / diag + 6.0 * hard + 1.4 * soft + 0.25 * mc.line(att, lab.anchor)
    if L < 18:
        c += 0.2  # too short to read as a leader
    return c


def place(labels: Sequence[LabelIn], panel: Box, mask=None, soft_mask=None, margin: float = 14.0,
          gap: float = 8.0, dot: float = 7.0, orders: int = 6, restarts: int = 48, seed: int = 11) -> Layout:
    """Place every label; returns the best layout found (violations listed if a label had to be forced).

    Deterministic orderings first, then seeded random restarts while any rule is still broken; each
    greedy result gets a swap-repair pass that exchanges the boxes of labels whose leaders cross.
    """
    import random

    if not labels:
        return Layout()
    mc = MaskCost(mask, soft_mask)
    anchors = [l.anchor for l in labels]
    scored = [sorted(((_cost(b, labels[i], base, panel, mc), b) for b, base in candidates(labels[i], panel, margin)),
                     key=lambda t: t[0]) for i in range(len(labels))]
    idxs = list(range(len(labels)))
    order_list = [
        sorted(idxs, key=lambda i: (-labels[i].priority, labels[i].anchor[1])),
        sorted(idxs, key=lambda i: labels[i].anchor[1]),
        sorted(idxs, key=lambda i: -labels[i].anchor[1]),
        sorted(idxs, key=lambda i: labels[i].anchor[0]),
        sorted(idxs, key=lambda i: -labels[i].anchor[0]),
        sorted(idxs, key=lambda i: (-labels[i].w * labels[i].h)),
    ][: max(1, orders)]
    rng = random.Random(seed)
    best: Optional[Layout] = None
    best_score = float("inf")
    k = 0
    while k < len(order_list) + restarts:
        order = order_list[k] if k < len(order_list) else rng.sample(idxs, len(idxs))
        k += 1
        placed = _greedy(order, labels, scored, anchors, panel, margin, gap, dot)
        placed = _repair(placed, labels, panel, dot)
        placed = _relocate(placed, labels, scored, anchors, panel, margin, gap, dot)
        nv = len(audit(placed, panel, dot=dot))
        score = nv * 1000 + sum(p.cost for p in placed)
        if score < best_score:
            best, best_score = Layout(placed=sorted(placed, key=lambda p: p.idx), cost=score), score
        if best_score < 1000 and k >= len(order_list):
            break
        if best_score < 1000 and len(labels) <= 3:
            break
    assert best is not None
    best.violations = audit(best.placed, panel, dot=dot)
    return best


def _greedy(order, labels, scored, anchors, panel, margin, gap, dot) -> list[Placed]:
    placed: list[Placed] = []
    for i in order:
        lab = labels[i]
        chosen = None
        for c, b in scored[i]:
            if _feasible(b, lab.anchor, i, placed, anchors, panel, margin, gap, dot):
                chosen = Placed(idx=i, box=b, anchor=lab.anchor, attach=attach_point(b, lab.anchor), cost=c)
                break
        if chosen is None:
            c, b = min(scored[i][:400], key=lambda t: t[0] + 100 * _conflicts(t[1], lab.anchor, placed, anchors, panel, dot))
            chosen = Placed(idx=i, box=b, anchor=lab.anchor, attach=attach_point(b, lab.anchor), cost=c + 50, forced=True)
        placed.append(chosen)
    return placed


def _involved(v: str) -> set[int]:
    import re as _re

    return {int(x) for x in _re.findall(r"(?:label|labels|leader|leaders|anchor) (\d+)", v)} | \
        {int(x) for x in _re.findall(r"and (\d+)", v)}


def _relocate(placed: list[Placed], labels, scored, anchors, panel: Box, margin, gap, dot, rounds: int = 4) -> list[Placed]:
    """Local search: move any label involved in a violation to its cheapest spot that is legal against the rest."""
    cur = {p.idx: p for p in placed}
    for _ in range(rounds):
        viol = audit(list(cur.values()), panel, dot=dot)
        if not viol:
            break
        moved = False
        for idx in sorted({i for v in viol for i in _involved(v)}):
            if idx not in cur:
                continue
            others = [p for k, p in cur.items() if k != idx]
            for c, b in scored[idx]:
                if _feasible(b, labels[idx].anchor, idx, others, anchors, panel, margin, gap, dot):
                    cand = Placed(idx=idx, box=b, anchor=labels[idx].anchor, attach=attach_point(b, labels[idx].anchor), cost=c)
                    trial = others + [cand]
                    if len(audit(trial, panel, dot=dot)) < len(audit(list(cur.values()), panel, dot=dot)):
                        cur[idx] = cand
                        moved = True
                    break
        if not moved:
            break
    return list(cur.values())


def _moved(p: Placed, lab: LabelIn, ref: Box) -> Placed:
    """Label `lab` moved onto the slot of box `ref` (same left/right column edge, same top)."""
    if abs(ref.x1 - p.box.x1) < abs(ref.x0 - p.box.x0):
        x0 = ref.x1 - lab.w
    else:
        x0 = ref.x0
    b = Box(x0, ref.y0, x0 + lab.w, ref.y0 + lab.h)
    return Placed(idx=p.idx, box=b, anchor=p.anchor, attach=attach_point(b, p.anchor), cost=p.cost)


def _repair(placed: list[Placed], labels, panel: Box, dot: float, rounds: int = 30) -> list[Placed]:
    cur = list(placed)
    nv = len(audit(cur, panel, dot=dot))
    for _ in range(rounds):
        if nv == 0:
            break
        improved = False
        for a in range(len(cur)):
            for b in range(a + 1, len(cur)):
                pa, pb = cur[a], cur[b]
                if not segments_cross(pa.attach, pa.anchor, pb.attach, pb.anchor) and not segment_hits_box(pa.attach, pa.anchor, pb.box) \
                        and not segment_hits_box(pb.attach, pb.anchor, pa.box):
                    continue
                trial = list(cur)
                trial[a] = _moved(pa, labels[pa.idx], pb.box)
                trial[b] = _moved(pb, labels[pb.idx], pa.box)
                tv = len(audit(trial, panel, dot=dot))
                if tv < nv:
                    cur, nv, improved = trial, tv, True
        if not improved:
            break
    return cur


def _conflicts(box: Box, anchor: Pt, placed: Sequence[Placed], anchors: Sequence[Pt], panel: Box, dot: float) -> int:
    n = 0 if box.inside(panel) else 1
    n += sum(1 for a in anchors if box.contains(a, pad=dot))
    att = attach_point(box, anchor)
    for p in placed:
        n += int(box.intersects(p.box)) + int(segment_hits_box(p.attach, p.anchor, box)) + int(segments_cross(att, anchor, p.attach, p.anchor))
    return n


def audit(placed: Sequence[Placed], panel: Optional[Box] = None, dot: float = 4.0) -> list[str]:
    """Independent check of a finished layout. Returns human-readable violations (empty = clean)."""
    v: list[str] = []
    for i, a in enumerate(placed):
        if panel is not None and not a.box.inside(panel):
            v.append(f"label {a.idx} outside panel")
        for j, b in enumerate(placed):
            if j <= i:
                continue
            if a.box.intersects(b.box):
                v.append(f"labels {a.idx} and {b.idx} overlap")
            if segments_cross(a.attach, a.anchor, b.attach, b.anchor):
                v.append(f"leaders {a.idx} and {b.idx} cross")
        for b in placed:
            if b is a:
                continue
            if segment_hits_box(b.attach, b.anchor, a.box):
                v.append(f"leader {b.idx} crosses label {a.idx}")
            if a.box.contains(b.anchor, pad=dot):
                v.append(f"label {a.idx} covers anchor {b.idx}")
        if a.box.contains(a.anchor, pad=dot) and _dist(a.attach, a.anchor) < 1.0:
            v.append(f"label {a.idx} covers its own anchor")
    return v
