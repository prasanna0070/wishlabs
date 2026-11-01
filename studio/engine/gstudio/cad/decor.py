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
    adjustments: list = field(default_factory=list)  # decor changed to fit the flat face: {spec_path, requested, built, why}
    solids: list = field(default_factory=list)  # [(decor index, placed solid)] for ears (GLB pick nodes)


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


def _fit_scale(poly: Polygon, region: Polygon, lo: float = 0.3) -> float:
    """Largest uniform scale <= 1 (about the polygon's centroid) that keeps poly inside region; 0 if none >= lo."""
    c = poly.centroid
    if region.contains(poly):
        return 1.0
    if not region.contains(c):
        return 0.0
    a, b = lo, 1.0
    if not region.contains(affinity.scale(poly, a, a, origin=c)):
        return 0.0
    for _ in range(24):
        m = (a + b) / 2
        if region.contains(affinity.scale(poly, m, m, origin=c)):
            a = m
        else:
            b = m
    return a


def _fit_flat(res, d: dict, poly: Polygon, flat: Polygon):
    """Keep a relief footprint on the flat front face (REVIEW-1 MAJ-31). Face plates and text are scaled down about
    their centre; small items are clipped. Returns (d, poly) or (None, None) when the item cannot stay on the face."""
    if flat is None or flat.contains(poly):
        return d, poly
    sp = f"decor[{d['_idx']}]" if "_idx" in d else None
    t = d["type"]
    if t in ("face_plate", "text"):
        k = _fit_scale(poly, flat)
        if k <= 0.0:
            res.warnings.append(f"{t} at {list(d['at'])} does not fit on the flat front face; skipped")
            res.adjustments.append({"spec_path": sp, "type": t, "action": "skipped", "why": "outside the flat front face"})
            return None, None
        c = poly.centroid
        poly = affinity.scale(poly, k, k, origin=c)
        if t == "face_plate":
            req = [float(d["size"][0]), float(d["size"][1])]
            d = {**d, "size": [round(req[0] * k, 1), round(req[1] * k, 1)]}
            res.adjustments.append({"spec_path": sp, "type": t, "action": "scaled", "requested_mm": req, "built_mm": d["size"],
                                    "scale": round(k, 3), "why": "kept on the flat front face (outline inset by the edge round + 2 mm)"})
            res.warnings.append(f"face plate {req[0]:g} x {req[1]:g} mm scaled to {d['size'][0]:g} x {d['size'][1]:g} mm to stay on the flat front face")
        else:
            req = float(d.get("height", 8))
            d = {**d, "height": round(req * k, 2)}
            res.adjustments.append({"spec_path": sp, "type": t, "action": "scaled", "requested_height_mm": req, "built_height_mm": d["height"],
                                    "scale": round(k, 3), "why": "kept on the flat front face"})
            res.warnings.append(f"text '{d.get('text', '')}' scaled to {d['height']:g} mm high to stay on the flat front face")
        return d, poly
    q = poly.intersection(flat)
    if q.is_empty or q.area < 0.5 * poly.area:
        res.warnings.append(f"{t} at {list(d['at'])} runs off the flat front face; skipped")
        res.adjustments.append({"spec_path": sp, "type": t, "action": "skipped", "why": "mostly outside the flat front face"})
        return None, None
    from .probe import largest

    q = largest(q)
    res.adjustments.append({"spec_path": sp, "type": t, "action": "clipped", "kept_fraction": round(q.area / poly.area, 3),
                            "why": "clipped to the flat front face"})
    return d, q


def apply_relief(outer: cq.Shape, decor: list, probe, cavity_pads: bool, wall: float, flat_region: Optional[Polygon] = None) -> DecorResult:
    """Face plates first (one by one: relief inside them reads their floor), then every other item grouped by
    (relief, depth, plate depth, colour) so each group costs one layer computation and one boolean.
    flat_region (silhouette bodies): every footprint is kept inside it, so no relief rides the edge round."""
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
        d, poly = _fit_flat(res, d, poly, flat_region)
        if d is None:
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


def silhouette_poly(probe) -> Polygon:
    """Front-view body silhouette (x, z): the body's section at y = 0 (exact for silhouette bodies)."""
    from .probe import largest

    return largest(probe.section("y", 0.0))


def half_width(sil: Polygon, z: float) -> Optional[float]:
    from shapely.geometry import LineString

    inter = sil.intersection(LineString([(-1e4, z), (1e4, z)]))
    return None if inter.is_empty else float(inter.bounds[2])


def side_top(sil: Polygon) -> float:
    """Height where the right side wall ends: above it the outline turns more horizontal than 45 deg (the top)."""
    minx, minz, maxx, maxz = sil.bounds
    zs = np.arange(max(minz + 2.0, 1.0), maxz, 1.0)
    W = [half_width(sil, z) for z in zs]
    zt = float(zs[0]) if len(zs) else maxz
    for z, a, b in zip(zs[:-1], W[:-1], W[1:]):
        if a is None or b is None:
            break
        if b - a < -1.0 and z > minz + 0.3 * (maxz - minz):
            return float(z)
        zt = float(z)
    return zt


def ear_envelope(sil: Polygon, z_side: float, margin: float = 2.0):
    """E(z): the largest |x| an ear may reach at height z = outline_x(min(z, side_top)) - margin (REVIEW-1 MAJ-30)."""
    w_top = half_width(sil, z_side) or 0.0

    def E(z):
        w = half_width(sil, z) if z <= z_side else w_top
        return (w if w is not None else w_top) - margin

    return E


def _surface_normal_at(probe, x: float):
    """Top-surface point above x and its front-view outward normal (nx, nz) from neighbouring rays."""
    xs = [x - 1.5, x, x + 1.5]
    hit, _, _ = probe.hits([[xx, 0.0, 1000.0] for xx in xs], [[0, 0, -1.0]])
    if not np.isfinite(hit[1, 2]):
        return None, None
    zs = hit[:, 2]
    if np.isfinite(zs[0]) and np.isfinite(zs[2]):
        slope = (zs[2] - zs[0]) / 3.0
    else:
        slope = 0.0
    n = np.array([-slope, 1.0])
    n /= np.linalg.norm(n)
    return float(zs[1]), n


def _placed_poly(poly: Polygon, phi: float, x: float, z: float, sink: float, n) -> Polygon:
    q = affinity.rotate(poly, phi, origin=(0, 0))
    return affinity.translate(q, x - n[0] * sink, z - n[1] * sink)


def _sink_depth(probe, x, z, n, w) -> float:
    """How far the ear base line (perpendicular to n at the foot) must sink so its ends are inside the body."""
    t = np.array([n[1], -n[0]])  # tangent
    worst = 0.0
    for a in (-0.45 * w, -0.3 * w, 0.3 * w, 0.45 * w):
        p = np.array([x, z]) + t * a
        o = [[p[0] + n[0] * 30.0, 0.0, p[1] + n[1] * 30.0]]
        hit, _, dist = probe.hits(o, [[-n[0], 0.0, -n[1]]])
        if np.isfinite(dist[0]):
            worst = max(worst, float(dist[0]) - 30.0)
    return worst + 0.8


def apply_ears(outer: cq.Shape, decor: list, probe, depth_hint: float) -> DecorResult:
    """Ears sit on the top surface tilted to the outline normal at their foot; only the visible part (v >= 0 of the
    ear outline, sunk until its base is inside the body) is added, so no stem or neck can poke out of the side wall.
    The ear's x is moved inward until its outboard edge stays inside outline_x(z) - 2 mm (ear_envelope)."""
    res = DecorResult(outer=outer)
    adds = []
    sil = silhouette_poly(probe)
    z_side = side_top(sil) if not sil.is_empty else 0.0
    E = ear_envelope(sil, z_side) if not sil.is_empty else None
    res.silhouette = sil
    for d in decor:
        if d["type"] != "ear":
            continue
        w, h = float(d["size"][0]), float(d["size"][1])
        style = d.get("style", "cat")
        rnd = d.get("roundness")
        th = float(d.get("thickness") or float(np.clip(0.6 * w, 8.0, min(0.4 * depth_hint, 30.0))))
        vis0 = shapes2d.ear_visible(style, w, h, rnd)
        if style == "antenna":  # the 3D antenna: ball + rod (rod_r) + base disc (2.4 rod_r, 2 mm above the base line)
            rod_r = max(2.4, min(w * 0.24, 3.6))
            ball = Point(0.0, h - w / 2).buffer(w / 2 + 0.2, quad_segs=32)
            rod = Polygon([(-rod_r - 0.2, 0.0), (rod_r + 0.2, 0.0), (rod_r + 0.2, h - w / 2), (-rod_r - 0.2, h - w / 2)])
            base_d = Polygon([(-2.4 * rod_r - 0.2, 0.0), (2.4 * rod_r + 0.2, 0.0), (2.4 * rod_r + 0.2, 2.2), (-2.4 * rod_r - 0.2, 2.2)])
            vis0 = ball.union(rod).union(base_d)
        for x_req in _ear_positions(d):
            sign = 1.0 if x_req >= 0 else -1.0
            vis_l = vis0 if sign > 0 else affinity.scale(vis0, -1, 1, origin=(0, 0))
            user_tilt = -sign * float(d.get("tilt", 0.0))
            best = None
            x_min = 0.0 if abs(x_req) < 1e-6 else (w / 2 + 1.0)
            xs = [x_req] if abs(x_req) < 1e-6 else [sign * xx for xx in np.arange(abs(x_req), x_min - 1e-6, -1.0)]
            for x in xs:
                zs, n = _surface_normal_at(probe, x)
                if zs is None:
                    continue
                phi = -math.degrees(math.atan2(n[0], n[1])) + user_tilt
                sink = _sink_depth(probe, x, zs, n, w)
                P = _placed_poly(vis_l, phi, x, zs, sink, n)
                excess = 0.0
                if E is not None:
                    minx, minz, maxx, maxz = P.bounds
                    for z in np.arange(minz, maxz + 0.5, 1.0):
                        seg = P.intersection(Polygon([(-1e4, z - 0.25), (1e4, z - 0.25), (1e4, z + 0.25), (-1e4, z + 0.25)]))
                        if seg.is_empty:
                            continue
                        reach = max(abs(seg.bounds[0]), abs(seg.bounds[2]))
                        excess = max(excess, reach - E(z))
                cand = {"x": x, "z": zs, "n": n, "phi": phi, "sink": sink, "poly": P, "excess": excess}
                if best is None or excess < best["excess"] - 1e-9:
                    best = cand
                if excess <= 0.0:
                    best = cand
                    break
            if best is None:
                res.warnings.append(f"ear at x={x_req:g} misses the top of the body; skipped")
                continue
            if best["excess"] > 0.0:
                res.warnings.append(f"ear at x={x_req:g}: could not keep it inside the side outline (sticks out {best['excess']:.1f} mm)")
            elif abs(best["x"] - x_req) > 0.5:
                res.warnings.append(f"ear at x={x_req:g} moved to x={best['x']:g} so it stays inside the body outline (2 mm margin)")
            x, zs, n, phi, sink = best["x"], best["z"], best["n"], best["phi"], best["sink"]
            if style == "antenna":
                solid = _antenna(w, h)
            else:
                poly = shapes2d.ear(style, w, h, rnd)
                if sign < 0:
                    poly = affinity.scale(poly, -1, 1, origin=(0, 0))
                solid = prism(poly_face(poly), -th / 2, th / 2)
                r = min(th / 2 - 0.3, 0.35 * w, 9.0)
                edges = [e for e in solid.Edges() if abs(abs(e.Center().z) - th / 2) < 1e-3]
                solid, _ = try_fillet(solid, edges, r, min_r=0.8)
            # keep only the visible part (v >= 0); its base is sunk into the body below
            keep = cq.Solid.makeBox(8 * w + 40, 8 * h + 40, 4 * th + 40, V(-4 * w - 20, 0.0, -2 * th - 20))
            solid = solid.intersect(keep)
            solid = largest_solid(solid) if len(solid.Solids()) > 1 else solid
            if phi:
                solid = solid.rotate(V(0, 0, 0), V(0, 0, 1), phi)
            pl = cq.Plane(origin=V(x - n[0] * sink, 0.0, zs - n[1] * sink), xDir=V(1, 0, 0), normal=V(0, -1, 0))
            placed = solid.moved(cq.Location(pl))
            adds.append(placed)
            res.solids.append((d.get("_idx"), placed))
            eb = placed.BoundingBox()
            res.ears.append({"style": style, "x": round(x, 1), "x_requested": round(x_req, 1), "z_base": round(zs, 1), "idx": d.get("_idx"),
                             "tilt_deg": round(-phi * sign, 1), "sink_mm": round(sink, 2), "roundness": shapes2d.ear_roundness(style, rnd),
                             "outboard_excess_mm": round(best["excess"], 2),
                             "visible_xz": [[round(a, 2), round(b, 2)] for a, b in np.asarray(best["poly"].exterior.coords)[::2]],
                             "bbox": [round(v, 2) for v in (eb.xmin, eb.xmax, eb.ymin, eb.ymax, eb.zmin, eb.zmax)]})
    if adds:
        from .body import _fuse_blend

        cur = outer
        blends = []
        for a in adds:
            try:
                out, used = _fuse_blend(cur, a, 3.0, factors=(1.0, 0.7))
                if out is None or not out.isValid():
                    raise ValueError("invalid blend")
                cur, used = out, used
            except Exception:
                cur, used = cur.fuse(a).clean(), 0.0
            blends.append(round(used, 2))
        res.outer = largest_solid(cur)
        for e, b in zip(res.ears, blends):
            e["blend_r"] = b
        if any(b < 3.0 for b in blends):
            res.warnings.append(f"ear root blend radius reduced to {min(blends):g} mm (OpenCascade fillet limit)")
    res.side_top_z = z_side
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
