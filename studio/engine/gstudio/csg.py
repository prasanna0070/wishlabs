"""Freeform CSG bodies (pack `freeform`; contract: _notes/freeform-contract.md). Light engine: pure Python, numpy only
for the grid checks; no CadQuery, firmware, manual, order or pcb imports.

The agent describes the OUTER form of a body as a tree of JSON nodes. Nothing is ever executed: the tree is data,
validated against a strict grammar with hard limits (validate_tree), then evaluated as a signed distance field
(compile_tree) for normalize (fit, envelope, face ray-casting) and built as real CAD on the builder
(gstudio.cad.csg_body walks the same compiled nodes, so both sides read one set of numbers).

    from gstudio import csg
    errs = csg.validate_tree(tree, path="body.csg")       # [] or [{path, msg, fix, code}]
    shape = csg.compile_tree(tree)                         # raises CsgError(errors) when invalid
    shape.sdf(x, y, z)                                     # outer distance in mm (< 0 inside), cut at the table z = 0
    shape.bounds                                           # (x0, x1, y0, y1, z0, z1), conservative, z0 >= 0
    csg.solid_checks(tree, kind="gadget")                  # empty / floating / footprint / disconnected / thin errors
    csg.scale_tree(tree, k)                                # every length times k

Frame (as every body): mm, +Z up, the object stands on z = 0, the front faces -Y, +X is the viewer's right.
Anchoring: box, cylinder, cone and extrude (axis z) are centred on x and y and rise from z = 0; sphere and torus are
centred on their origin; extrude axis "y" / "x" is centred along its axis; revolve uses its own (r, z).
move: turn about the node's own origin, first about X, then Y, then Z (R = Rz Ry Rx, the blob `rot` rule), then shift.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

MAX_NODES = 40
MAX_DEPTH = 8
MAX_POINTS = 64
MIN_FEATURE = 2.0  # mm: smallest printable size, and the thinnest part of the finished outer solid
MIN_GAP = 1.0  # mm between neighbouring outline points
MAX_XY = 130.0
MAX_Z = 260.0
MAX_LEN = 260.0
MAX_BLEND = 40.0
FOOTPRINT_MIN = {"gadget": 1500.0, "printed": 400.0}  # mm2 of footprint on the table (15 cm2 / 4 cm2)
NAME_MAX = 40

PRIMS = ("box", "cylinder", "cone", "sphere", "torus", "extrude", "revolve")
COMBOS = ("move", "union", "cut", "intersect")
OPS = PRIMS + COMBOS
_KEYS: dict[str, tuple[set, set]] = {  # op -> (required, optional); "name" is allowed on every node
    "box": ({"size"}, {"round"}),
    "cylinder": ({"r", "h"}, {"round"}),
    "cone": ({"r1", "r2", "h"}, set()),
    "sphere": ({"r"}, set()),
    "torus": ({"R", "r"}, set()),
    "extrude": ({"points", "h"}, {"axis", "round"}),
    "revolve": ({"points"}, set()),
    "move": ({"node"}, {"to", "rot"}),
    "union": ({"nodes"}, {"blend"}),
    "cut": ({"node", "minus"}, set()),
    "intersect": ({"nodes"}, set()),
}
_OPS_TEXT = ", ".join(OPS)


class CsgError(ValueError):
    """Invalid tree; .errors = [{path, msg, fix, code}]."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("; ".join(f"{e['path']}: {e['msg']}" for e in errors[:4]))


# =============================================================================================== small helpers
def _e(errs: list, path: str, msg: str, fix: Optional[str] = None, code: str = "csg_schema") -> None:
    d = {"path": path, "msg": msg, "code": code}
    if fix:
        d["fix"] = fix
    errs.append(d)


def _isnum(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _show(v: Any) -> str:
    try:
        s = json.dumps(v)
    except (TypeError, ValueError):
        s = repr(v)
    return s if len(s) <= 40 else s[:37] + "..."


def rot_matrix(rot) -> tuple:
    """R = Rz @ Ry @ Rx for rot = [rx, ry, rz] degrees (turn about x, then y, then z), as spec._rot_matrix."""
    ax, ay, az = (math.radians(float(v)) for v in rot)
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return ((cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
            (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
            (-sy, cy * sx, cy * cx))


def _mm(A, B) -> tuple:
    return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _mv(A, v) -> tuple:
    return tuple(sum(A[i][k] * v[k] for k in range(3)) for i in range(3))


_I3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def polygon_area(pts) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a, b, c) -> bool:
    return min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9 and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9


def _seg_x(p1, p2, p3, p4) -> bool:
    d1, d2, d3, d4 = _orient(p3, p4, p1), _orient(p3, p4, p2), _orient(p1, p2, p3), _orient(p1, p2, p4)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return ((abs(d1) < 1e-9 and _on_seg(p3, p4, p1)) or (abs(d2) < 1e-9 and _on_seg(p3, p4, p2))
            or (abs(d3) < 1e-9 and _on_seg(p1, p2, p3)) or (abs(d4) < 1e-9 and _on_seg(p1, p2, p4)))


def self_crossing(pts) -> Optional[tuple[int, int]]:
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue
            if _seg_x(a, b, pts[j], pts[(j + 1) % n]):
                return i, j
    return None


# =============================================================================================== validation
class _V:
    def __init__(self):
        self.errs: list[dict] = []
        self.count = 0
        self.max_depth = 0
        self.deep_path = ""


def validate_tree(tree: Any, path: str = "body.csg") -> list[dict]:
    """Strict grammar + limits. [] when the tree is valid; else [{path, msg, fix, code}] (at most 12, most useful first)."""
    v = _V()
    _walk(tree, path, 1, v)
    if v.count > MAX_NODES:
        _e(v.errs, path, f"the tree has {v.count} nodes; the limit is {MAX_NODES} (every object with an op counts, move and "
           "union included)", "use fewer pieces: one extrude outline replaces several boxes, a union takes many nodes at once, "
           "drop small details", "csg_limit")
    if v.max_depth > MAX_DEPTH:
        _e(v.errs, v.deep_path, f"the tree is nested {v.max_depth} levels deep; the limit is {MAX_DEPTH} (the root is level 1)",
           "flatten it: put several nodes in one union instead of unions inside unions, and one move per primitive", "csg_limit")
    errs = sorted(v.errs, key=lambda e: e.get("code") != "csg_limit")
    return errs[:12]


def _walk(node: Any, path: str, depth: int, v: _V) -> None:
    if depth > v.max_depth:
        v.max_depth, v.deep_path = depth, path
    if not isinstance(node, dict):
        _e(v.errs, path, f"a node must be an object with an \"op\", not {_show(node)}", f"use one of: {_OPS_TEXT}")
        return
    v.count += 1
    op = node.get("op")
    if op not in _KEYS:
        if op is None:
            _e(v.errs, path, "node has no \"op\"", f"add \"op\": one of {_OPS_TEXT}")
        else:
            _e(v.errs, path, f"unknown op {_show(op)}", f"use one of: {_OPS_TEXT} (no code, no other operations)")
        return
    req, opt = _KEYS[op]
    allowed = req | opt | {"op", "name"}
    extra = sorted(k for k in node if k not in allowed)
    if extra:
        _e(v.errs, path, f"unknown key(s) {', '.join(repr(k) for k in extra)} for op {op!r}",
           f"allowed keys for {op}: {', '.join(['op'] + sorted(req) + sorted(opt) + ['name'])}")
    for k in sorted(req):
        if k not in node:
            _e(v.errs, f"{path}.{k}", f"required key {k!r} is missing for op {op!r}")
    nm = node.get("name")
    if nm is not None and (not isinstance(nm, str) or len(nm) > NAME_MAX):
        _e(v.errs, f"{path}.name", f"name must be a short text (at most {NAME_MAX} characters)")
    if depth > MAX_DEPTH + 4:  # far too deep: stop walking (reported once by validate_tree)
        return
    if op in PRIMS:
        _prim_fields(node, op, path, v.errs)
        return
    if op == "move":
        _vec3(node, "to", path, v.errs, lo=(-MAX_XY, -MAX_XY, -MAX_XY), hi=(MAX_XY, MAX_XY, MAX_Z), what="position")
        _vec3(node, "rot", path, v.errs, lo=(-360.0,) * 3, hi=(360.0,) * 3, what="rotation (degrees)")
        if "node" in node:
            _walk(node["node"], f"{path}.node", depth + 1, v)
        return
    if op in ("union", "intersect"):
        kids = node.get("nodes")
        if "nodes" in node and (not isinstance(kids, list) or len(kids) < 2):
            _e(v.errs, f"{path}.nodes", f"{op} needs a list of at least 2 nodes", "list the pieces in \"nodes\": [...]")
            kids = kids if isinstance(kids, list) else []
        for i, k in enumerate(kids or []):
            _walk(k, f"{path}.nodes[{i}]", depth + 1, v)
        if op == "union":
            _num(node, "blend", path, v.errs, 0.0, MAX_BLEND, "blend radius", required=False)
        return
    # cut
    if "node" in node:
        _walk(node["node"], f"{path}.node", depth + 1, v)
    minus = node.get("minus")
    if "minus" in node and (not isinstance(minus, list) or not minus):
        _e(v.errs, f"{path}.minus", "cut needs \"minus\": a list of 1 or more nodes to take away")
        minus = minus if isinstance(minus, list) else []
    for i, k in enumerate(minus or []):
        _walk(k, f"{path}.minus[{i}]", depth + 1, v)


def _num(node, key, path, errs, lo, hi, what, required=True, code=None, fix=None) -> Optional[float]:
    if key not in node or node.get(key) is None:
        return None
    val = node[key]
    if not _isnum(val):
        _e(errs, f"{path}.{key}", f"{key} must be a number (mm), not {_show(val)}")
        return None
    val = float(val)
    if val < lo - 1e-9:
        small = lo >= 1.0 and lo <= MIN_FEATURE and code is None
        _e(errs, f"{path}.{key}", f"{what} {val:g} is below the minimum {lo:g}" + (" (smallest printable feature is 2 mm)" if small else ""),
           fix or f"use {key} >= {lo:g}", code or ("csg_min_feature" if small else "csg_limit"))
        return None
    if val > hi + 1e-9:
        _e(errs, f"{path}.{key}", f"{what} {val:g} is above the maximum {hi:g}", fix or f"use {key} <= {hi:g} (the print volume)",
           code or "csg_limit")
        return None
    return val


def _vec3(node, key, path, errs, lo, hi, what) -> Optional[tuple]:
    if key not in node or node.get(key) is None:
        return None
    val = node[key]
    if not isinstance(val, list) or len(val) != 3 or not all(_isnum(c) for c in val):
        _e(errs, f"{path}.{key}", f"{key} must be [x, y, z] (3 numbers), not {_show(val)}")
        return None
    for i, c in enumerate(val):
        if not (lo[i] - 1e-9 <= float(c) <= hi[i] + 1e-9):
            _e(errs, f"{path}.{key}[{i}]", f"{what} {'xyz'[i]} = {float(c):g} is outside {lo[i]:g}..{hi[i]:g}",
               "keep every piece inside the print volume (|x|, |y| <= 130, z <= 260 mm)", "csg_limit")
            return None
    return tuple(float(c) for c in val)


def _points(node, path, errs, revolve: bool) -> Optional[list]:
    pts = node.get("points")
    if pts is None:
        return None
    what = "[r, z]" if revolve else "[a, b]"
    if not isinstance(pts, list) or not all(isinstance(p, list) and len(p) == 2 and all(_isnum(c) for c in p) for p in pts):
        _e(errs, f"{path}.points", f"points must be a list of {what} pairs of numbers")
        return None
    if len(pts) < 3 or len(pts) > MAX_POINTS:
        _e(errs, f"{path}.points", f"an outline needs 3 to {MAX_POINTS} points, not {len(pts)}",
           "simplify the outline" if len(pts) > MAX_POINTS else "add points", "csg_limit")
        return None
    P = [(float(a), float(b)) for a, b in pts]
    for i, (a, b) in enumerate(P):
        if revolve and a < -1e-9:
            _e(errs, f"{path}.points[{i}]", f"r = {a:g} is negative; a revolve profile lives in r >= 0", "use r >= 0", "csg_limit")
            return None
        lim_a = MAX_XY if revolve else MAX_LEN
        if abs(a) > lim_a + 1e-9 or abs(b) > MAX_LEN + 1e-9:
            _e(errs, f"{path}.points[{i}]", f"point {_show(pts[i])} is outside the print volume", "keep coordinates within "
               f"+-{MAX_LEN:g} mm (r <= {MAX_XY:g})", "csg_limit")
            return None
    n = len(P)
    for i in range(n):
        j = (i + 1) % n
        if math.dist(P[i], P[j]) < MIN_GAP - 1e-9:
            _e(errs, f"{path}.points[{j}]", f"points {i} and {j} are {math.dist(P[i], P[j]):.2f} mm apart; outline points must be at "
               f"least {MIN_GAP:g} mm apart" + (" (do not repeat the first point at the end)" if j == 0 else ""),
               "remove or move the point", "csg_min_feature")
            return None
    x = self_crossing(P)
    if x:
        i, j = x
        _e(errs, f"{path}.points", f"the outline crosses itself (edge {i}->{(i + 1) % n} meets edge {j}->{(j + 1) % n})",
           "list the points in order around the shape")
        return None
    if abs(polygon_area(P)) < 4.0:
        _e(errs, f"{path}.points", "the outline encloses (almost) no area", "spread the points out", "csg_min_feature")
        return None
    return P


def _prim_fields(node, op, path, errs) -> None:
    mf = MIN_FEATURE
    if op == "box":
        s = node.get("size")
        if s is not None:
            if not isinstance(s, list) or len(s) != 3 or not all(_isnum(c) for c in s):
                _e(errs, f"{path}.size", f"size must be [sx, sy, sz] (3 numbers, mm), not {_show(s)}")
            else:
                for i, c in enumerate(s):
                    c = float(c)
                    if c < mf - 1e-9:
                        _e(errs, f"{path}.size[{i}]", f"box size {'xyz'[i]} = {c:g} mm is below the 2 mm minimum feature",
                           "make every side at least 2 mm", "csg_min_feature")
                    elif c > MAX_LEN + 1e-9:
                        _e(errs, f"{path}.size[{i}]", f"box size {'xyz'[i]} = {c:g} mm is above {MAX_LEN:g} mm", "make it smaller",
                           "csg_limit")
        _num(node, "round", path, errs, 0.0, MAX_LEN / 2, "round")
    elif op == "cylinder":
        _num(node, "r", path, errs, mf / 2, MAX_XY, "radius r", fix="use r >= 1 (2 mm across)")
        _num(node, "h", path, errs, mf, MAX_LEN, "height h")
        _num(node, "round", path, errs, 0.0, MAX_LEN / 2, "round")
    elif op == "cone":
        r1 = _num(node, "r1", path, errs, 0.0, MAX_XY, "radius r1")
        r2 = _num(node, "r2", path, errs, 0.0, MAX_XY, "radius r2")
        _num(node, "h", path, errs, mf, MAX_LEN, "height h")
        if r1 is not None and r2 is not None and max(r1, r2) < mf / 2:
            _e(errs, path, f"cone radii {r1:g} and {r2:g} are both below 1 mm", "make r1 or r2 at least 1 mm", "csg_min_feature")
    elif op == "sphere":
        _num(node, "r", path, errs, mf / 2, MAX_XY, "radius r", fix="use r >= 1 (2 mm across)")
    elif op == "torus":
        R = _num(node, "R", path, errs, mf, MAX_XY, "ring radius R")
        r = _num(node, "r", path, errs, mf / 2, MAX_XY, "tube radius r", fix="use r >= 1 (2 mm across)")
        if R is not None and r is not None and R < r + 1.0 - 1e-9:
            _e(errs, path, f"torus ring radius R = {R:g} must be at least r + 1 = {r + 1:g} (the ring needs a hole)",
               "make R bigger or r smaller (for a solid round shape use sphere or cylinder)", "csg_limit")
    elif op == "extrude":
        _points(node, path, errs, revolve=False)
        _num(node, "h", path, errs, mf, MAX_LEN, "height h")
        ax = node.get("axis", "z")
        if ax not in ("z", "y", "x"):
            _e(errs, f"{path}.axis", f"axis must be \"z\" (points [x, y], up from z = 0), \"y\" (points [x, z], front view) or "
               f"\"x\" (points [y, z], side view), not {_show(ax)}")
        _num(node, "round", path, errs, 0.0, MAX_LEN / 2, "round")
    elif op == "revolve":
        _points(node, path, errs, revolve=True)


# =============================================================================================== polygons
def fillet_path(pts: list, r: float, step_deg: float = 10.0):
    """Round every corner of a closed polygon with radius r (cut back where an edge is shorter than both tangents).
    Returns (start, cmds, min_convex_r): cmds = [("L", (x, y))] or [("A", (mx, my), (x, y))] (arc through its midpoint);
    min_convex_r = the smallest radius actually used on a convex corner (r when none was cut back)."""
    P = [(float(a), float(b)) for a, b in pts]
    n = len(P)
    ccw = polygon_area(P) > 0
    corners = []  # per vertex: (t1, mid, t2) or (p,)
    min_cvx = r
    for i in range(n):
        p0, p1, p2 = P[i - 1], P[i], P[(i + 1) % n]
        l1, l2 = math.dist(p0, p1), math.dist(p1, p2)
        u1 = ((p0[0] - p1[0]) / l1, (p0[1] - p1[1]) / l1)
        u2 = ((p2[0] - p1[0]) / l2, (p2[1] - p1[1]) / l2)
        c = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        th = math.acos(c)  # angle between the two edges at the vertex
        if th > math.pi - 1e-3 or th < 1e-3 or r <= 0.05:
            corners.append((p1,))
            continue
        t = r / math.tan(th / 2)
        tmax = 0.5 * min(l1, l2) - 1e-4
        rr = r
        if t > tmax:
            t = max(tmax, 0.0)
            rr = t * math.tan(th / 2)
        if rr < 0.05:
            corners.append((p1,))
            continue
        cross = (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0])
        if (cross > 0) == ccw:
            min_cvx = min(min_cvx, rr)
        bx, by = u1[0] + u2[0], u1[1] + u2[1]
        bl = math.hypot(bx, by)
        dc = rr / math.sin(th / 2)
        cen = (p1[0] + bx / bl * dc, p1[1] + by / bl * dc)
        T1 = (p1[0] + u1[0] * t, p1[1] + u1[1] * t)
        T2 = (p1[0] + u2[0] * t, p1[1] + u2[1] * t)
        mx, my = p1[0] + bx / bl * (dc - rr), p1[1] + by / bl * (dc - rr)  # arc midpoint (on the bisector)
        corners.append((T1, (mx, my), T2, cen, rr))
    start = corners[0][0] if len(corners[0]) == 1 else corners[0][2]
    cmds = []
    order = list(range(1, n)) + [0]
    for i in order:
        c = corners[i]
        if len(c) == 1:
            cmds.append(("L", c[0]))
        else:
            cmds.append(("L", c[0]))
            cmds.append(("A", c[1], c[2]))
    # drop zero-length lines (two tangents met in the middle of an edge)
    out, last = [], start
    for cmd in cmds:
        end = cmd[-1]
        if cmd[0] == "L" and math.dist(last, end) < 1e-6:
            continue
        out.append(cmd)
        last = end
    return start, out, min_cvx


def path_points(start, cmds, step_deg: float = 10.0) -> list:
    """Sample a fillet_path into a closed polyline (arcs every step_deg; the first point is not repeated)."""
    pts = [start]
    last = start
    for cmd in cmds:
        if cmd[0] == "L":
            pts.append(cmd[1])
        else:
            mid, end = cmd[1], cmd[2]
            cx, cy, rr = _circle3(last, mid, end)
            a1 = math.atan2(last[1] - cy, last[0] - cx)
            am = math.atan2(mid[1] - cy, mid[0] - cx)
            a2 = math.atan2(end[1] - cy, end[0] - cx)
            da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
            dm = (am - a1 + math.pi) % (2 * math.pi) - math.pi
            if dm * da < 0:  # the arc goes the long way round
                da = da - math.copysign(2 * math.pi, da)
            k = max(2, int(math.ceil(abs(da) / math.radians(step_deg))))
            for j in range(1, k + 1):
                a = a1 + da * j / k
                pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        last = cmd[-1]
    if math.dist(pts[0], pts[-1]) < 1e-6:
        pts.pop()
    return pts


def _circle3(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return (ax + cx) / 2, (ay + cy) / 2, math.dist(a, c) / 2
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return ux, uy, math.dist((ux, uy), a)


class Poly:
    """Exact signed distance to a closed polygon (negative inside). Edges in `skip` (revolve axis edges) take part in the
    inside test but not in the distance."""

    def __init__(self, pts, skip: Optional[set] = None):
        self.pts = [(float(a), float(b)) for a, b in pts]
        n = len(self.pts)
        self.segs = []
        for i in range(n):
            ax, ay = self.pts[i]
            bx, by = self.pts[(i + 1) % n]
            ex, ey = bx - ax, by - ay
            L2 = ex * ex + ey * ey
            self.segs.append((ax, ay, ex, ey, (1.0 / L2) if L2 > 0 else 0.0, bool(skip and i in skip)))
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        self.bb = (min(xs), max(xs), min(ys), max(ys))

    def sdf(self, x: float, y: float) -> float:
        d2 = math.inf
        inside = False
        for ax, ay, ex, ey, il, sk in self.segs:
            wx, wy = x - ax, y - ay
            if not sk:
                t = (wx * ex + wy * ey) * il
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                bx, by = wx - ex * t, wy - ey * t
                dd = bx * bx + by * by
                if dd < d2:
                    d2 = dd
            if (ay > y) != (ay + ey > y):
                if x < ax + (y - ay) * ex / ey:
                    inside = not inside
        d = math.sqrt(d2)
        return -d if inside else d

    def sdf_np(self, X, Y):
        import numpy as np

        d2 = np.full(X.shape, np.inf)
        inside = np.zeros(X.shape, bool)
        for ax, ay, ex, ey, il, sk in self.segs:
            wx, wy = X - ax, Y - ay
            if not sk:
                t = np.clip((wx * ex + wy * ey) * il, 0.0, 1.0)
                bx, by = wx - ex * t, wy - ey * t
                np.minimum(d2, bx * bx + by * by, out=d2)
            if ey != 0.0:
                c = (ay > Y) != (ay + ey > Y)
                xc = ax + (Y - ay) * (ex / ey)
                inside ^= c & (X < xc)
        d = np.sqrt(d2)
        return np.where(inside, -d, d)


# =============================================================================================== compiled nodes
class Node:
    path: str = ""
    name: Optional[str] = None
    bbox: Optional[tuple] = None  # world (x0, x1, y0, y1, z0, z1); None = empty

    def label(self) -> str:
        return f"{self.path} ('{self.name}')" if self.name else self.path

    def bdist(self, x, y, z) -> float:
        b = self.bbox
        dx = b[0] - x if x < b[0] else (x - b[1] if x > b[1] else 0.0)
        dy = b[2] - y if y < b[2] else (y - b[3] if y > b[3] else 0.0)
        dz = b[4] - z if z < b[4] else (z - b[5] if z > b[5] else 0.0)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def bdist_np(self, P, np):
        b = self.bbox
        d = np.maximum(np.maximum(np.stack([b[0] - P[:, 0], b[2] - P[:, 1], b[4] - P[:, 2]], 1),
                                  np.stack([P[:, 0] - b[1], P[:, 1] - b[3], P[:, 2] - b[5]], 1)), 0.0)
        return np.sqrt((d * d).sum(1))

    def leaves(self) -> list:
        return []


class Prim(Node):
    """A primitive with its world placement: world = R @ local + t."""

    op = ""

    def __init__(self, R, t, path, name):
        self.R, self.t, self.path, self.name = R, t, path, name
        self.ident = all(abs(R[i][j] - (1.0 if i == j else 0.0)) < 1e-12 for i in range(3) for j in range(3))

    def leaves(self):
        return [self]

    def to_local(self, x, y, z):
        px, py, pz = x - self.t[0], y - self.t[1], z - self.t[2]
        if self.ident:
            return px, py, pz
        R = self.R
        return (R[0][0] * px + R[1][0] * py + R[2][0] * pz, R[0][1] * px + R[1][1] * py + R[2][1] * pz,
                R[0][2] * px + R[1][2] * py + R[2][2] * pz)

    def to_local_np(self, P, np):
        Q = P - np.asarray(self.t)
        if self.ident:
            return Q[:, 0], Q[:, 1], Q[:, 2]
        L = Q @ np.asarray(self.R)  # rows: (R^T q)^T = q^T R
        return L[:, 0], L[:, 1], L[:, 2]

    def sdf(self, x, y, z, off: float = 0.0) -> float:
        return self.lsdf(*self.to_local(x, y, z)) - off

    def sdf_np(self, P, np, off: float = 0.0, pad: float = 4.0):
        out = self.bdist_np(P, np)
        m = out < pad + max(off, 0.0)
        if m.any():
            out[m] = self.lsdf_np(*self.to_local_np(P[m], np), np=np)
        else:
            return out - off
        return out - off

    def world_box(self, local_pts) -> tuple:
        W = [tuple(a + b for a, b in zip(_mv(self.R, p), self.t)) for p in local_pts]
        return (min(w[0] for w in W), max(w[0] for w in W), min(w[1] for w in W), max(w[1] for w in W),
                min(w[2] for w in W), max(w[2] for w in W))

    def disks_box(self, disks) -> tuple:
        """Exact world box of a solid swept between coaxial disks [(z_local, radius)] about the local Z axis."""
        n = _mv(self.R, (0.0, 0.0, 1.0))
        ext = [r_ * math.sqrt(max(0.0, 1.0 - n[i] * n[i])) for i in range(3) for r_ in (1.0,)]
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for zl, r in disks:
            c = tuple(a + b for a, b in zip(_mv(self.R, (0.0, 0.0, zl)), self.t))
            for i in range(3):
                lo[i] = min(lo[i], c[i] - r * ext[i])
                hi[i] = max(hi[i], c[i] + r * ext[i])
        return (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])


class Box(Prim):
    op = "box"

    def __init__(self, size, rnd, R, t, path, name):
        super().__init__(R, t, path, name)
        self.size = tuple(float(s) for s in size)
        self.h = (self.size[0] / 2, self.size[1] / 2, self.size[2] / 2)
        self.round = max(0.0, min(float(rnd or 0.0), min(self.size) / 2 - 0.5))
        hx, hy, hz = self.h
        self.bbox = self.world_box([(sx * hx, sy * hy, z) for sx in (-1, 1) for sy in (-1, 1) for z in (0.0, self.size[2])])

    def lsdf(self, x, y, z):
        r = self.round
        hx, hy, hz = self.h
        qx, qy, qz = abs(x) - (hx - r), abs(y) - (hy - r), abs(z - hz) - (hz - r)
        out = math.sqrt(max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2 + max(qz, 0.0) ** 2)
        return out + min(max(qx, qy, qz), 0.0) - r

    def lsdf_np(self, x, y, z, np):
        r = self.round
        hx, hy, hz = self.h
        q = np.stack([np.abs(x) - (hx - r), np.abs(y) - (hy - r), np.abs(z - hz) - (hz - r)], 1)
        return np.sqrt((np.maximum(q, 0.0) ** 2).sum(1)) + np.minimum(q.max(1), 0.0) - r


class Cylinder(Prim):
    op = "cylinder"

    def __init__(self, r, h, rnd, R, t, path, name):
        super().__init__(R, t, path, name)
        self.r, self.hgt = float(r), float(h)
        self.round = max(0.0, min(float(rnd or 0.0), min(self.r, self.hgt / 2) - 0.5))
        self.bbox = self.disks_box([(0.0, self.r), (self.hgt, self.r)])

    def lsdf(self, x, y, z):
        rr = self.round
        d1 = math.hypot(x, y) - self.r + rr
        d2 = abs(z - self.hgt / 2) - self.hgt / 2 + rr
        return min(max(d1, d2), 0.0) + math.hypot(max(d1, 0.0), max(d2, 0.0)) - rr

    def lsdf_np(self, x, y, z, np):
        rr = self.round
        d1 = np.hypot(x, y) - self.r + rr
        d2 = np.abs(z - self.hgt / 2) - self.hgt / 2 + rr
        return np.minimum(np.maximum(d1, d2), 0.0) + np.hypot(np.maximum(d1, 0.0), np.maximum(d2, 0.0)) - rr


class Sphere(Prim):
    op = "sphere"

    def __init__(self, r, R, t, path, name):
        super().__init__(R, t, path, name)
        self.r = float(r)
        c = self.t
        self.bbox = (c[0] - self.r, c[0] + self.r, c[1] - self.r, c[1] + self.r, c[2] - self.r, c[2] + self.r)

    def lsdf(self, x, y, z):
        return math.sqrt(x * x + y * y + z * z) - self.r

    def lsdf_np(self, x, y, z, np):
        return np.sqrt(x * x + y * y + z * z) - self.r


class Torus(Prim):
    op = "torus"

    def __init__(self, Rr, r, R, t, path, name):
        super().__init__(R, t, path, name)
        self.Rr, self.r = float(Rr), float(r)
        b = self.disks_box([(0.0, self.Rr)])
        self.bbox = (b[0] - self.r, b[1] + self.r, b[2] - self.r, b[3] + self.r, b[4] - self.r, b[5] + self.r)

    def lsdf(self, x, y, z):
        return math.hypot(math.hypot(x, y) - self.Rr, z) - self.r

    def lsdf_np(self, x, y, z, np):
        return np.hypot(np.hypot(x, y) - self.Rr, z) - self.r


class Revolve(Prim):
    """A closed (r, z) profile turned about the local Z axis; also the cone (a trapezoid profile)."""

    op = "revolve"

    def __init__(self, pts, R, t, path, name, op="revolve", cone=None):
        super().__init__(R, t, path, name)
        self.op = op
        self.cone = cone  # (r1, r2, h) for op cone
        P = [(max(0.0, float(a)), float(b)) for a, b in pts]
        if polygon_area(P) < 0:
            P = P[::-1]
        self.points = P
        n = len(P)
        skip = {i for i in range(n) if P[i][0] < 1e-6 and P[(i + 1) % n][0] < 1e-6}
        self.poly = Poly(P, skip)
        self.bbox = self.disks_box([(z, r) for r, z in P])

    def lsdf(self, x, y, z):
        return self.poly.sdf(math.hypot(x, y), z)

    def lsdf_np(self, x, y, z, np):
        return self.poly.sdf_np(np.hypot(x, y), z)


def _cone_profile(r1, r2, h) -> list:
    P = [(0.0, 0.0)]
    if r1 > 1e-6:
        P.append((r1, 0.0))
    if r2 > 1e-6:
        P.append((r2, h))
    P.append((0.0, h))
    return P


class Extrude(Prim):
    op = "extrude"

    def __init__(self, pts, h, axis, rnd, R, t, path, name):
        super().__init__(R, t, path, name)
        P = [(float(a), float(b)) for a, b in pts]
        if polygon_area(P) < 0:
            P = P[::-1]
        self.points = P
        self.hgt, self.axis = float(h), axis or "z"
        rc = max(0.0, min(float(rnd or 0.0), self.hgt / 2 - 0.5))
        self.round_corner = rc
        if rc > 0.05:
            self.start, self.cmds, mc = fillet_path(P, rc)
            self.outline = path_points(self.start, self.cmds)
            self.round_cap = max(0.0, min(rc, mc))
        else:
            self.start, self.cmds, self.outline, self.round_cap = None, None, list(P), 0.0
        self.poly = Poly(self.outline)
        if self.axis == "z":
            self.w0, self.w1 = 0.0, self.hgt
        else:
            self.w0, self.w1 = -self.hgt / 2, self.hgt / 2
        self.bbox = self.world_box([self.lift(a, b, w) for a, b in P for w in (self.w0, self.w1)])

    def lift(self, a, b, w) -> tuple:
        """Local 3D point of outline point (a, b) at extrusion coordinate w."""
        if self.axis == "z":
            return (a, b, w)
        if self.axis == "y":
            return (a, w, b)
        return (w, a, b)

    def _uvw(self, x, y, z):
        if self.axis == "z":
            return x, y, z
        if self.axis == "y":
            return x, z, y
        return y, z, x

    def lsdf(self, x, y, z):
        u, v, w = self._uvw(x, y, z)
        d2 = self.poly.sdf(u, v)
        dw = abs(w - (self.w0 + self.w1) / 2) - self.hgt / 2
        rc = self.round_cap
        if rc > 0.0:
            q0, q1 = d2 + rc, dw + rc
            return min(max(q0, q1), 0.0) + math.hypot(max(q0, 0.0), max(q1, 0.0)) - rc
        return min(max(d2, dw), 0.0) + math.hypot(max(d2, 0.0), max(dw, 0.0))

    def lsdf_np(self, x, y, z, np):
        u, v, w = self._uvw(x, y, z)
        d2 = self.poly.sdf_np(u, v)
        dw = np.abs(w - (self.w0 + self.w1) / 2) - self.hgt / 2
        rc = self.round_cap
        q0, q1 = d2 + rc, dw + rc
        return np.minimum(np.maximum(q0, q1), 0.0) + np.hypot(np.maximum(q0, 0.0), np.maximum(q1, 0.0)) - rc


class Union(Node):
    def __init__(self, kids, blend, path, name):
        self.kids = [k for k in kids if k.bbox is not None]
        self.blend, self.path, self.name = float(blend or 0.0), path, name
        if not self.kids:
            self.bbox = None
        else:
            bs = [k.bbox for k in self.kids]
            self.bbox = (min(b[0] for b in bs), max(b[1] for b in bs), min(b[2] for b in bs), max(b[3] for b in bs),
                         min(b[4] for b in bs), max(b[5] for b in bs))

    def leaves(self):
        return [l for k in self.kids for l in k.leaves()]

    def sdf(self, x, y, z, off: float = 0.0) -> float:
        r = self.blend if off == 0.0 else 0.0  # the cavity (off < 0) and grown cut tools carry no blend
        best = math.inf
        for k in self.kids:
            bd = k.bdist(x, y, z)
            if bd > 0.0 and bd - off >= best and bd - off >= r:  # sdf >= box distance outside the box: min unchanged
                continue
            d = k.sdf(x, y, z, off)
            if r > 0.0 and best < math.inf:
                best = _round_union(best, d, r)
            elif d < best:
                best = d
        return best

    def sdf_np(self, P, np, off: float = 0.0, pad: float = 4.0):
        r = self.blend if off == 0.0 else 0.0
        acc = None
        for k in self.kids:
            d = k.sdf_np(P, np, off, pad)
            if acc is None:
                acc = d
            elif r > 0.0:
                u = np.maximum(r, np.minimum(acc, d)) - np.hypot(np.maximum(r - acc, 0.0), np.maximum(r - d, 0.0))
                acc = u
            else:
                acc = np.minimum(acc, d)
        return acc


def _round_union(a: float, b: float, r: float) -> float:
    """Rolling-ball (radius r) fillet union of two distance fields (hg_sdf fOpUnionRound)."""
    return max(r, min(a, b)) - math.hypot(max(r - a, 0.0), max(r - b, 0.0))


class Cut(Node):
    def __init__(self, base, minus, path, name):
        self.base, self.path, self.name = base, path, name
        self.minus = [m for m in minus if m.bbox is not None]
        self.bbox = base.bbox

    def leaves(self):
        return self.base.leaves() + [l for m in self.minus for l in m.leaves()]

    def sdf(self, x, y, z, off: float = 0.0) -> float:
        a = self.base.sdf(x, y, z, off)
        res = a
        for m in self.minus:
            bd = m.bdist(x, y, z)
            if bd > 0.0 and bd + off >= -res:  # the tool (grown by -off) cannot reach deeper than res here
                continue
            b = m.sdf(x, y, z, -off)
            if -b > res:
                res = -b
        return res

    def sdf_np(self, P, np, off: float = 0.0, pad: float = 4.0):
        res = self.base.sdf_np(P, np, off, pad)
        for m in self.minus:
            res = np.maximum(res, -m.sdf_np(P, np, -off, pad))
        return res


class Intersect(Node):
    def __init__(self, kids, path, name):
        self.kids, self.path, self.name = kids, path, name
        if any(k.bbox is None for k in kids):
            self.bbox = None
            return
        bs = [k.bbox for k in kids]
        b = (max(q[0] for q in bs), min(q[1] for q in bs), max(q[2] for q in bs), min(q[3] for q in bs),
             max(q[4] for q in bs), min(q[5] for q in bs))
        self.bbox = b if (b[0] < b[1] and b[2] < b[3] and b[4] < b[5]) else None

    def leaves(self):
        return [l for k in self.kids for l in k.leaves()]

    def sdf(self, x, y, z, off: float = 0.0) -> float:
        return max(k.sdf(x, y, z, off) for k in self.kids)

    def sdf_np(self, P, np, off: float = 0.0, pad: float = 4.0):
        res = None
        for k in self.kids:
            d = k.sdf_np(P, np, off, pad)
            res = d if res is None else np.maximum(res, d)
        return res


class _Empty(Node):
    def __init__(self, path):
        self.path, self.bbox = path, None

    def sdf(self, x, y, z, off: float = 0.0) -> float:
        return math.inf

    def sdf_np(self, P, np, off: float = 0.0, pad: float = 4.0):
        return np.full(len(P), np.inf)


def _build(node: dict, path: str, R, t) -> Node:
    op = node["op"]
    nm = node.get("name")
    if op == "move":
        to = tuple(float(v) for v in (node.get("to") or (0.0, 0.0, 0.0)))
        rot = node.get("rot") or (0.0, 0.0, 0.0)
        Rm = rot_matrix(rot) if any(float(v) for v in rot) else _I3
        R2 = _mm(R, Rm)
        t2 = tuple(a + b for a, b in zip(_mv(R, to), t))
        return _build(node["node"], f"{path}.node", R2, t2)
    if op == "union":
        return Union([_build(k, f"{path}.nodes[{i}]", R, t) for i, k in enumerate(node["nodes"])], node.get("blend"), path, nm)
    if op == "intersect":
        return Intersect([_build(k, f"{path}.nodes[{i}]", R, t) for i, k in enumerate(node["nodes"])], path, nm)
    if op == "cut":
        return Cut(_build(node["node"], f"{path}.node", R, t),
                   [_build(k, f"{path}.minus[{i}]", R, t) for i, k in enumerate(node["minus"])], path, nm)
    if op == "box":
        return Box(node["size"], node.get("round"), R, t, path, nm)
    if op == "cylinder":
        return Cylinder(node["r"], node["h"], node.get("round"), R, t, path, nm)
    if op == "sphere":
        return Sphere(node["r"], R, t, path, nm)
    if op == "torus":
        return Torus(node["R"], node["r"], R, t, path, nm)
    if op == "cone":
        r1, r2, h = float(node["r1"]), float(node["r2"]), float(node["h"])
        return Revolve(_cone_profile(r1, r2, h), R, t, path, nm, op="cone", cone=(r1, r2, h))
    if op == "extrude":
        return Extrude(node["points"], node["h"], node.get("axis", "z"), node.get("round"), R, t, path, nm)
    if op == "revolve":
        return Revolve(node["points"], R, t, path, nm)
    raise CsgError([{"path": path, "msg": f"unknown op {op!r}", "code": "csg_schema"}])


class Shape:
    """A compiled, validated tree: the outer form cut flat at the table (z >= 0)."""

    def __init__(self, tree: dict, root: Node, path: str):
        self.tree, self.root, self.path = tree, root, path
        b = root.bbox
        self.bounds = None if b is None else (b[0], b[1], b[2], b[3], max(b[4], 0.0), b[5])

    def sdf(self, x: float, y: float, z: float) -> float:
        return max(self.root.sdf(x, y, z, 0.0), -z)

    def cavity_sdf(self, x: float, y: float, z: float, wall: float) -> float:
        """The hollow as the CAD builds it (every node inset by `wall`, cut tools grown by it, no blends), floor at wall."""
        return max(self.root.sdf(x, y, z, -wall), wall - z)

    def sdf_np(self, P, pad: float = 4.0):
        import numpy as np

        P = np.asarray(P, float).reshape(-1, 3)
        return np.maximum(self.root.sdf_np(P, np, 0.0, pad), -P[:, 2])

    def leaves(self) -> list:
        return self.root.leaves()


def compile_tree(tree: Any, path: str = "body.csg", validate: bool = True) -> Shape:
    if validate:
        errs = validate_tree(tree, path)
        if errs:
            raise CsgError(errs)
    root = _build(tree, path, _I3, (0.0, 0.0, 0.0))
    return Shape(tree, root, path)


# =============================================================================================== scaling
_LEN_KEYS = {"size", "round", "r", "h", "r1", "r2", "R", "blend", "to"}


def scale_tree(tree: Any, k: float) -> Any:
    """A copy of the tree with every length (sizes, radii, heights, outline points, move `to`, blend) times k."""
    if isinstance(tree, list):
        return [scale_tree(v, k) for v in tree]
    if not isinstance(tree, dict):
        return tree
    out = {}
    for key, v in tree.items():
        if key in ("node",):
            out[key] = scale_tree(v, k)
        elif key in ("nodes", "minus"):
            out[key] = [scale_tree(n, k) for n in v] if isinstance(v, list) else v
        elif key == "points" and isinstance(v, list):
            out[key] = [[round(float(c) * k, 3) for c in p] if isinstance(p, list) else p for p in v]
        elif key in _LEN_KEYS:
            if isinstance(v, list):
                out[key] = [round(float(c) * k, 3) if _isnum(c) else c for c in v]
            elif _isnum(v):
                out[key] = round(float(v) * k, 3)
            else:
                out[key] = v
        else:
            out[key] = v
    return out


# =============================================================================================== grid checks
_CHECK_CACHE: dict[str, tuple[list, dict]] = {}


def solid_checks(tree: Any, kind: str = "gadget", path: str = "body.csg", info: Optional[dict] = None,
                 wall: float = 2.4) -> list[dict]:
    """Checks on the finished outer solid (a grid of its distance field): not empty, stands on the table with enough
    footprint, ONE connected solid, no part thinner than 2 mm. [] when it passes. Cached per tree. Pass `info` (a dict)
    to receive {grid_mm, volume_cm3, bounds, components, footprint_cm2, solid_pieces, cavity_main}: the hollow the CAD
    keeps is the biggest connected piece of the inset tree; `solid_pieces` names the nodes whose own hollow does not
    join it (they print solid), `cavity_main` = (origin, h, mask) of that main hollow on the grid."""
    key = json.dumps(tree, sort_keys=True, separators=(",", ":")) + "|" + kind + "|" + path + f"|{float(wall):.3f}"
    hit = _CHECK_CACHE.get(key)
    if hit is None:
        try:
            hit = _solid_checks(tree, kind, path, float(wall))
        except CsgError as e:
            hit = (e.errors, {})
        if len(_CHECK_CACHE) > 64:
            _CHECK_CACHE.clear()
        _CHECK_CACHE[key] = hit
    if info is not None:
        info.update(hit[1])
    return [dict(e) for e in hit[0]]


def _names(leaves: list) -> str:
    out = [l.label() for l in leaves[:3]]
    return ", ".join(out) + (" and others" if len(leaves) > 3 else "")


def _solid_checks(tree: Any, kind: str, path: str, wall: float = 2.4) -> tuple[list, dict]:
    import numpy as np

    errs: list[dict] = []
    shape = compile_tree(tree, path)
    b = shape.bounds
    if b is None or b[5] <= 0.5:
        _e(errs, path, "the shape is empty above the table (nothing at z >= 0)",
           "check the cuts and intersects (they may remove everything) and keep the shape at z >= 0 (a box rises from z = 0)",
           "csg_empty")
        return errs, {}
    x0, x1, y0, y1, z0, z1 = b
    vol = max((x1 - x0) * (y1 - y0) * (z1 - z0), 1.0)
    h = max(1.25, (vol / 450000.0) ** (1.0 / 3.0))
    xs = np.arange(x0 + h / 2, x1, h)
    ys = np.arange(y0 + h / 2, y1, h)
    zs = np.arange(max(z0, 0.0) + h / 2, z1, h)
    if not len(xs) or not len(ys) or not len(zs):
        _e(errs, path, "the shape is too small to build", "make it at least a few millimetres in every direction", "csg_empty")
        return errs, {}
    G = np.stack(np.meshgrid(xs, ys, zs, indexing="ij"), -1).reshape(-1, 3)
    D = shape.sdf_np(G)
    S = (D <= 0.0).reshape(len(xs), len(ys), len(zs))
    info = {"grid_mm": round(h, 2), "volume_cm3": round(float(S.sum()) * h ** 3 / 1000.0, 1),
            "bounds": [round(v, 1) for v in b]}
    if not S.any():
        _e(errs, path, "the shape is empty above the table (nothing at z >= 0)",
           "check the cuts and intersects (they may remove everything) and keep the shape at z >= 0", "csg_empty")
        return errs, info
    # ---- footprint on the table
    h2 = min(h, 1.0)
    fx = np.arange(x0 + h2 / 2, x1, h2)
    fy = np.arange(y0 + h2 / 2, y1, h2)
    F = np.stack(np.meshgrid(fx, fy, [0.5], indexing="ij"), -1).reshape(-1, 3)
    area = float((shape.sdf_np(F) <= 0.0).sum()) * h2 * h2
    info["footprint_cm2"] = round(area / 100.0, 1)
    need = FOOTPRINT_MIN.get(kind, FOOTPRINT_MIN["gadget"])
    if area <= 0.0:
        zl = float(zs[np.nonzero(S.any(axis=(0, 1)))[0][0]]) - h / 2
        _e(errs, path, f"the shape does not stand on the table: its lowest part is at about z = {zl:.0f} mm",
           "move it down so a flat underside reaches z = 0 (a box, cylinder or extrude starting at z = 0), or add a base",
           "csg_floating")
    elif area < need - 1e-6:
        _e(errs, path, f"the footprint on the table is only {area / 100:.1f} cm2; a {kind} needs at least {need / 100:.0f} cm2 "
           "to stand steadily", "give it a wider flat base at z = 0 (for example a box or cylinder rising from z = 0)",
           "csg_footprint")
    # ---- one connected solid
    lab, n = _label(S, np)
    info["components"] = int(n)
    if n > 1:
        sizes = np.bincount(lab.ravel())[1:]
        main = int(np.argmax(sizes)) + 1
        leaves = shape.leaves()
        order = [i + 1 for i in np.argsort(-sizes) if i + 1 != main][:3]
        for c in order:
            idx = np.argwhere(lab == c)
            pts = np.stack([xs[idx[:, 0]], ys[idx[:, 1]], zs[idx[:, 2]]], 1)
            cen = pts.mean(0)
            who = [l for l in leaves if (l.sdf_np(pts, np) <= h * 0.5).any()]
            v = len(idx) * h ** 3 / 1000.0
            _e(errs, path, f"the shape is not one solid: a separate piece of about {max(v, 0.1):.1f} cm3 around "
               f"({cen[0]:.0f}, {cen[1]:.0f}, {cen[2]:.0f}) mm" + (f" (from {_names(who)})" if who else "")
               + " does not touch the main body", "move or enlarge it so it overlaps the main body by at least 2 mm "
               "(pieces only join where they overlap), or remove it", "csg_disconnected")
    # ---- the hollow: the CAD keeps its biggest connected piece; pieces whose hollow does not join it print solid
    try:
        Dc = np.maximum(shape.root.sdf_np(G, np, -wall), wall - G[:, 2])
        Sc = (Dc <= 0.0).reshape(S.shape)
        labc, nc = _label(Sc, np)
        if nc:
            sz = np.bincount(labc.ravel())[1:]
            mainc = int(np.argmax(sz)) + 1
            M = labc == mainc
            info["cavity_main"] = ((float(xs[0]), float(ys[0]), float(zs[0])), float(h), _dilate(M, np))
            info["cavity_cm3"] = round(float(sz.max()) * h ** 3 / 1000.0, 1)
            pieces = []
            for c in [i + 1 for i in np.argsort(-sz) if i + 1 != mainc]:
                if sz[c - 1] * h ** 3 < 1500.0:  # under 1.5 cm3: a sliver of hollow, nothing mounts there
                    continue
                idx = np.argwhere(labc == c)
                pts = np.stack([xs[idx[:, 0]], ys[idx[:, 1]], zs[idx[:, 2]]], 1)
                who = [l for l in shape.leaves() if (l.sdf_np(pts, np) <= 0.0).mean() > 0.5]
                cen = pts.mean(0)
                pieces.append({"nodes": [l.path for l in who], "names": [l.name for l in who if l.name],
                               "center": [round(float(v), 1) for v in cen], "cm3": round(float(sz[c - 1]) * h ** 3 / 1000.0, 1),
                               "label": _names(who) or f"the part around ({cen[0]:.0f}, {cen[1]:.0f}, {cen[2]:.0f}) mm"})
            info["solid_pieces"] = pieces
    except Exception as exc:  # noqa: BLE001 - the hollow analysis must never block the outer checks
        info["cavity_error"] = str(exc)[:200]
    # ---- thin parts (the finished outer solid must be >= MIN_FEATURE thick everywhere)
    thin = _thin(shape, G[D <= 0.0], D[D <= 0.0], np)
    if thin is not None:
        cen, t, cnt = thin
        who = [l for l in shape.leaves() if abs(l.sdf(*cen)) <= 3.0]
        _e(errs, path, f"part of the shape around ({cen[0]:.0f}, {cen[1]:.0f}, {cen[2]:.0f}) mm is only about {t:.1f} mm "
           f"thick" + (f" (near {_names(who)})" if who else "") + f"; the thinnest printable part is {MIN_FEATURE:g} mm",
           "make it at least 2 mm thick (bigger sizes), or keep cuts at least 2 mm from the outside", "csg_thin")
    return errs, info


def _thin(shape: Shape, Q, DQ, np) -> Optional[tuple]:
    """Near-surface solid points whose inward ray leaves the solid again within MIN_FEATURE: (centre, thickness, count)."""
    m = DQ > -1.0
    if not m.any():
        return None
    Q, s = Q[m], DQ[m]
    if len(Q) > 120000:
        Q, s = Q[:: int(math.ceil(len(Q) / 120000))], s[:: int(math.ceil(len(Q) / 120000))]
    e = 0.2
    g = np.zeros_like(Q)
    for i in range(3):
        dv = np.zeros(3)
        dv[i] = e
        g[:, i] = shape.sdf_np(Q + dv) - shape.sdf_np(Q - dv)
    nl = np.linalg.norm(g, axis=1)
    ok = nl > 1e-9
    n = np.zeros_like(g)
    n[ok] = g[ok] / nl[ok, None]
    # march inward (-n) from the surface point (q - n*s is about on the surface): thickness = distance to the far side
    t = np.zeros(len(Q))
    alive = ok.copy()
    thick = np.full(len(Q), np.inf)
    base = Q - n * s[:, None]  # projected out onto the surface (s <= 0, n points outward)
    for _ in range(14):
        if not alive.any():
            break
        P = base[alive] - n[alive] * t[alive, None]
        d = shape.sdf_np(P)
        idx = np.nonzero(alive)[0]
        out = (d > 0.02) & (t[alive] > 0.3)
        thick[idx[out]] = t[alive][out]
        alive[idx[out]] = False
        step = np.maximum(-d, 0.15)
        t[idx] = t[idx] + np.where(out, 0.0, step)
        far = t > MIN_FEATURE + 0.6
        alive &= ~far
    bad = thick < MIN_FEATURE - 0.2  # the march overshoots the far side by <= 0.17 mm
    cnt = int(bad.sum())
    if cnt < 4:
        return None
    sel = Q[bad]
    tb = thick[bad]
    k = int(np.argmin(tb))
    near = np.linalg.norm(sel - sel[k], axis=1) < 6.0
    cen = sel[near].mean(0)
    return (float(cen[0]), float(cen[1]), float(cen[2])), float(np.median(tb[near])), cnt


def _dilate(M, np):
    """M grown by one cell (26-neighbourhood): grid lookups near the hollow's edge stay inside it."""
    out = M.copy()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx or dy or dz:
                    r = np.roll(np.roll(np.roll(M, dx, 0), dy, 1), dz, 2)
                    if dx:
                        r[0 if dx == 1 else -1, :, :] = False
                    if dy:
                        r[:, 0 if dy == 1 else -1, :] = False
                    if dz:
                        r[:, :, 0 if dz == 1 else -1] = False
                    out |= r
    return out


def in_main_hollow(info: dict, x: float, y: float, z: float) -> bool:
    """True when (x, y, z) lies in (or within one grid cell of) the hollow piece the CAD keeps; True without grid data."""
    cm = info.get("cavity_main") if info else None
    if not cm:
        return True
    (x0, y0, z0), h, M = cm
    i, j, k = int(round((x - x0) / h)), int(round((y - y0) / h)), int(round((z - z0) / h))
    if i < 0 or j < 0 or k < 0 or i >= M.shape[0] or j >= M.shape[1] or k >= M.shape[2]:
        return False
    return bool(M[i, j, k])


def _label(S, np):
    try:
        from scipy import ndimage  # type: ignore

        lab, n = ndimage.label(S)
        return lab, int(n)
    except Exception:  # noqa: BLE001 - the web image has no scipy: min-label propagation (6-connected)
        pass
    L = np.where(S, np.arange(1, S.size + 1).reshape(S.shape), 0).astype(np.int64)
    big = S.size + 5
    for _ in range(10000):
        C = np.where(S, L, big)
        N = C.copy()
        for ax in range(3):
            for sh in (1, -1):
                R = np.roll(C, sh, axis=ax)
                idx = [slice(None)] * 3
                idx[ax] = 0 if sh == 1 else -1
                R[tuple(idx)] = big
                np.minimum(N, R, out=N)
        N = np.where(S, N, 0)
        flat = N.ravel()
        for _j in range(3):  # pointer jumping: take the label of the cell a label names (same component, never larger)
            m = flat > 0
            flat[m] = flat[flat[m] - 1]
        N = flat.reshape(S.shape)
        if np.array_equal(N, L):
            break
        L = N
    lab = L
    lab = np.where(S, lab, 0)
    u = np.unique(lab[lab > 0])
    remap = np.zeros(S.size + 3, dtype=np.int64)
    remap[u] = np.arange(1, len(u) + 1)
    return remap[lab], int(len(u))


__all__ = ["CsgError", "Shape", "compile_tree", "validate_tree", "solid_checks", "scale_tree", "fillet_path", "path_points",
           "in_main_hollow",
           "rot_matrix", "Poly", "MAX_NODES", "MAX_DEPTH", "MAX_POINTS", "MIN_FEATURE", "OPS", "PRIMS"]
