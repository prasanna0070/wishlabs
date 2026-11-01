"""Body families: build the closed outer solid and the closed inner cavity.

silhouette  front-view spline outline extruded along Y, front and rear perimeter rounded (edge_round front,
            a smaller round at the rear so the rear cover reads as a cap)
revolve     half profile revolved about Z, optional squash along Y, rounded bottom edge
blob        union of filleted primitives with blended seams, cut flat at z=0, rounded bottom edge

Every family returns a BodyGeom with outer (no decor), cavity (closed, wall-thick inside outer) and info
(actual radii used, warnings). Splitting into shell / cover / base plate happens in enclosure.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cadquery as cq
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from shapely.geometry import Polygon, box as sbox

from .geom import V, halfspace_z, largest_solid, scale_xyz, sphere, try_fillet, wire_points_2d


@dataclass
class BodyGeom:
    family: str
    outer: cq.Shape
    cavity: cq.Shape
    wall: float
    floor_z: float  # top of the cavity floor (inner bottom) for rear_cover split
    skin_front: float
    r_front: float = 0.0
    r_rear: float = 0.0
    r_bottom: float = 0.0
    info: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def bb(self):
        return self.outer.BoundingBox()


# ================================================================================================ silhouette
def _outline_sequence(outline):
    pts = [(float(x), float(z)) for x, z in outline]
    n = len(pts)
    on = [abs(z) < 1e-3 for _, z in pts]
    s = next(i for i in range(n) if on[i] and not on[i - 1])  # first point of the bottom run
    e = s
    while on[(e + 1) % n]:
        e = (e + 1) % n
    left, right = pts[s], pts[e]
    if left[0] > right[0]:  # outline given clockwise in the bottom run sense; reverse
        pts = pts[::-1]
        return _outline_sequence(pts)
    seq = []
    k = e
    while True:
        seq.append(pts[k])
        if k == s:
            break
        k = (k + 1) % n
    return seq, left, right


def outline_face(outline, tangent_bottom: bool = False) -> cq.Face:
    """Front-view outline face in the XZ plane at y=0 (spline + straight bottom)."""
    seq, left, right = _outline_sequence(outline)
    vs = [V(x, 0, z) for x, z in seq]
    tries = [True, False] if tangent_bottom else [False]
    last = None
    for tang in tries:
        try:
            if tang:
                sp = cq.Edge.makeSpline(vs, tangents=[V(1, 0, 0), V(1, 0, 0)], scale=True)
            else:
                sp = cq.Edge.makeSpline(vs)
            line = cq.Edge.makeLine(V(left[0], 0, 0), V(right[0], 0, 0))
            w = cq.Wire.assembleEdges([sp, line])
            pts = wire_points_2d(w, "xz", 120)
            if Polygon(pts).is_valid and min(pts[:, 1]) > -0.5:
                return cq.Face.makeFromWires(w)
            last = cq.Face.makeFromWires(w)
        except Exception:
            continue
    if last is None:
        raise ValueError("could not build the outline spline")
    return last


def _offset_face(face: cq.Face, d: float) -> cq.Face:
    wires = face.outerWire().offset2D(d, "arc")
    faces = [cq.Face.makeFromWires(w) for w in wires]
    return max(faces, key=lambda f: f.Area())


def fix_shape(shape: cq.Shape) -> cq.Shape:
    if shape.isValid():
        return shape
    from OCP.ShapeFix import ShapeFix_Shape

    fx = ShapeFix_Shape(shape.wrapped)
    fx.Perform()
    out = cq.Shape.cast(fx.Shape())
    return out if out.isValid() else shape


def inner_outline_face(face: cq.Face, wall: float) -> cq.Face:
    """Inward offset of an outline face (XZ plane) as spline + straight bottom, via shapely."""
    from shapely.geometry.polygon import orient

    from .probe import largest

    pts = wire_points_2d(face.outerWire(), "xz", 240)
    P = orient(Polygon(pts).buffer(0), 1.0)
    I = orient(largest(P.buffer(-wall)), 1.0)
    ring = np.asarray(I.exterior.coords)[:-1]
    zb = ring[:, 1].min()
    on = np.abs(ring[:, 1] - zb) < 1e-3
    xs = ring[on, 0]
    xl, xr = float(xs.min()), float(xs.max())
    n = len(ring)
    ir = int(np.where(on)[0][np.argmax(ring[on, 0])])
    il = int(np.where(on)[0][np.argmin(ring[on, 0])])
    seq = []
    k = ir
    while True:
        seq.append(ring[k])
        if k == il:
            break
        k = (k + 1) % n
    seq = _resample_open(np.asarray(seq), 1.8)
    seq[0] = (xr, zb)
    seq[-1] = (xl, zb)
    sp = cq.Edge.makeSpline([V(x, 0, z) for x, z in seq])
    line = cq.Edge.makeLine(V(xl, 0, zb), V(xr, 0, zb))
    return cq.Face.makeFromWires(cq.Wire.assembleEdges([sp, line]))


def silhouette(body: dict, skin_front: float) -> BodyGeom:
    D = float(body["depth"])
    wall = float(body.get("wall", 2.4))
    er = float(body.get("edge_round", 8.0))
    warns = []
    face = outline_face(body["outline"])
    solid = cq.Solid.extrudeLinear(face.translate(V(0, -D / 2, 0)), V(0, D, 0))
    r_front_req = min(er, D / 2 - 2)
    r_rear_req = min(er, 6.0, D / 2 - 2)

    def fedges(s, y):
        return [e for e in s.Edges() if abs(e.Center().y - y) < 1e-3]

    solid, r_rear = try_fillet(solid, fedges(solid, D / 2), r_rear_req)
    solid, r_front = try_fillet(solid, fedges(solid, -D / 2), r_front_req)
    if r_front + 1e-6 < r_front_req:
        warns.append(f"edge_round {er:g} reduced to {r_front:.1f} mm on the front (OpenCascade fillet limit)")
    # cavity: inner offset of the outline, rebuilt as a clean B-spline (OCC offset curves extrude invalid)
    inner = inner_outline_face(face, wall)
    y0 = -D / 2 + skin_front
    y1 = D / 2 - wall
    cav = cq.Solid.extrudeLinear(inner.translate(V(0, y0, 0)), V(0, y1 - y0, 0))
    rif = max(r_front - max(skin_front, wall), 0.0)
    rir = max(r_rear - wall, 0.0)
    if rir > 0.6:
        cav, _ = try_fillet(cav, fedges(cav, y1), rir)
    if rif > 0.6:
        cav, _ = try_fillet(cav, fedges(cav, y0), rif)
    cav = fix_shape(cav)
    info = {"edge_round_requested": er, "edge_round_front": round(r_front, 2), "edge_round_rear": round(r_rear, 2),
            "depth": D}
    return BodyGeom("silhouette", solid, cav, wall, wall, skin_front, r_front, r_rear, 0.0, info, warns)


# ================================================================================================ revolve
def _profile_face(curve_pts, start_tangent=None) -> cq.Face:
    """Face in XZ (x=r) bounded by the axis and a spline from (r1,0)->...->(0,h) via curve_pts[0..]."""
    vs = [V(r, 0, z) for r, z in curve_pts]
    st = V(*start_tangent) if start_tangent is not None else (vs[1] - vs[0]).normalized()
    sp = cq.Edge.makeSpline(vs, tangents=[st, V(-1, 0, 0)], scale=True)
    r1, z1 = curve_pts[0]
    h = curve_pts[-1][1]
    edges = [sp, cq.Edge.makeLine(V(0, 0, h), V(0, 0, z1))]
    if r1 > 1e-6:
        edges.append(cq.Edge.makeLine(V(0, 0, z1), V(r1, 0, z1)))
    return cq.Face.makeFromWires(cq.Wire.assembleEdges(edges))


def _revolve(face: cq.Face) -> cq.Solid:
    return cq.Solid.revolve(face, 360.0, V(0, 0, 0), V(0, 0, 1))


def _half_curve(poly: Polygon) -> list[tuple[float, float]]:
    """Ordered boundary points of a half-section (r>=0) from the bottom axis point to the top axis point."""
    ring = np.asarray(poly.exterior.coords)[:-1]
    on = np.abs(ring[:, 0]) < 1e-6
    n = len(ring)
    # rotate so that we start at an axis point followed by an off-axis point
    start = next(i for i in range(n) if on[i] and not on[(i + 1) % n])
    seq = [ring[(start + k) % n] for k in range(n)]
    out = [seq[0]]
    for p in seq[1:]:
        out.append(p)
        if abs(p[0]) < 1e-6:
            break
    arr = np.asarray(out)
    if arr[0, 1] > arr[-1, 1]:
        arr = arr[::-1]
    return [(float(max(r, 0.0)), float(z)) for r, z in arr]


def _resample_open(pts, spacing):
    a = np.asarray(pts, float)
    seg = np.linalg.norm(np.diff(a, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    n = int(np.clip(cum[-1] / spacing, 12, 120))
    t = np.linspace(0, cum[-1], n)
    return np.stack([np.interp(t, cum, a[:, 0]), np.interp(t, cum, a[:, 1])], 1)


def _squash_loft(spline_edge, sq: float, z_floor: float) -> cq.Shape:
    """Solid bounded by z=z_floor and the surface x^2 + (y/sq)^2 = r(z)^2 with r(z) from a profile spline (r,0,z)."""
    from .geom import ellipse_wire, loft_sections

    L = spline_edge.Length()
    ts = np.linspace(0, 1, 60)
    pts = [spline_edge.positionAt(t) for t in ts]
    if pts[0].z > pts[-1].z:
        pts = pts[::-1]
    secs = [(p.x, p.z) for p in pts if p.x > 1.2]
    top = pts[-1]
    wires = [ellipse_wire(r, r * sq, (0, 0, z)) for r, z in secs]
    return loft_sections(wires, end_point=(0.0, 0.0, top.z))


def revolve(body: dict) -> BodyGeom:
    prof = [(float(r), float(z)) for r, z in body["profile"]]
    wall = float(body.get("wall", 2.4))
    er = float(body.get("edge_round", 8.0))
    sq = float(body.get("squash_y", 1.0))
    warns = []
    curve = prof[1:]
    r1 = curve[0][0]
    r_bottom = 0.0
    solid = None
    if er > 0.5:
        try:
            s0 = _revolve(_profile_face(curve))
            edges = [e for e in s0.Edges() if abs(e.Center().z) < 1e-3 and e.geomType() == "CIRCLE"]
            s1, r_bottom = try_fillet(s0, edges, min(er, r1 * 0.6))
            if r_bottom > 0:
                solid = s1
        except Exception:
            solid = None
    if solid is None:
        solid = _revolve(_profile_face(curve, start_tangent=(1, 0, 0)))
        r_bottom = 0.0
        if er > 0.5:
            warns.append("bottom edge round built as a smooth tangent start instead of a fillet")
    # cavity from the 2D section (mirrored so buffer works across the axis)
    edge = _profile_face(curve) if r_bottom > 0 else _profile_face(curve, start_tangent=(1, 0, 0))
    pts2 = wire_points_2d(edge.outerWire(), "xz", 160)
    half = Polygon(pts2).buffer(0)
    full = half.union(Polygon(np.stack([-pts2[:, 0], pts2[:, 1]], 1)).buffer(0)).buffer(0.01).buffer(-0.01)
    rr = max(r_bottom, 0.0)
    if rr > 0.3:
        full = full.buffer(-rr).buffer(rr)
    inner = full.buffer(-wall)
    inner_half = inner.intersection(sbox(0, -1, 1000, 1000))
    from .probe import largest

    inner_half = largest(inner_half)
    cpts = _half_curve(inner_half)
    # drop the flat-bottom axis start: build spline from the first off-axis point on the floor
    floor_z = min(z for _, z in cpts)
    cp = [p for p in cpts if p[0] > 1e-6]
    cp = _resample_open(cp, 1.5)
    cp[0][1] = floor_z
    curve_in = [(float(cp[0][0]), float(cp[0][1]))] + [tuple(map(float, p)) for p in cp[1:]] + [(0.0, float(cpts[-1][1]))]
    try:
        vs = [V(r, 0, z) for r, z in curve_in]
        sp = cq.Edge.makeSpline(vs)
        e_axis = cq.Edge.makeLine(V(0, 0, curve_in[-1][1]), V(0, 0, floor_z))
        e_floor = cq.Edge.makeLine(V(0, 0, floor_z), V(curve_in[0][0], 0, floor_z))
        cav = _revolve(cq.Face.makeFromWires(cq.Wire.assembleEdges([sp, e_axis, e_floor])))
    except Exception:
        from .geom import resample_ring

        ring = resample_ring(np.asarray(inner_half.exterior.coords), 1.2)
        cav = _revolve(cq.Face.makeFromWires(cq.Wire.makePolygon([V(max(r, 0), 0, z) for r, z in ring], close=True)))
    if abs(sq - 1.0) > 1e-6:
        R = max(r for r, _ in prof)
        sc = (R * sq - wall) / max(R - wall, 1.0)
        # loft of elliptical sections: exact-enough and ~100x faster in booleans than a non-uniform GTransform
        osp = [e for e in _profile_face(curve).Edges() if e.geomType() == "BSPLINE"][0]
        solid = _squash_loft(osp, sq, 0.0)
        r_bottom = 0.0
        if er > 0.5:
            be = [e for e in solid.Edges() if abs(e.BoundingBox().zmax) < 1e-3]
            solid, r_bottom = try_fillet(solid, be, min(er, r1 * 0.6))
        try:
            isp = [e for e in cav.Edges() if e.geomType() == "BSPLINE"][0]
            cav = _squash_loft(isp, sc, floor_z)
        except Exception:
            cav = scale_xyz(cav, 1.0, sc, 1.0)
    cav = fix_shape(cav)
    solid = fix_shape(solid)
    info = {"edge_round_requested": er, "edge_round_bottom": round(r_bottom, 2), "squash_y": sq}
    return BodyGeom("revolve", solid, cav, wall, floor_z, wall, 0.0, 0.0, r_bottom, info, warns)


# ================================================================================================ blob
def _axis_index(p) -> int:
    ax = p.get("axis", "z")
    return "xyz".index(ax)


def primitive(p: dict, shrink: float = 0.0):
    kind = p["kind"]
    c = np.asarray(p["center"], float)
    size = np.asarray(p["size"], float) - 2 * shrink
    if np.any(size <= 1.0):
        return None
    r = max(float(p.get("radius", 4.0)) - shrink, 0.0)
    if kind == "sphere":
        return sphere(c, float(min(size)) / 2)
    if kind == "ellipsoid":
        from .geom import ellipsoid

        return ellipsoid(c, size / 2)
    if kind == "rounded_box":
        b = cq.Solid.makeBox(*size, V(c - size / 2))
        rr = min(r, float(min(size)) / 2 - 0.05)
        if rr > 0.2:
            b, _ = try_fillet(b, b.Edges(), rr, factors=(1.0, 0.9, 0.75, 0.6), min_r=0.2)
        return b
    ai = _axis_index(p)
    L = float(size[ai])
    perp = [size[i] for i in range(3) if i != ai]
    d = float(min(perp))
    axis = np.zeros(3)
    axis[ai] = 1
    if kind == "cylinder":
        others = [i for i in range(3) if i != ai]
        if abs(size[others[0]] - size[others[1]]) > 1e-6:
            from .geom import ellipse_wire

            xd = np.zeros(3)
            xd[others[0]] = 1
            w = ellipse_wire(size[others[0]] / 2, size[others[1]] / 2, tuple(-axis * L / 2), tuple(axis), tuple(xd))
            cyl = cq.Solid.extrudeLinear(cq.Face.makeFromWires(w), V(axis * L))
            rr = min(r, d / 2 - 0.05, L / 2 - 0.05)
            if rr > 0.2:
                cyl, _ = try_fillet(cyl, cyl.Edges(), rr, factors=(1.0, 0.85, 0.7, 0.5), min_r=0.2)
            return cyl.translate(V(c))
        cyl = cq.Solid.makeCylinder(d / 2, L, V(-axis * L / 2), V(axis))
        rr = min(r, d / 2 - 0.05, L / 2 - 0.05)
        if rr > 0.2:
            cyl, _ = try_fillet(cyl, cyl.Edges(), rr, factors=(1.0, 0.85, 0.7, 0.5), min_r=0.2)
        s = cyl
    else:  # capsule
        Lc = max(L - d, 0.01)
        s = cq.Solid.makeCylinder(d / 2, Lc, V(-axis * Lc / 2), V(axis))
        s = s.fuse(sphere(axis * Lc / 2, d / 2), sphere(-axis * Lc / 2, d / 2)).clean()
        s = largest_solid(s)
    sc = [1.0, 1.0, 1.0]
    for i in range(3):
        if i != ai and abs(size[i] - d) > 1e-6:
            sc[i] = size[i] / d
    s = scale_xyz(s, *sc)
    return s.translate(V(c))


def _fuse_blend(a: cq.Shape, b: cq.Shape, r: float):
    op = BRepAlgoAPI_Fuse(a.wrapped, b.wrapped)
    op.SetRunParallel(True)
    op.Build()
    res = cq.Shape.cast(op.Shape())
    sec = [cq.Edge(e) for e in op.SectionEdges()]
    res = res.clean()
    if r > 0.3 and sec:
        # section edges may have been merged by clean(); match by midpoint
        mids = [e.Center() for e in sec]
        cand = [e for e in res.Edges() if any((e.Center() - m).Length < 0.5 for m in mids)]
        out, used = try_fillet(res, cand, r, factors=(1.0, 0.7, 0.5, 0.35), min_r=0.5)
        return out, used
    return res, 0.0


def blob(body: dict) -> BodyGeom:
    prims = body["primitives"]
    wall = float(body.get("wall", 2.4))
    er = float(body.get("edge_round", 8.0))
    warns = []
    solids = [primitive(p) for p in prims]
    order = sorted(range(len(prims)), key=lambda i: -float(np.prod(prims[i]["size"])))
    acc = solids[order[0]]
    blends = []
    for i in order[1:]:
        r = min(float(prims[i].get("radius", 4.0)), 8.0)
        acc, used = _fuse_blend(acc, solids[i], r)
        blends.append(round(used, 2))
    acc = largest_solid(acc.intersect(halfspace_z(0.0, "above")))
    bottom = [e for e in acc.Edges() if abs(e.Center().z) < 1e-3]
    acc, r_bottom = try_fillet(acc, bottom, er, min_r=0.8)
    if er > 0.5 and r_bottom + 1e-6 < er:
        warns.append(f"bottom edge_round {er:g} reduced to {r_bottom:.1f} mm")
    # cavity: union of shrunk primitives, floor at z=wall
    inner = [primitive(p, wall) for p in prims]
    inner = [s for s in inner if s is not None]
    cav = inner[0]
    if len(inner) > 1:
        cav = cav.fuse(*inner[1:]).clean()
    cav = largest_solid(cav.intersect(halfspace_z(wall, "above")))
    cb = [e for e in cav.Edges() if abs(e.Center().z - wall) < 1e-3]
    if r_bottom - wall > 0.6:
        cav, _ = try_fillet(cav, cb, r_bottom - wall, min_r=0.6)
    cav = fix_shape(cav)
    info = {"edge_round_requested": er, "edge_round_bottom": round(r_bottom, 2), "blend_radii": blends}
    return BodyGeom("blob", acc, cav, wall, wall, wall, 0.0, 0.0, r_bottom, info, warns)


def build_body(body: dict, skin_front: float) -> BodyGeom:
    fam = body["family"]
    if fam == "silhouette":
        return silhouette(body, skin_front)
    if fam == "revolve":
        return revolve(body)
    if fam == "blob":
        return blob(body)
    raise ValueError(f"unknown body family {fam}")
