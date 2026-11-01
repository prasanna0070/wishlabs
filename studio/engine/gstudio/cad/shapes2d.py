"""2D decor outlines as shapely geometry in a local (u right, v up) plane, centred on (0, 0).

Shapes are built as polygons and turned into smooth B-spline faces by geom.poly_face, so engraved eyes,
mouths and ears have soft, rounded outlines instead of polygon facets.
"""

from __future__ import annotations

import math

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

RES = 32  # shapely quad segments


def ellipse(w: float, h: float, cx: float = 0.0, cy: float = 0.0) -> Polygon:
    return affinity.scale(Point(cx, cy).buffer(1.0, quad_segs=RES), w / 2, h / 2)


def rounded_rect(w: float, h: float, r: float) -> Polygon:
    r = max(0.0, min(r, w / 2 - 0.01, h / 2 - 0.01))
    core = Polygon([(-w / 2 + r, -h / 2 + r), (w / 2 - r, -h / 2 + r), (w / 2 - r, h / 2 - r), (-w / 2 + r, h / 2 - r)])
    return core.buffer(r, quad_segs=RES) if r > 0 else core


def _soften(p: Polygon, r: float) -> Polygon:
    """Round convex and concave corners by r (morphological open + close)."""
    if r <= 0:
        return p
    q = p.buffer(-r, quad_segs=RES).buffer(r, quad_segs=RES)
    q = q.buffer(r * 0.6, quad_segs=RES).buffer(-r * 0.6, quad_segs=RES)
    return q if not q.is_empty else p


def stroke_arc(w: float, h: float, s: float, up: bool) -> Polygon:
    """A stroked arc: 'U' (up=False, ends up, belly down) or 'n' (up=True) filling a w x h box, stroke s."""
    s = min(s, h * 0.9, w * 0.45)
    half = max(w / 2 - s / 2, 0.5)
    sag = max(h - s, 0.4)
    t = np.linspace(-math.pi / 2, math.pi / 2, 48)
    x = half * np.sin(t)
    y = sag / 2 - sag * np.cos(t)
    if up:
        y = -y
    return LineString(np.stack([x, y], 1)).buffer(s / 2, quad_segs=RES, cap_style="round")


def star(w: float, h: float) -> Polygon:
    R = min(w, h) / 2
    pts = []
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        r = R if k % 2 == 0 else R * 0.47
        pts.append((r * math.cos(a), r * math.sin(a) - R * 0.05))
    return _soften(Polygon(pts), R * 0.12)


def heart(w: float, h: float) -> Polygon:
    t = np.linspace(0, 2 * math.pi, 160, endpoint=False)
    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
    p = Polygon(np.stack([x, y], 1))
    minx, miny, maxx, maxy = p.bounds
    p = affinity.translate(p, -(minx + maxx) / 2, -(miny + maxy) / 2)
    p = affinity.scale(p, w / (maxx - minx), h / (maxy - miny), origin=(0, 0))
    return _soften(p, min(w, h) * 0.05)


def eye(style: str, w: float, h: float) -> Polygon:
    if style == "dot":
        d = min(w, h)
        return ellipse(d, d)
    if style == "oval":
        return ellipse(w, h)
    stroke = float(np.clip(0.3 * min(w, 2 * h), 1.5, 3.2))
    if style == "arc_closed":
        return stroke_arc(w, h, stroke, up=False)
    if style == "arc_happy":
        return stroke_arc(w, h, stroke, up=True)
    if style == "star":
        return star(w, h)
    if style == "heart":
        return heart(w, h)
    return ellipse(w, h)


def mouth(style: str, w: float, h: float) -> Polygon:
    stroke = float(np.clip(0.32 * h, 1.5, 3.0))
    if style == "smile":
        return stroke_arc(w, h, stroke, up=False)
    if style == "open":
        t = np.linspace(math.pi, 2 * math.pi, 64)
        pts = [(w / 2 * math.cos(a), h / 2 + h * math.sin(a)) for a in t]
        return _soften(Polygon(pts), min(w, h) * 0.12)
    if style == "small_o":
        return ellipse(w, h)
    if style == "cat":
        a = affinity.translate(stroke_arc(w / 2 + stroke / 2, h, stroke, up=False), -w / 4 + stroke / 4, 0)
        b = affinity.translate(stroke_arc(w / 2 + stroke / 2, h, stroke, up=False), w / 4 - stroke / 4, 0)
        return unary_union([a, b])
    return stroke_arc(w, h, stroke, up=False)


def face_plate(shape: str, w: float, h: float) -> Polygon:
    if shape == "circle":
        d = w
        return ellipse(d, d)
    if shape == "ellipse":
        return ellipse(w, h)
    return rounded_rect(w, h, min(w, h) * 0.28)


# Ear roundness (REVIEW-1 MAJ-30/MAJ-34): 0 = crisp/pointed outline, 1 = as round as the style allows. None = style default.
EAR_ROUNDNESS_DEFAULT = {"cat": 0.25, "bear": 0.6, "bunny": 0.5, "owl_tuft": 0.2, "antenna": 0.5}


def ear_roundness(style: str, roundness=None) -> float:
    r = EAR_ROUNDNESS_DEFAULT.get(style, 0.5) if roundness is None else float(roundness)
    return float(min(max(r, 0.0), 1.0))


def ear(style: str, w: float, h: float, roundness=None) -> Polygon:
    """Ear outline with its base centre at (0, 0) and the tip toward +v. Extends below v=0 (embedded neck).

    roundness 0..1: cat/owl_tuft corner and tip radius, bear disc aspect (1 = a full circle) and neck taper, bunny tip
    fullness. The part below v=0 is clipped to the body by decor.apply_ears, so it never shows."""
    e = max(h * 0.45, 8.0)
    r = ear_roundness(style, roundness)
    if style == "cat":
        p = Polygon([(-w / 2, -e), (w / 2, -e), (w * 0.42, 0.05 * h), (w * 0.06, h), (-w * 0.08, h * 0.98), (-w * 0.5, 0.05 * h)])
        return _soften(p, w * (0.08 + 0.32 * r))
    if style == "bear":
        dh = w * (0.8 + 0.2 * r)  # disc height: a flatter oval at low roundness, a circle at 1
        c = ellipse(w, dh, 0, h - dh / 2)
        neck_top = 0.32 * w * (1.15 - 0.35 * r)  # tapered neck: wide at the root, narrower under a rounder disc
        neck_root = 0.36 * w
        stem = Polygon([(-neck_root, -e), (neck_root, -e), (neck_top, h - dh / 2), (-neck_top, h - dh / 2)])
        return _soften(unary_union([c, stem]), w * 0.08)
    if style == "bunny":
        body = ellipse(w, h * 1.1 + e, 0, (h - e) / 2 + h * 0.03)
        if r > 0.5:  # fuller, rounder tip: blend with a circle at the top
            k = (r - 0.5) * 2.0
            tip = Point(0, h - w / 2).buffer(w / 2 * (0.8 + 0.2 * k), quad_segs=RES)
            body = _soften(unary_union([body, tip]), w * 0.1 * k + 0.01)
        return body
    if style == "owl_tuft":
        p = Polygon([(-w / 2, -e), (w / 2, -e), (w * 0.48, h * 0.35), (w * 0.30, h), (w * 0.05, h * 0.62), (-w * 0.30, h * 0.40), (-w * 0.5, 0)])
        return _soften(p, w * (0.06 + 0.30 * r))
    # antenna fallback outline (3D antenna is built separately)
    return ellipse(w, w, 0, h - w / 2).union(Polygon([(-1.6, -e), (1.6, -e), (1.6, h - w / 2), (-1.6, h - w / 2)]))


def ear_visible(style: str, w: float, h: float, roundness=None) -> Polygon:
    """The part of the ear meant to show (v >= 0): the 'disc footprint' the decor_inside_silhouette check allows."""
    p = ear(style, w, h, roundness)
    clip = Polygon([(-10 * w, 0.0), (10 * w, 0.0), (10 * w, 10 * h + 10), (-10 * w, 10 * h + 10)])
    q = p.intersection(clip)
    return q if not q.is_empty else p


def footprint_points(poly: Polygon, n: int = 24) -> np.ndarray:
    """Points on and inside a polygon used for surface sampling."""
    pts = [poly.centroid.coords[0]]
    ring = np.asarray(poly.exterior.coords)
    idx = np.linspace(0, len(ring) - 1, n, endpoint=False).astype(int)
    pts += [tuple(ring[i]) for i in idx]
    c = np.asarray(poly.centroid.coords[0])
    pts += [tuple(c + (ring[i] - c) * 0.5) for i in idx[::2]]
    return np.asarray(pts, float)
