"""Freeform CSG body (pack `freeform`): the agent's CSG tree as real CAD (CadQuery / OpenCascade).

The tree is compiled once by the light engine (gstudio.csg.compile_tree: validated data, world placements composed),
and this module walks the same compiled nodes, so the CAD and normalize's distance field read one set of numbers:

  outer   every primitive exactly (box, cylinder, cone, sphere, torus, extrude with true corner arcs, revolve),
          placed with its rotation + translation; union (fillet of radius `blend` on the seams), cut, intersect;
          cut flat at z = 0; optional bottom edge round (edge_round)
  cavity  the same tree with every primitive inset by the wall (cut tools grown by it, no blends), floor at z = wall:
          exactly the level set {outer distance <= -wall} of the light engine for unions, cuts and intersects
          (parts thinner than 2 x wall stay solid)

Then everything downstream is the blob path: base-plate (default) or rear-cover seam, cartridge, kits, fins, checks.
Failures raise CsgBuildError with the node path, so the message names what to change.
"""

from __future__ import annotations

import math
import time

import cadquery as cq
import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Trsf

try:
    from .. import csg as C
except (ImportError, ValueError):  # the vendored cad/gadget_cad/ copy of a delivered design (export.vendor_source)
    from . import csg as C  # type: ignore[no-redef]
from .body import BodyGeom, fix_shape
from .geom import V, halfspace_z, largest_solid, solids_of, try_fillet

TINY = 0.05


class CsgBuildError(ValueError):
    """A CAD failure the agent can repair: str() = "<path>: <msg> (fix: <fix>)" (contract section 8)."""

    def __init__(self, path: str, msg: str, fix: str = ""):
        self.path, self.msg, self.fix = path, msg, fix
        self.errors = [{"path": path, "msg": msg, "fix": fix, "code": "csg_cad"}]
        super().__init__(f"{path}: {msg}" + (f" (fix: {fix})" if fix else ""))


def _nm(node) -> str:
    return f" ('{node.name}')" if getattr(node, "name", None) else ""


def _short(e: Exception) -> str:
    t = " ".join(str(e).split())[:80]
    for w in ("timed out", "HTTP", "no answer", "not JSON", "Connection", "Timeout", "not available yet"):
        t = t.replace(w, "-")  # never look like an outage to the agent's repair loop (contract section 8)
    return t or type(e).__name__


def _place(s: cq.Shape, node) -> cq.Shape:
    if node.ident and not any(node.t):
        return s
    R, t = node.R, node.t
    tr = gp_Trsf()
    tr.SetValues(R[0][0], R[0][1], R[0][2], t[0], R[1][0], R[1][1], R[1][2], t[1], R[2][0], R[2][1], R[2][2], t[2])
    return cq.Shape.cast(BRepBuilderAPI_Transform(s.wrapped, tr, True).Shape())


def _fillet_all(s: cq.Shape, r: float, edges=None) -> cq.Shape:
    if r <= 0.2:
        return s
    out, _ = try_fillet(s, edges if edges is not None else s.Edges(), r, factors=(1.0, 0.95, 0.85, 0.7, 0.5), min_r=0.2)
    return out


# ------------------------------------------------------------------------------------------------ primitives
def _box(n, off: float):
    hx, hy, hz = (h + off for h in n.h)
    if min(hx, hy, hz) <= TINY:
        return None
    rr = n.round + off
    s = cq.Solid.makeBox(2 * hx, 2 * hy, 2 * hz, V(-hx, -hy, n.h[2] - hz))
    if rr > 0.2:
        s = _fillet_all(s, min(rr, min(hx, hy, hz) - 0.05))
    return s


def _cylinder(n, off: float):
    r, hh = n.r + off, n.hgt / 2 + off
    if r <= TINY or hh <= TINY:
        return None
    s = cq.Solid.makeCylinder(r, 2 * hh, V(0, 0, n.hgt / 2 - hh), V(0, 0, 1))
    rr = n.round + off
    if rr > 0.2:
        rims = [e for e in s.Edges() if e.geomType() == "CIRCLE"]
        s = _fillet_all(s, min(rr, r - 0.05, hh - 0.05), rims)
    return s


def _sphere(n, off: float):
    r = n.r + off
    if r <= TINY:
        return None
    from .geom import sphere

    return sphere((0.0, 0.0, 0.0), r)


def _torus(n, off: float):
    r = n.r + off
    if r <= TINY:
        return None
    return cq.Solid.makeTorus(n.Rr, r, V(0, 0, 0), V(0, 0, 1))


def _cone(n, off: float):
    r1, r2, h = n.cone
    s = (r1 - r2) / h
    k = math.sqrt(1.0 + s * s)
    z0, z1 = -off, h + off
    a1 = r1 + s * off + off * k  # radius at z0 of the offset lateral face (exact face offset; edges stay sharp)
    a2 = r2 - s * off + off * k
    if z1 - z0 <= TINY or (a1 <= TINY and a2 <= TINY):
        return None
    if a1 < 0 or a2 < 0:  # the offset face reaches the axis inside the range: stop there (pointed)
        za = z0 + a1 / (a1 - a2) * (z1 - z0)
        if a1 < 0:
            z0, a1 = za, 0.0
        else:
            z1, a2 = za, 0.0
    if z1 - z0 <= TINY:
        return None
    return cq.Solid.makeCone(max(a1, 0.0), max(a2, 0.0), z1 - z0, V(0, 0, z0), V(0, 0, 1))


def _plane_pt(n, a: float, b: float, w: float) -> cq.Vector:
    return V(*n.lift(a, b, w))


def _outline_wire(n, w: float) -> cq.Wire:
    """The extrude outline (true corner arcs when rounded) as a closed wire in its local plane at coordinate w."""
    if n.cmds is None:
        P = n.points
        return cq.Wire.makePolygon([_plane_pt(n, a, b, w) for a, b in P] + [_plane_pt(n, *P[0], w)])
    edges = []
    last = n.start
    for cmd in n.cmds:
        if cmd[0] == "L":
            edges.append(cq.Edge.makeLine(_plane_pt(n, *last, w), _plane_pt(n, *cmd[1], w)))
        else:
            edges.append(cq.Edge.makeThreePointArc(_plane_pt(n, *last, w), _plane_pt(n, *cmd[1], w), _plane_pt(n, *cmd[2], w)))
        last = cmd[-1]
    if math.dist(last, n.start) > 1e-6:
        edges.append(cq.Edge.makeLine(_plane_pt(n, *last, w), _plane_pt(n, *n.start, w)))
    return cq.Wire.assembleEdges(edges)


def _offset_wire(wire: cq.Wire, off: float, area0: float):
    """2D offset of a closed planar wire by `off` (outward > 0), round joins; None when it vanishes."""
    if abs(off) < 1e-9:
        return wire
    best = None
    for sign in (1.0, -1.0):  # OCC's offset direction depends on the wire orientation: keep the one that grows/shrinks right
        try:
            ws = wire.offset2D(sign * off, "arc")
        except Exception:  # noqa: BLE001
            continue
        if not ws:
            continue
        w = max(ws, key=lambda x: abs(cq.Face.makeFromWires(x).Area()))
        a = abs(cq.Face.makeFromWires(w).Area())
        if (off > 0 and a > area0) or (off < 0 and a < area0):
            best = w
            break
    return best


def _extrude(n, off: float):
    w0, w1 = n.w0 - off, n.w1 + off
    if w1 - w0 <= TINY:
        return None
    wire = _outline_wire(n, w0)
    area0 = abs(cq.Face.makeFromWires(wire).Area())
    if abs(off) > 1e-9:
        wire = _offset_wire(wire, off, area0)
        if wire is None:
            return None
    face = cq.Face.makeFromWires(wire)
    d = np.asarray(n.lift(0.0, 0.0, 1.0)) - np.asarray(n.lift(0.0, 0.0, 0.0))
    s = cq.Solid.extrudeLinear(face, V(*(d * (w1 - w0))))
    rc = n.round_cap + off
    if rc > 0.2:
        caps = [e for e in s.Edges() if abs(_wcoord(e, d) - w0) < 1e-3 or abs(_wcoord(e, d) - w1) < 1e-3]
        s = _fillet_all(s, min(rc, (w1 - w0) / 2 - 0.05), caps)
    return s


def _wcoord(e, d) -> float:
    a = np.asarray(e.startPoint().toTuple())
    b = np.asarray(e.endPoint().toTuple())
    wa, wb = float(np.dot(a, d)), float(np.dot(b, d))
    return wa if abs(wa - wb) < 1e-6 else math.inf  # only edges lying in one cap plane


def _revolve(n, off: float):
    P = n.points
    if abs(off) < 1e-9:
        pts = P
    else:
        from shapely.geometry import Polygon, box as sbox
        from shapely.ops import unary_union

        full = unary_union([Polygon(P), Polygon([(-r, z) for r, z in P])]).buffer(0)
        g = full.buffer(off, quad_segs=12, join_style="round").intersection(sbox(0.0, -1e4, 1e4, 1e4))
        polys = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
        polys = [p for p in polys if p.area > 0.5]
        if not polys:
            return None
        poly = max(polys, key=lambda p: p.area).simplify(0.01)
        pts = [(max(0.0, x), z) for x, z in list(poly.exterior.coords)[:-1]]
    vs = [V(r, 0.0, z) for r, z in pts]
    wire = cq.Wire.makePolygon(vs + [vs[0]])
    face = cq.Face.makeFromWires(wire)
    return cq.Solid.revolve(face, 360.0, V(0, 0, 0), V(0, 0, 1))


_PRIM = {"box": _box, "cylinder": _cylinder, "sphere": _sphere, "torus": _torus, "cone": _cone, "extrude": _extrude,
         "revolve": _revolve}


# ------------------------------------------------------------------------------------------------ tree
def _ok(s) -> bool:
    try:
        return s is not None and s.isValid() and len(solids_of(s)) >= 1 and s.Volume() > 1e-3
    except Exception:  # noqa: BLE001
        return False


def _solid(node, off: float, info: dict):
    if isinstance(node, C.Prim):
        try:
            s = _PRIM[node.op](node, off)
        except Exception as e:  # noqa: BLE001
            raise CsgBuildError(node.path, f"the CAD could not make this {node.op}{_nm(node)} ({_short(e)})", "change its sizes or rounding") from None
        if s is None:
            return None
        s = _place(s, node)
        if not _ok(s):
            s = fix_shape(s)
        if not _ok(s):
            raise CsgBuildError(node.path, f"the CAD made an invalid {node.op}{_nm(node)}", "change its sizes or rounding")
        return s
    if isinstance(node, C.Union):
        parts = [p for p in (_solid(k, off, info) for k in node.kids) if p is not None]
        if not parts:
            return None
        r = node.blend if off == 0.0 else 0.0
        acc = parts[0]
        for k, p in zip(node.kids[1:], parts[1:]):
            acc = _fuse(acc, p, r, node, info)
        return acc
    if isinstance(node, C.Cut):
        base = _solid(node.base, off, info)
        if base is None:
            return None
        tools = [p for p in (_solid(m, -off, info) for m in node.minus) if p is not None]
        if not tools:
            return base
        out = _bool(lambda: base.cut(*tools).clean(), lambda: _seq(base, tools, "cut"), node, "cut")
        return out if solids_of(out) else None
    if isinstance(node, C.Intersect):
        parts = [_solid(k, off, info) for k in node.kids]
        if any(p is None for p in parts):
            return None
        acc = parts[0]
        for p in parts[1:]:
            a = acc
            acc = _bool(lambda: a.intersect(p).clean(), lambda: a.intersect(p), node, "intersect")
            if not solids_of(acc):
                return None
        return acc
    if isinstance(node, C._Empty):
        return None
    raise CsgBuildError(getattr(node, "path", "body.csg"), "unknown node", "use the ops of the freeform rules")


def _seq(base, tools, op):
    acc = base
    for t in tools:
        acc = getattr(acc, op)(t).clean()
    return acc


def _bool(fn, fallback, node, what):
    try:
        out = fn()
        if _ok(out):
            return out
    except Exception:  # noqa: BLE001
        pass
    try:
        out = fix_shape(fallback())
        if _ok(out) or (out is not None and not solids_of(out)):
            return out
    except Exception:  # noqa: BLE001
        pass
    raise CsgBuildError(node.path, f"the CAD could not {what} these pieces{_nm(node)} (OpenCascade boolean failed)",
                        "move the pieces so they overlap clearly (by 2 mm or more) instead of just touching, or simplify this part")


def _fuse(a, b, r: float, node, info: dict):
    if r > 0.3:
        from .body import _fuse_blend

        try:
            out, used = _fuse_blend(a, b, r, factors=(1.0, 0.8, 0.6, 0.4))
            if _ok(out):
                info.setdefault("blend_radii", []).append(round(used, 2))
                return out
        except Exception:  # noqa: BLE001
            pass
        info.setdefault("blend_radii", []).append(0.0)
        info.setdefault("blend_failed", []).append(node.path)
    return _bool(lambda: a.fuse(b).clean(), lambda: a.fuse(b, glue=False), node, "join (union)")


def count_nodes(tree) -> int:
    if not isinstance(tree, dict):
        return 0
    n = 1
    for k in ("node",):
        if isinstance(tree.get(k), dict):
            n += count_nodes(tree[k])
    for k in ("nodes", "minus"):
        for c in tree.get(k) or []:
            n += count_nodes(c)
    return n


# ------------------------------------------------------------------------------------------------ body
def csg_body(body: dict) -> BodyGeom:
    t0 = time.time()
    tree = body["csg"]
    wall = float(body.get("wall", 2.4))
    er = float(body.get("edge_round", 0.0) or 0.0)
    warns: list[str] = []
    try:
        shape = C.compile_tree(tree, "body.csg")
    except C.CsgError as e:
        x = e.errors[0]
        raise CsgBuildError(x["path"], x["msg"], x.get("fix") or "") from None
    info: dict = {"csg_nodes": count_nodes(tree), "csg_primitives": len(shape.leaves())}
    outer = _solid(shape.root, 0.0, info)
    for p in sorted(set(info.get("blend_failed") or [])):
        warns.append(f"{p}: the blend fillet could not be built where these pieces meet, so that seam stays sharp (pieces that "
                     "overlap by a few mm blend more reliably than pieces that only touch)")
    if outer is None:
        raise CsgBuildError("body.csg", "the CAD shape is empty (the cuts or intersects remove everything)", "check the cuts and intersects")
    try:
        # a shape that already starts at z = 0 is not cut: intersecting with a half space whose plane holds its bottom face
        # (bounded by tangent rounds) leaves a valid-looking shell that later booleans misread (base plate "collision")
        cut = outer if outer.BoundingBox().zmin >= -1e-6 else outer.intersect(halfspace_z(0.0, "above"))
    except Exception as e:  # noqa: BLE001
        raise CsgBuildError("body.csg", f"the CAD could not cut the shape flat at the table z = 0 ({_short(e)})", "give it a simple flat underside at z = 0") from None
    sols = sorted(solids_of(cut), key=lambda s: -s.Volume())
    if not sols:
        raise CsgBuildError("body.csg", "nothing of the shape is above the table (z >= 0)", "keep the shape at z >= 0")
    acc = sols[0]
    lost = sum(s.Volume() for s in sols[1:])
    if lost > 1.0:
        warns.append(f"body.csg: {len(sols) - 1} separate piece(s) ({lost / 1000:.1f} cm3) do not touch the main body and were "
                     "left out")
        info["left_out_mm3"] = round(lost, 1)
    r_bottom = 0.0
    if er > 0.5:
        bottom = [e for e in acc.Edges() if abs(e.Center().z) < 1e-3]
        # A fillet of radius e along a bottom corner arc of radius rho is a torus with R = rho - e, r = e: at e = rho / 2
        # it degenerates (horn torus) and the STEP of the shell then hangs on re-import (desk console, 2026-11-20); so
        # e stays below 0.45 x the tightest bottom arc (a ring torus).
        radii = []
        for e in bottom:
            try:
                gt = e.geomType()
                if gt == "CIRCLE":
                    radii.append(float(e.radius()))
                elif gt == "ELLIPSE":
                    radii.append(float(e._geomAdaptor().Ellipse().MinorRadius()))
            except Exception:  # noqa: BLE001
                continue
        e_r = min(er, 0.45 * min(radii)) if radii else er
        if e_r >= 0.8:
            acc, r_bottom = try_fillet(acc, bottom, e_r, min_r=0.8)
        if r_bottom + 1e-6 < er:
            why = f" (the tightest bottom corner has a {min(radii):.1f} mm radius)" if radii and e_r < er else ""
            warns.append(f"bottom edge_round {er:g} reduced to {r_bottom:.1f} mm{why}")
    acc = fix_shape(acc)
    # cavity: inset tree, floor at z = wall
    cav = _solid(shape.root, -wall, {})
    if cav is None:
        raise CsgBuildError("body.csg", f"nothing is left to hollow at a {wall:g} mm wall: the form is thinner than 2 x wall "
                            "everywhere", "make the main volume bigger")
    try:
        cav = largest_solid(cav if cav.BoundingBox().zmin >= wall - 1e-6 else cav.intersect(halfspace_z(wall, "above")))
    except Exception as e:  # noqa: BLE001
        raise CsgBuildError("body.csg", f"the hollow could not be made ({_short(e)})", "make the main volume bigger or simpler") from None
    if r_bottom - wall > 0.6:
        cb = [e for e in cav.Edges() if abs(e.Center().z - wall) < 1e-3]
        cav, _ = try_fillet(cav, cb, r_bottom - wall, min_r=0.6)
    cav = fix_shape(cav)
    info.update({"edge_round_requested": er, "edge_round_bottom": round(r_bottom, 2), "build_s": round(time.time() - t0, 2),
                 "outer_mm3": round(acc.Volume(), 1), "cavity_mm3": round(cav.Volume(), 1)})
    return BodyGeom("csg", acc, cav, wall, wall, wall, 0.0, 0.0, r_bottom, info, warns)


__all__ = ["csg_body", "CsgBuildError", "count_nodes"]
