"""Cosmetic relief projected along +Y onto the body surface, plus ears.

Technique: for a decor footprint F (front-view polygon), clip the outer solid to a local box L, then
  engrave:  tool = prism(F) & (L - L shifted +Y by depth)   -> removed (a layer exactly `depth` thick along Y)
  emboss:   tool = prism(F) & (L shifted -Y by depth - L)   -> added
This follows any surface shape (flat front, fillet bands, revolved or blob fronts) without offsetting surfaces.
Behind each decor item a cavity "pad" keeps >= 1.2 mm of skin under the deepest relief on curved bodies.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

import cadquery as cq
import numpy as np
from shapely import affinity
from shapely.geometry import Point, Polygon

from . import shapes2d
from .geom import V, largest_solid, poly_face, prism, sphere, try_fillet

MIN_SKIN = 1.2
FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


@dataclass
class DecorRecord:
    type: str
    color: Optional[str]
    relief: str
    tool: cq.Shape
    poly_xz: Polygon
    depth: float
    label: str = ""


@dataclass
class DecorResult:
    outer: cq.Shape
    records: list = field(default_factory=list)
    pads: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    ears: list = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ 2D footprints
def footprint(d: dict) -> Optional[Polygon]:
    t = d["type"]
    x, z = float(d["at"][0]), float(d["at"][1])
    if t == "text":
        n = len(d["text"])
        h = float(d.get("height", 8))
        return affinity.translate(shapes2d.rounded_rect(max(n * h * 0.72, h), h * 1.25, 1.0), x, z)
    w, h = float(d["size"][0]), float(d["size"][1])
    if t == "face_plate":
        p = shapes2d.face_plate(d.get("shape", "circle"), w, h)
    elif t == "eye":
        p = shapes2d.eye(d.get("style", "dot"), w, h)
    elif t == "mouth":
        p = shapes2d.mouth(d.get("style", "smile"), w, h)
    elif t == "blush":
        p = shapes2d.ellipse(w, h)
    else:
        return None
    return affinity.translate(p, x, z)


def expand_decor(decor: list) -> list:
    """Mirror blush 'both' into two entries; leave ears for apply_ears."""
    out = []
    for d in decor:
        if d["type"] == "blush" and d.get("side") == "both":
            out.append({**d, "side": "single"})
            out.append({**d, "side": "single", "at": [-float(d["at"][0]), d["at"][1]]})
        else:
            out.append(d)
    return out


def decor_stack(decor: list) -> float:
    """Deepest engraved stack (face-plate recess + relief inside it), used to size the front skin."""
    items = expand_decor([d for d in decor if d["type"] != "ear"])
    plates = [(footprint(d), float(d.get("depth", 1.2))) for d in items if d["type"] == "face_plate" and d.get("relief", "engrave") == "engrave"]
    best = 0.0
    for d in items:
        if d.get("relief", "engrave") != "engrave":
            continue
        dep = float(d.get("depth", 0.8))
        if d["type"] != "face_plate":
            fp = footprint(d)
            for pp, pd in plates:
                if fp is not None and pp is not None and pp.contains(fp.centroid):
                    dep += pd
                    break
        best = max(best, dep)
    return best


# ------------------------------------------------------------------------------------------------ projection helpers
def _text_poly(d: dict) -> Polygon:
    """Footprint of the real glyphs (used for surface sampling)."""
    P = _text_solid(d, d["at"][0], d["at"][1], 0.0, 1.0)
    if P is None:
        return footprint(d)
    b = P.BoundingBox()
    return Polygon([(b.xmin - 0.5, b.zmin - 0.5), (b.xmax + 0.5, b.zmin - 0.5), (b.xmax + 0.5, b.zmax + 0.5), (b.xmin - 0.5, b.zmax + 0.5)])


def _front_hits(probe, poly: Polygon):
    pts = shapes2d.footprint_points(poly, 24)
    o = np.stack([pts[:, 0], np.full(len(pts), -1000.0), pts[:, 1]], 1)
    hits, _, _ = probe.hits(o, np.array([[0, 1.0, 0]]))
    ys = hits[:, 1]
    ys = ys[np.isfinite(ys)]
    return ys


def _plane(x, ys_back, z) -> cq.Plane:
    # local x -> +X, local y -> +Z, local z -> -Y (toward the viewer)
    return cq.Plane(origin=V(x, ys_back, z), xDir=V(1, 0, 0), normal=V(0, -1, 0))


def _prism_xz(poly_local: Polygon, x, z, y_back, length) -> Optional[cq.Shape]:
    f = poly_face(poly_local)
    if f is None:
        return None
    return prism(f, 0, length).moved(cq.Location(_plane(x, y_back, z)))


def _text_solid(d: dict, x, z, y_back, length) -> Optional[cq.Shape]:
    h = float(d.get("height", 8))
    fp = next((p for p in FONT_PATHS if os.path.exists(p)), None)
    try:
        wp = cq.Workplane(_plane(x, y_back, z)).text(d["text"], h / 0.73, length, combine=False, halign="center",
                                                   valign="center", fontPath=fp, kind="bold")
        sols = [s for s in wp.vals() if isinstance(s, cq.Shape)]
        return cq.Compound.makeCompound(sols) if sols else None
    except Exception:
        return None


def _layer_tool(outer, poly: Polygon, d: dict, ys, extra_back: float):
    """Engrave/emboss tool for one decor item; returns (tool, relief, depth)."""
    depth = float(d.get("depth", 0.8 if d["type"] != "face_plate" else 1.2))
    relief = d.get("relief", "engrave")
    yf_min, yf_max = float(ys.min()), float(ys.max())
    if yf_max - yf_min < 1e-3 and d["type"] != "text":
        # planar patch: the relief is a straight prism, no layer booleans needed
        minx, minz, maxx, maxz = poly.bounds
        cx, cz = (minx + maxx) / 2, (minz + maxz) / 2
        local = affinity.translate(poly, -cx, -cz)
        y0 = yf_min + extra_back  # the current surface: the front plane, or the floor of an engraved face plate
        if relief == "emboss":
            P = _prism_xz(local, cx, cz, y0, depth)
        else:
            P = _prism_xz(local, cx, cz, y0 + depth, depth)
        return P, relief, depth
    y0 = yf_min - depth - 2.0
    y1 = yf_max + depth + extra_back + 2.0
    minx, minz, maxx, maxz = poly.bounds
    if d["type"] == "text":  # size the clip from the real glyphs (bold fonts run wider than the estimate)
        P0 = _text_solid(d, d["at"][0], d["at"][1], y1, y1 - y0)
        if P0 is not None:
            tb = P0.BoundingBox()
            minx, maxx = min(minx, tb.xmin - 1.0), max(maxx, tb.xmax + 1.0)
            minz, maxz = min(minz, tb.zmin - 1.0), max(maxz, tb.zmax + 1.0)
    clip = cq.Solid.makeBox(maxx - minx + 2, y1 - y0, maxz - minz + 2, V(minx - 1, y0, minz - 1))
    L = outer.intersect(clip)
    if L.Volume() < 1e-6:
        return None, relief, depth
    cx, cz = (minx + maxx) / 2, (minz + maxz) / 2
    local = affinity.translate(poly, -cx, -cz)
    if d["type"] == "text":
        P = _text_solid(d, d["at"][0], d["at"][1], y1, y1 - y0)
    else:
        P = _prism_xz(local, cx, cz, y1, y1 - y0)
    if P is None:
        return None, relief, depth
    if relief == "emboss":
        layer = L.translate(V(0, -depth, 0)).cut(L)
    else:
        layer = L.cut(L.translate(V(0, depth, 0)))
    tool = P.intersect(layer)
    return tool, relief, depth


DEFAULT_COLOR = {"face_plate": None, "eye": "detail", "mouth": "detail", "blush": "blush", "text": "detail", "ear": None}


def apply_relief(outer: cq.Shape, decor: list, probe, cavity_pads: bool, wall: float) -> DecorResult:
    """Face plates first (one by one: relief inside them reads their floor), then every other item grouped by
    (relief, depth, plate depth, colour) so each group costs one layer computation and one boolean."""
    from shapely.ops import unary_union

    res = DecorResult(outer=outer)
    items = expand_decor([d for d in decor if d["type"] != "ear"])
    plates = []
    cur = outer
    groups: dict = {}
    order = sorted(items, key=lambda d: 0 if d["type"] == "face_plate" else 1)
    for k, d in enumerate(order):
        poly = _text_poly(d) if d["type"] == "text" else footprint(d)
        if poly is None or poly.is_empty:
            continue
        ys = _front_hits(probe, poly)
        if len(ys) < 3:
            res.warnings.append(f"decor {d['type']} at {list(d['at'])} is not on the front surface; skipped")
            continue
        inside_plate = next((pd for pp, pd in plates if pp.contains(poly.centroid)), 0.0)
        color = d.get("color") or DEFAULT_COLOR.get(d["type"])
        if d["type"] == "face_plate" or d["type"] == "text":
            n0 = len(res.records)
            _apply_one(res, d, poly, ys, inside_plate, color, cavity_pads, wall, outer, cur_ref := [cur])
            if len(res.records) > n0 and "_idx" in d:
                res.records[-1].idx = [d["_idx"]]
            cur = cur_ref[0]
            if d["type"] == "face_plate" and d.get("relief", "engrave") == "engrave":
                plates.append((poly, float(d.get("depth", 1.2))))
            continue
        key = (d.get("relief", "engrave"), float(d.get("depth", 0.8)), inside_plate, color)
        g = groups.setdefault(key, {"polys": [], "ys": [], "items": []})
        d = {**d}
        g["polys"].append(poly)
        g["ys"].append(ys)
        g["items"].append(d)
    for (relief, depth, inside_plate, color), g in groups.items():
        poly = unary_union(g["polys"])
        ys = np.concatenate(g["ys"])
        d = {**g["items"][0], "relief": relief, "depth": depth}
        ref = [cur]
        _apply_one(res, d, poly, ys, inside_plate, color, cavity_pads, wall, outer, ref,
                   label=", ".join(sorted({x.get("style", x["type"]) for x in g["items"]})), count=len(g["items"]))
        if res.records and res.records[-1].tool is not None:
            res.records[-1].idx = sorted({x["_idx"] for x in g["items"] if "_idx" in x})
        cur = ref[0]
    cur = cur.clean()
    res.outer = largest_solid(cur) if len(cur.Solids()) > 1 else cur
    return res


def _apply_one(res, d, poly, ys, inside_plate, color, cavity_pads, wall, outer0, cur_ref, label=None, count=1):
    cur = cur_ref[0]
    try:
        tool, relief, depth = _layer_tool(cur, poly, d, ys, inside_plate)
    except Exception as e:  # pragma: no cover - defensive
        res.warnings.append(f"decor {d['type']} failed: {e}")
        return
    if tool is None or tool.Volume() < 1e-4:
        res.warnings.append(f"decor {d['type']} at {list(d['at'])} produced no relief; skipped")
        return
    cur_ref[0] = cur.fuse(tool) if relief == "emboss" else cur.cut(tool)
    rec = DecorRecord(d["type"], color, relief, tool, poly, depth, label or d.get("style", d.get("text", "")))
    rec.count = count
    res.records.append(rec)
    if cavity_pads and relief == "engrave":
        stack = depth + inside_plate + MIN_SKIN
        if stack > wall - 0.05:
            pad = _pad(outer0, poly.buffer(2.0), ys, stack)
            if pad is not None:
                res.pads.append(pad)


def _pad(outer0, poly: Polygon, ys, stack: float):
    y0 = float(ys.min()) - 2.0
    y1 = float(ys.max()) + stack + 3.0
    minx, minz, maxx, maxz = poly.bounds
    clip = cq.Solid.makeBox(maxx - minx + 2, y1 - y0, maxz - minz + 2, V(minx - 1, y0, minz - 1))
    L = outer0.intersect(clip)
    if L.Volume() < 1e-6:
        return None
    layer = L.cut(L.translate(V(0, stack, 0)))
    cx, cz = (minx + maxx) / 2, (minz + maxz) / 2
    P = _prism_xz(affinity.translate(poly, -cx, -cz), cx, cz, y1, y1 - y0)
    return P.intersect(layer) if P is not None else None


# ------------------------------------------------------------------------------------------------ ears
def _ear_positions(d: dict) -> list[float]:
    x = float(d["at"][0])
    side = d.get("side", "both")
    if side == "both":
        return [x, -x] if abs(x) > 1e-6 else [x]
    if side == "left":  # robot's left = +X (ARCHITECTURE §0)
        return [abs(x)]
    return [-abs(x)]


def apply_ears(outer: cq.Shape, decor: list, probe, depth_hint: float) -> DecorResult:
    res = DecorResult(outer=outer)
    adds = []
    for d in decor:
        if d["type"] != "ear":
            continue
        w, h = float(d["size"][0]), float(d["size"][1])
        style = d.get("style", "cat")
        # default thickness follows the ear, not the body: chunky but rounded, never a slab
        th = float(d.get("thickness") or float(np.clip(0.6 * w, 8.0, min(0.4 * depth_hint, 30.0))))
        for x in _ear_positions(d):
            hit, _, _ = probe.hits([[x, 0.0, 1000.0]], [[0, 0, -1.0]])
            if not np.isfinite(hit[0, 2]):
                res.warnings.append(f"ear at x={x:g} misses the top of the body; skipped")
                continue
            zs = float(hit[0, 2])
            if abs(zs - float(d["at"][1])) > 5:
                res.warnings.append(f"ear at x={x:g} moved from z={float(d['at'][1]):g} to the top surface z={zs:.0f}")
            sign = 1.0 if x >= 0 else -1.0
            tilt = -sign * float(d.get("tilt", 0.0))
            if style == "antenna":
                solid = _antenna(w, h)
            else:
                poly = shapes2d.ear(style, w, h)
                if sign < 0:
                    poly = affinity.scale(poly, -1, 1, origin=(0, 0))
                f = poly_face(poly)
                solid = prism(f, -th / 2, th / 2)
                r = min(th / 2 - 0.3, 0.35 * w, 9.0)
                edges = [e for e in solid.Edges() if abs(abs(e.Center().z) - th / 2) < 1e-3]
                solid, _ = try_fillet(solid, edges, r, min_r=0.8)
            if tilt:
                solid = solid.rotate(V(0, 0, 0), V(0, 0, 1), tilt)
            pl = cq.Plane(origin=V(x, 0.0, zs), xDir=V(1, 0, 0), normal=V(0, -1, 0))
            placed = solid.moved(cq.Location(pl))
            adds.append(placed)
            eb = placed.BoundingBox()
            res.ears.append({"style": style, "x": round(x, 1), "z_base": round(zs, 1), "idx": d.get("_idx"),
                             "bbox": [round(v, 2) for v in (eb.xmin, eb.xmax, eb.ymin, eb.ymax, eb.zmin, eb.zmax)]})
    if adds:
        res.outer = largest_solid(outer.fuse(*adds))
    return res


def _antenna(w: float, h: float) -> cq.Shape:
    rod_r = max(2.4, min(w * 0.24, 3.6))
    ball_r = w / 2
    top = h - ball_r
    rod = cq.Solid.makeCylinder(rod_r, top + 10, V(0, -10, 0), V(0, 1, 0))
    ball = sphere((0, top, 0), ball_r)
    base = cq.Solid.makeCylinder(rod_r * 2.4, 12, V(0, -10, 0), V(0, 1, 0))
    s = rod.fuse(ball, base).clean()
    edges = [e for e in s.Edges() if abs(e.Center().y - 2.0) < 0.05]
    s, _ = try_fillet(s, edges, rod_r * 0.9, min_r=0.5)
    return largest_solid(s)
