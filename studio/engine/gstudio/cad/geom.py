"""Low-level CadQuery/OCP helpers shared by the CAD package. No engine imports (vendored into gadget_cad/)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import cadquery as cq
import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.gp import gp_GTrsf
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

Vec = cq.Vector
BIG = 2000.0


def V(x, y=None, z=None) -> cq.Vector:
    if y is None:
        x, y, z = x
    return cq.Vector(float(x), float(y), float(z))


def unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    n = np.linalg.norm(a)
    return a / n if n > 1e-12 else a


# ------------------------------------------------------------------------------------------------ frames
@dataclass
class Frame:
    """Local frame: o origin, u (right), v (up), n (outward normal, local +z)."""

    o: np.ndarray
    u: np.ndarray
    v: np.ndarray
    n: np.ndarray

    @staticmethod
    def make(o, n, up_hint=(0, 0, 1)) -> "Frame":
        n = unit(n)
        up = unit(up_hint)
        if abs(float(np.dot(up, n))) > 0.95:
            up = unit((0, 1, 0)) if abs(n[1]) < 0.9 else unit((1, 0, 0))
        u = unit(np.cross(up, n))
        v = unit(np.cross(n, u))
        return Frame(np.asarray(o, float), u, v, n)

    def rotated(self, deg: float) -> "Frame":
        if not deg:
            return self
        a = math.radians(deg)
        u = self.u * math.cos(a) + self.v * math.sin(a)
        v = np.cross(self.n, u)
        return Frame(self.o, unit(u), unit(v), self.n)

    def pt(self, a, b=0.0, c=0.0) -> np.ndarray:
        return self.o + self.u * a + self.v * b + self.n * c

    def dir(self, a, b=0.0, c=0.0) -> np.ndarray:
        return self.u * a + self.v * b + self.n * c

    def to_local(self, p) -> np.ndarray:
        d = np.asarray(p, float) - self.o
        return np.array([d @ self.u, d @ self.v, d @ self.n])

    def plane(self) -> cq.Plane:
        return cq.Plane(origin=V(self.o), xDir=V(self.u), normal=V(self.n))

    def loc(self) -> cq.Location:
        return cq.Location(self.plane())

    def place(self, shape: cq.Shape) -> cq.Shape:
        return shape.moved(self.loc())

    def to_json(self) -> dict:
        return {k: [round(float(x), 4) for x in getattr(self, k)] for k in ("o", "u", "v", "n")}

    @staticmethod
    def from_json(d: dict) -> "Frame":
        return Frame(*(np.asarray(d[k], float) for k in ("o", "u", "v", "n")))


WORLD = Frame(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))


# ------------------------------------------------------------------------------------------------ primitives (local)
def box(cx, cy, cz, sx, sy, sz) -> cq.Solid:
    return cq.Solid.makeBox(sx, sy, sz, V(cx - sx / 2, cy - sy / 2, cz - sz / 2))


def box_z(cx, cy, z0, z1, sx, sy) -> cq.Solid:
    """Box centred at (cx, cy) in XY spanning z0..z1."""
    lo, hi = min(z0, z1), max(z0, z1)
    return cq.Solid.makeBox(sx, sy, max(hi - lo, 1e-3), V(cx - sx / 2, cy - sy / 2, lo))


def cyl_z(cx, cy, z0, z1, r) -> cq.Solid:
    lo, hi = min(z0, z1), max(z0, z1)
    return cq.Solid.makeCylinder(r, max(hi - lo, 1e-3), V(cx, cy, lo), V(0, 0, 1))


def cyl_axis(p, d, length, r) -> cq.Solid:
    return cq.Solid.makeCylinder(r, length, V(p), V(unit(d)))


def cone_z(cx, cy, z0, z1, r0, r1) -> cq.Solid:
    if z1 < z0:
        z0, z1, r0, r1 = z1, z0, r1, r0
    return cq.Solid.makeCone(r0, r1, z1 - z0, V(cx, cy, z0), V(0, 0, 1))


def ring_z(cx, cy, z0, z1, ro, ri) -> cq.Shape:
    return cyl_z(cx, cy, z0, z1, ro).cut(cyl_z(cx, cy, z0 - 1, z1 + 1, ri))


def sphere(c, r) -> cq.Solid:
    return cq.Solid.makeSphere(r, V(c), angleDegrees1=-90, angleDegrees2=90)


def rounded_rect_face(w, h, r, cx=0.0, cy=0.0) -> cq.Face:
    return rounded_rect_face_fast(w, h, r, cx, cy)


def rrect_prism(cx, cy, z0, z1, w, h, r) -> cq.Shape:
    f = rounded_rect_face_fast(w, h, r, cx, cy)
    return prism(f, z0, z1)


def rounded_rect_face_fast(w, h, r, cx=0.0, cy=0.0) -> cq.Face:
    """Rounded rectangle face in local XY built from lines and arcs (robust, few edges)."""
    r = max(0.0, min(r, w / 2 - 1e-3, h / 2 - 1e-3))
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    if r < 0.05:
        pts = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
        return cq.Face.makeFromWires(cq.Wire.makePolygon(pts, close=True))
    k = r * (1 - math.sqrt(0.5))
    e = [
        cq.Edge.makeLine(V(x0 + r, y0, 0), V(x1 - r, y0, 0)),
        cq.Edge.makeThreePointArc(V(x1 - r, y0, 0), V(x1 - k, y0 + k, 0), V(x1, y0 + r, 0)),
        cq.Edge.makeLine(V(x1, y0 + r, 0), V(x1, y1 - r, 0)),
        cq.Edge.makeThreePointArc(V(x1, y1 - r, 0), V(x1 - k, y1 - k, 0), V(x1 - r, y1, 0)),
        cq.Edge.makeLine(V(x1 - r, y1, 0), V(x0 + r, y1, 0)),
        cq.Edge.makeThreePointArc(V(x0 + r, y1, 0), V(x0 + k, y1 - k, 0), V(x0, y1 - r, 0)),
        cq.Edge.makeLine(V(x0, y1 - r, 0), V(x0, y0 + r, 0)),
        cq.Edge.makeThreePointArc(V(x0, y0 + r, 0), V(x0 + k, y0 + k, 0), V(x0 + r, y0, 0)),
    ]
    return cq.Face.makeFromWires(cq.Wire.assembleEdges(e))


def circle_face(r, cx=0.0, cy=0.0) -> cq.Face:
    return cq.Face.makeFromWires(cq.Wire.makeCircle(r, V(cx, cy, 0), V(0, 0, 1)))


def ellipse_face(rx, ry, cx=0.0, cy=0.0) -> cq.Face:
    if abs(rx - ry) < 1e-3:
        return circle_face(rx, cx, cy)
    if rx >= ry:
        w = cq.Wire.makeEllipse(rx, ry, V(cx, cy, 0), V(0, 0, 1), V(1, 0, 0))
    else:
        w = cq.Wire.makeEllipse(ry, rx, V(cx, cy, 0), V(0, 0, 1), V(0, 1, 0))
    return cq.Face.makeFromWires(w)


def stadium_face(length, width, cx=0.0, cy=0.0, angle=0.0) -> cq.Face:
    """Slot (stadium) of total length `length` and width `width` along local x (rotated by angle deg)."""
    f = rounded_rect_face_fast(max(length, width + 1e-3), width, width / 2 - 1e-3, 0, 0)
    if angle:
        f = f.rotate(V(0, 0, 0), V(0, 0, 1), angle)
    return f.translate(V(cx, cy, 0))


def prism(face: cq.Face | cq.Shape, z0: float, z1: float) -> cq.Shape:
    """Extrude a local XY face (at z=0) to span z0..z1."""
    lo, hi = min(z0, z1), max(z0, z1)
    f = face.translate(V(0, 0, lo))
    if isinstance(f, cq.Compound) or len(f.Faces()) > 1:
        sols = [cq.Solid.extrudeLinear(ff, V(0, 0, hi - lo)) for ff in f.Faces()]
        return cq.Compound.makeCompound(sols)
    return cq.Solid.extrudeLinear(f, V(0, 0, hi - lo))


def compound(shapes: Iterable) -> cq.Compound:
    items = []
    for s in shapes:
        if s is None:
            continue
        if isinstance(s, cq.Workplane):
            s = s.val()
        items.append(s)
    return cq.Compound.makeCompound(items)


# ------------------------------------------------------------------------------------------------ booleans
def fuse(base: cq.Shape, tools: Sequence[cq.Shape], clean: bool = True) -> cq.Shape:
    tools = [t for t in tools if t is not None]
    if not tools:
        return base
    out = base.fuse(*tools)
    return out.clean() if clean else out


def cut(base: cq.Shape, tools: Sequence[cq.Shape], clean: bool = True) -> cq.Shape:
    tools = [t for t in tools if t is not None]
    if not tools:
        return base
    out = base.cut(*tools)
    return out.clean() if clean else out


def intersect(a: cq.Shape, b: cq.Shape) -> cq.Shape:
    return a.intersect(b)


def solids_of(shape: cq.Shape) -> list[cq.Solid]:
    return list(shape.Solids()) if shape is not None else []


def largest_solid(shape: cq.Shape) -> cq.Solid:
    sols = solids_of(shape)
    if not sols:
        raise ValueError("shape has no solids")
    return max(sols, key=lambda s: s.Volume())


def single_solid(shape: cq.Shape, min_frac: float = 0.0) -> tuple[cq.Shape, int]:
    """Return the largest solid; also the count of dropped fragments (slivers from booleans)."""
    sols = solids_of(shape)
    if len(sols) <= 1:
        return (sols[0] if sols else shape), 0
    big = max(sols, key=lambda s: s.Volume())
    return big, len(sols) - 1


def split_fragments(shape: cq.Shape) -> tuple[cq.Shape, list]:
    """Largest solid plus a record {volume_mm3, centroid, bbox} for every dropped fragment (REVIEW-1 MIN-10)."""
    sols = solids_of(shape)
    if len(sols) <= 1:
        return (sols[0] if sols else shape), []
    sols = sorted(sols, key=lambda s: -s.Volume())
    frags = []
    for s in sols[1:]:
        try:
            c = s.Center()
            b = s.BoundingBox()
            frags.append({"volume_mm3": round(float(s.Volume()), 4), "centroid": [round(c.x, 2), round(c.y, 2), round(c.z, 2)],
                          "bbox": [round(v, 2) for v in (b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax)]})
        except Exception:
            frags.append({"volume_mm3": None, "centroid": None})
    return sols[0], frags


def halfspace_y(y: float, keep: str) -> cq.Solid:
    """Big box keeping y <= y (keep='below') or y >= y (keep='above')."""
    if keep == "below":
        return cq.Solid.makeBox(2 * BIG, BIG, 2 * BIG, V(-BIG, y - BIG, -BIG))
    return cq.Solid.makeBox(2 * BIG, BIG, 2 * BIG, V(-BIG, y, -BIG))


def halfspace_z(z: float, keep: str) -> cq.Solid:
    if keep == "below":
        return cq.Solid.makeBox(2 * BIG, 2 * BIG, BIG, V(-BIG, -BIG, z - BIG))
    return cq.Solid.makeBox(2 * BIG, 2 * BIG, BIG, V(-BIG, -BIG, z))


def scale_xyz(shape: cq.Shape, sx=1.0, sy=1.0, sz=1.0, about=(0, 0, 0)) -> cq.Shape:
    if abs(sx - 1) < 1e-9 and abs(sy - 1) < 1e-9 and abs(sz - 1) < 1e-9:
        return shape
    s = shape.translate(V(-about[0], -about[1], -about[2]))
    g = gp_GTrsf()
    g.SetValue(1, 1, sx)
    g.SetValue(2, 2, sy)
    g.SetValue(3, 3, sz)
    out = cq.Shape.cast(BRepBuilderAPI_GTransform(s.wrapped, g, True).Shape())
    return out.translate(V(about))


def ellipse_wire(rx: float, ry: float, center, normal=(0, 0, 1), xdir=(1, 0, 0)) -> cq.Wire:
    """Ellipse with semi-axis rx along xdir and ry along normal x xdir (handles rx < ry)."""
    n = unit(normal)
    xd = unit(xdir)
    if abs(rx - ry) < 1e-9:
        return cq.Wire.makeCircle(rx, V(center), V(n))
    if rx >= ry:
        return cq.Wire.makeEllipse(rx, ry, V(center), V(n), V(xd))
    yd = np.cross(n, xd)
    return cq.Wire.makeEllipse(ry, rx, V(center), V(n), V(yd))


def loft_sections(wires: list, end_point=None, start_point=None, smooth: bool = True) -> cq.Shape:
    """Smooth solid through section wires, optionally closed by a point at either end (a pole)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Pnt

    ts = BRepOffsetAPI_ThruSections(True, not smooth, 1e-6)
    if start_point is not None:
        ts.AddVertex(BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, start_point))).Vertex())
    for w in wires:
        ts.AddWire(w.wrapped)
    if end_point is not None:
        ts.AddVertex(BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, end_point))).Vertex())
    ts.CheckCompatibility(False)
    ts.Build()
    return cq.Shape.cast(ts.Shape())


def spheroid(center, semi, axis: int) -> cq.Shape:
    """Exact ellipsoid with two equal semi-axes: revolve a half ellipse about its distinct axis (index 0/1/2)."""
    a_ax = float(semi[axis])
    a_r = float([semi[i] for i in range(3) if i != axis][0])
    # local: revolve axis = Z, radial = X
    if a_ax >= a_r:
        e = cq.Edge.makeEllipse(a_ax, a_r, V(0, 0, 0), V(0, -1, 0), V(0, 0, 1), 0, 180)
    else:
        e = cq.Edge.makeEllipse(a_r, a_ax, V(0, 0, 0), V(0, -1, 0), V(1, 0, 0), -90, 90)
    p0, p1 = e.startPoint(), e.endPoint()
    w = cq.Wire.assembleEdges([e, cq.Edge.makeLine(p1, p0)])
    s = cq.Solid.revolve(cq.Face.makeFromWires(w), 360.0, V(0, 0, 0), V(0, 0, 1))
    if axis == 0:
        s = s.rotate(V(0, 0, 0), V(0, 1, 0), 90)
    elif axis == 1:
        s = s.rotate(V(0, 0, 0), V(1, 0, 0), -90)
    return s.translate(V(center))


def ellipsoid(center, semi) -> cq.Shape:
    """Ellipsoid: exact spheroid when two semi-axes match, else a smooth loft of z sections closed at both poles."""
    a, b, c = (float(x) for x in semi)
    for k, (p, q) in enumerate(((b, c), (a, c), (a, b))):
        if abs(p - q) < 1e-6:
            return spheroid(center, (a, b, c), k)
    ts = np.cos(np.linspace(math.pi, 0, 26))[1:-1]
    wires = [ellipse_wire(a * math.sqrt(1 - t * t), b * math.sqrt(1 - t * t), (center[0], center[1], center[2] + c * t)) for t in ts]
    return loft_sections(wires, end_point=(center[0], center[1], center[2] + c), start_point=(center[0], center[1], center[2] - c))


# ------------------------------------------------------------------------------------------------ fillets
def try_fillet(solid: cq.Shape, edges: list, radius: float, factors=(1.0, 0.8, 0.65, 0.5, 0.38, 0.28, 0.2),
               min_r: float = 0.6) -> tuple[cq.Shape, float]:
    """Fillet `edges` with the largest radius from radius*factors that OpenCascade accepts. Returns (shape, r_used)."""
    if radius <= 0 or not edges:
        return solid, 0.0
    for f in factors:
        r = radius * f
        if r < min_r:
            break
        try:
            out = solid.fillet(r, edges)
            if out.isValid() and out.Volume() > 0:
                return out, r
        except Exception:
            continue
    return solid, 0.0


# ------------------------------------------------------------------------------------------------ 2D (shapely) -> faces
def resample_ring(coords: np.ndarray, spacing: float, nmin: int = 16, nmax: int = 220) -> np.ndarray:
    pts = np.asarray(coords, float)
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    seg = np.linalg.norm(np.diff(np.vstack([pts, pts[:1]]), axis=0), axis=1)
    per = float(seg.sum())
    n = int(np.clip(per / max(spacing, 1e-3), nmin, nmax))
    cum = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, per, n, endpoint=False)
    closed = np.vstack([pts, pts[:1]])
    x = np.interp(t, cum, closed[:, 0])
    y = np.interp(t, cum, closed[:, 1])
    return np.stack([x, y], 1)


def smooth_wire_2d(points: np.ndarray, z: float = 0.0) -> cq.Wire:
    """Closed periodic B-spline through 2D points in local XY."""
    vs = [V(p[0], p[1], z) for p in points]
    e = cq.Edge.makeSpline(vs, periodic=True)
    return cq.Wire.assembleEdges([e])


def poly_face(poly, spacing: float = 0.9, smooth: bool = True) -> Optional[cq.Shape]:
    """shapely Polygon/MultiPolygon -> planar face(s) in local XY (z=0). Holes supported."""
    from shapely.geometry import MultiPolygon, Polygon

    if poly is None or poly.is_empty:
        return None
    polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
    faces = []
    for p in polys:
        if not isinstance(p, Polygon) or p.area < 0.05:
            continue
        outer = _ring_wire(np.asarray(p.exterior.coords), spacing, smooth)
        inners = [_ring_wire(np.asarray(r.coords), spacing, smooth) for r in p.interiors if Polygon(r).area > 0.05]
        try:
            faces.append(cq.Face.makeFromWires(outer, inners))
        except Exception:
            faces.append(cq.Face.makeFromWires(_ring_wire(np.asarray(p.exterior.coords), spacing, False)))
    if not faces:
        return None
    return faces[0] if len(faces) == 1 else cq.Compound.makeCompound(faces)


def _ring_wire(coords: np.ndarray, spacing: float, smooth: bool) -> cq.Wire:
    pts = resample_ring(coords, spacing)
    if smooth and len(pts) >= 8:
        try:
            return smooth_wire_2d(pts)
        except Exception:
            pass
    return cq.Wire.makePolygon([V(p[0], p[1], 0) for p in pts], close=True)


def wire_points_2d(wire_or_edges, plane: str = "xz", n_per_edge: int = 200) -> np.ndarray:
    """Sample a wire into 2D points in the given world plane ('xz', 'xy', 'yz')."""
    edges = wire_or_edges.Edges() if hasattr(wire_or_edges, "Edges") else list(wire_or_edges)
    pts = []
    for e in edges:
        for p in e.positions(list(np.linspace(0, 1, n_per_edge, endpoint=False))):
            pts.append((p.x, p.y, p.z))
    a = np.array(pts)
    idx = {"xz": [0, 2], "xy": [0, 1], "yz": [1, 2]}[plane]
    return a[:, idx]


# ------------------------------------------------------------------------------------------------ tessellation
def tessellate(shape: cq.Shape, tol: float = 0.05, ang: float = 0.25, fresh: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    """Mesh a shape with per-face normals. Returns (verts[N,3], normals[N,3], tris[M,3], face_ranges)
    where face_ranges[k] = (tri_start, tri_end) for the k-th face of shape.Faces().
    fresh=True drops any cached (possibly finer) triangulation first, so display meshes stay light."""
    if fresh:
        from OCP.BRepTools import BRepTools

        BRepTools.Clean_s(shape.wrapped)
    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, ang, True)
    vs, ns, ts, ranges = [], [], [], []
    off = 0
    tri_count = 0
    for face in shape.Faces():
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if poly is None:
            ranges.append((tri_count, tri_count))
            continue
        trsf = loc.Transformation()
        if not poly.HasNormals():
            try:
                BRepLib_ToolTriangulatedShape.ComputeNormals_s(face.wrapped, poly)
            except Exception:
                pass
        nn = poly.NbNodes()
        P = np.empty((nn, 3))
        for i in range(nn):
            p = poly.Node(i + 1).Transformed(trsf)
            P[i] = (p.X(), p.Y(), p.Z())
        N = np.zeros((nn, 3))
        if poly.HasNormals():
            for i in range(nn):
                d = poly.Normal(i + 1).Transformed(trsf)
                N[i] = (d.X(), d.Y(), d.Z())
        nt = poly.NbTriangles()
        T = np.empty((nt, 3), dtype=np.int64)
        for i in range(nt):
            T[i] = poly.Triangle(i + 1).Get()
        T -= 1
        rev = face.wrapped.Orientation() == TopAbs_REVERSED
        if rev:
            T = T[:, [0, 2, 1]]
            N = -N
        if not poly.HasNormals() or not np.any(N):
            fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
            N = np.zeros((nn, 3))
            for k in range(3):
                np.add.at(N, T[:, k], fn)
            N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
        vs.append(P)
        ns.append(N)
        ts.append(T + off)
        off += nn
        ranges.append((tri_count, tri_count + nt))
        tri_count += nt
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), ranges
    return np.vstack(vs), np.vstack(ns), np.vstack(ts), ranges


def to_trimesh(shape: cq.Shape, tol: float = 0.08, ang: float = 0.3):
    import trimesh

    v, n, t, _ = tessellate(shape, tol, ang)
    m = trimesh.Trimesh(vertices=v, faces=t, process=True)
    m.merge_vertices(digits_vertex=4)
    return m


def to_manifold(shape: cq.Shape, tol: float = 0.08, ang: float = 0.3):
    """manifold3d.Manifold from a closed shape (falls back to convex hull for non-manifold meshes)."""
    import manifold3d as mf

    m = to_trimesh(shape, tol, ang)
    mesh = mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32))
    man = mf.Manifold(mesh)
    if man.status() != mf.Error.NoError or man.is_empty():
        try:
            parts = m.split(only_watertight=False)
            mans = []
            for p in parts:
                mm = mf.Manifold(mf.Mesh(vert_properties=np.asarray(p.vertices, np.float32),
                                         tri_verts=np.asarray(p.faces, np.uint32)))
                if mm.status() == mf.Error.NoError and not mm.is_empty():
                    mans.append(mm)
                else:
                    mans.append(mf.Manifold.hull_points(np.asarray(p.vertices, np.float32)))
            man = mf.Manifold.batch_boolean(mans, mf.OpType.Add) if mans else man
        except Exception:
            man = mf.Manifold.hull_points(np.asarray(m.vertices, np.float32))
    return man


def accurate_volume(shape: cq.Shape, tol: float = 0.01, ang: float = 0.08) -> float:
    """Volume from a fine closed tessellation. OpenCascade's integrated volume (GProp) drifts by 1-10 % on dense
    B-spline faces depending on its settings; the fine mesh converges (checked at 0.05 / 0.01 mm)."""
    import trimesh
    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape.wrapped)
    v, _, t, _ = tessellate(shape, tol, ang)
    BRepTools.Clean_s(shape.wrapped)
    if not len(t):
        return 0.0
    m = trimesh.Trimesh(vertices=v, faces=t, process=True)
    m.merge_vertices(digits_vertex=5)
    return float(abs(m.volume))


def bbox(shape: cq.Shape) -> list[float]:
    b = shape.BoundingBox()
    return [b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax]


def rgb(hexstr: str) -> tuple[float, float, float]:
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a, b, t: float) -> tuple[float, float, float]:
    return tuple(float(a[i] * (1 - t) + b[i] * t) for i in range(3))  # type: ignore[return-value]


def camel(s: str) -> str:
    parts = [p for p in "".join(ch if ch.isalnum() else " " for ch in s).split() if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Part"
