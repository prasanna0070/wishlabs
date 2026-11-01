"""Display-only cable routes from backplane slot connectors to module connectors.

Free space = inside the shell cavity, at least (cable radius + margin) away from every rigid component and from the
shell's own walls, bosses and pads (signed distances from VTK). Routes are shortest paths over the free voxels,
string-pulled, smoothed, and swept as a circle along a spline. Lengths are recorded and compared with the harness.
"""

from __future__ import annotations

import math

import cadquery as cq
import numpy as np

from .geom import V, camel, cyl_axis, sphere, tessellate, unit
from .parts import Part

CABLE_D = {2: 2.6, 3: 3.0, 4: 3.4, 5: 3.7, 6: 4.0, 7: 4.3, 8: 4.6}
CABLE_COLOR = {2: (0.62, 0.14, 0.12), 3: (0.20, 0.20, 0.22), 4: (0.16, 0.30, 0.62), 6: (0.30, 0.42, 0.20), 8: (0.42, 0.30, 0.52)}


def _polydata(shapes, tol=0.4, ang=0.6):
    import pyvista as pv

    vs, fs, off = [], [], 0
    for s in shapes:
        v, _, t, _ = tessellate(s, tol, ang, fresh=True)
        if not len(t):
            continue
        vs.append(v)
        fs.append(np.hstack([np.full((len(t), 1), 3), t + off]))
        off += len(v)
    if not vs:
        return None
    return pv.PolyData(np.vstack(vs), np.vstack(fs).ravel())


def _signed(poly, pts):
    import pyvista as pv

    cloud = pv.PolyData(pts)
    return np.asarray(cloud.compute_implicit_distance(poly)["implicit_distance"])


class FreeSpace:
    def __init__(self, cavity: cq.Shape, obstacles: list, h: float = 2.5, margin: float = 2.6):
        b = cavity.BoundingBox()
        self.h = h
        self.o = np.array([b.xmin, b.ymin, b.zmin])
        self.n = np.maximum(np.ceil(np.array([b.xlen, b.ylen, b.zlen]) / h).astype(int) + 1, 2)
        ii = np.indices(self.n).reshape(3, -1).T
        self.pts = self.o + ii * h
        cav = _polydata([cavity], 0.3, 0.5)
        dc = _signed(cav, self.pts)
        inside = dc < -margin
        do = np.full(len(self.pts), 1e3)
        obs = _polydata(obstacles)
        if obs is not None and inside.any():
            do[inside] = _signed(obs, self.pts[inside])  # only points already inside the cavity
        self.clear = np.minimum(-dc, do)  # distance to the nearest wall/obstacle (positive = free)
        self.free = self.clear > margin
        self.margin = margin

    def idx(self, p):
        k = np.round((np.asarray(p) - self.o) / self.h).astype(int)
        return np.clip(k, 0, self.n - 1)

    def flat(self, k):
        return (k[..., 0] * self.n[1] + k[..., 1]) * self.n[2] + k[..., 2]

    def nearest_free(self, p, rmax=12.0):
        k = self.idx(p)
        best, bd = None, 1e9
        r = int(math.ceil(rmax / self.h))
        sl = [slice(max(k[i] - r, 0), min(k[i] + r + 1, self.n[i])) for i in range(3)]
        sub = np.indices([s.stop - s.start for s in sl]).reshape(3, -1).T + np.array([s.start for s in sl])
        f = self.free[self.flat(sub)]
        if not f.any():
            return None
        cand = sub[f]
        d = np.linalg.norm(self.o + cand * self.h - p, axis=1)
        return cand[int(np.argmin(d))]

    def segment_free(self, a, b) -> bool:
        L = np.linalg.norm(b - a)
        n = max(int(L / (self.h * 0.5)), 1)
        ts = np.linspace(0, 1, n + 1)
        pts = a[None] + (b - a)[None] * ts[:, None]
        return bool(np.all(self.free[self.flat(self.idx(pts))]))


def _graph(fs: FreeSpace):
    from scipy.sparse import coo_matrix

    nx, ny, nz = fs.n
    N = nx * ny * nz
    free = fs.free.reshape(nx, ny, nz)
    rows, cols, w = [], [], []
    idx = np.arange(N).reshape(nx, ny, nz)
    # prefer to run close to walls and floors (as a tidy harness does): cost rises with distance from the nearest surface
    cl = fs.clear.reshape(nx, ny, nz)
    pen = 1.0 + 0.09 * np.clip(cl - fs.margin, 0.0, 40.0)
    for d in [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, -1, 0), (1, 0, 1), (1, 0, -1), (0, 1, 1), (0, 1, -1)]:
        sa = tuple(slice(0, n - di) if di >= 0 else slice(-di, n) for di, n in zip(d, (nx, ny, nz)))
        sb = tuple(slice(di, n) if di >= 0 else slice(0, n + di) for di, n in zip(d, (nx, ny, nz)))
        m = free[sa] & free[sb]
        a = idx[sa][m]
        b = idx[sb][m]
        length = math.sqrt(sum(x * x for x in d))
        wt = length * 0.5 * (pen[sa][m] + pen[sb][m])
        rows += [a, b]
        cols += [b, a]
        w += [wt, wt]
    rows, cols, w = np.concatenate(rows), np.concatenate(cols), np.concatenate(w)
    return coo_matrix((w, (rows, cols)), shape=(N, N)).tocsr()


def _shortest(fs: FreeSpace, G, ka, kb):
    from scipy.sparse.csgraph import dijkstra

    a, b = int(fs.flat(ka)), int(fs.flat(kb))
    dist, pred = dijkstra(G, indices=a, return_predecessors=True, limit=np.inf)
    if not np.isfinite(dist[b]):
        return None
    path = [b]
    while path[-1] != a:
        p = pred[path[-1]]
        if p < 0:
            return None
        path.append(p)
    path = path[::-1]
    nx, ny, nz = fs.n
    k = np.stack([np.array(path) // (ny * nz), (np.array(path) // nz) % ny, np.array(path) % nz], 1)
    return fs.o + k * fs.h


def _hug_ok(fs: FreeSpace, a, b, slack: float = 3.5) -> bool:
    """Segment is free and never strays much further from surfaces than its end points do."""
    if not fs.segment_free(a, b):
        return False
    L = np.linalg.norm(b - a)
    n = max(int(L / (fs.h * 0.5)), 1)
    ts = np.linspace(0, 1, n + 1)
    pts = a[None] + (b - a)[None] * ts[:, None]
    c = fs.clear[fs.flat(fs.idx(pts))]
    lim = max(c[0], c[-1]) + slack
    return bool(np.all(c <= lim))


def _pull(fs: FreeSpace, pts: np.ndarray) -> np.ndarray:
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        j = min(len(pts) - 1, i + 40)
        while j > i + 1 and not _hug_ok(fs, pts[i], pts[j]):
            j -= 1
        out.append(pts[j])
        i = j
    return np.array(out)


def _densify(pts: np.ndarray, step: float = 6.0) -> np.ndarray:
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        L = np.linalg.norm(b - a)
        n = max(int(L / step), 1)
        for t in np.linspace(0, 1, n + 1)[1:]:
            out.append(a + (b - a) * t)
    return np.array(out)


def _smooth(pts: np.ndarray, it: int = 3) -> np.ndarray:
    p = pts.copy()
    for _ in range(it):
        q = p.copy()
        q[1:-1] = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
        p = q
    return p


def sweep_cable(points: np.ndarray, d: float) -> cq.Shape:
    r = d / 2
    try:
        path = cq.Wire.assembleEdges([cq.Edge.makeSpline([V(p) for p in points])])
        t0 = unit(points[1] - points[0])
        prof = cq.Wire.makeCircle(r, V(points[0]), V(t0))
        s = cq.Solid.sweep(prof, [], path, makeSolid=True, isFrenet=True)
        if s.isValid() and s.Volume() > 0.5 * math.pi * r * r * path.Length():
            return s
    except Exception:
        pass
    sols = []
    for a, b in zip(points[:-1], points[1:]):
        L = float(np.linalg.norm(b - a))
        if L > 1e-3:
            sols.append(cyl_axis(a, b - a, L, r))
    sols += [sphere(p, r) for p in points[1:-1]]
    return cq.Compound.makeCompound(sols)


def route_all(M, kits, cart, params, cavity) -> tuple[list, list]:
    warns = []
    obstacles = [M.shapes["shell"], M.shapes["other"]] + [s for p in M.parts if p.name not in ("PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate")
                                                           for _, s, _ in p.subparts]
    fs = FreeSpace(cavity, obstacles, h=3.0, margin=2.6)
    G = _graph(fs)
    routes = []
    bpref = params.get("backplane_ref", "BP1")
    for k in kits:
        feat = next((f for f in params["features"] if f["id"] == k.feature_id), None)
        if feat is None or k.cable_end is None:
            continue
        slot = feat.get("slot") or (feat.get("harness") or {}).get("slot")
        if slot not in cart.slot_ends:
            warns.append(f"{k.ref}: no backplane connector position for slot {slot}; cable not drawn")
            continue
        h = feat.get("harness") or {}
        pins = int(h.get("pins") or 3)
        d = CABLE_D.get(pins, 3.4)
        a_anchor = np.array(cart.anchors[f"{bpref}.{slot}"])
        a_pt, a_dir = cart.slot_ends[slot]
        b_anchor = np.array(k.anchors.get(f"{k.ref}.CONN", k.cable_end[0]))
        b_pt = k.cable_end[0] + k.cable_end[1] * 6.0
        ka, kb = fs.nearest_free(a_pt), fs.nearest_free(b_pt)
        if ka is None or kb is None:
            warns.append(f"{k.ref}: connector end has no free space nearby; cable not drawn")
            continue
        mid = _shortest(fs, G, ka, kb)
        if mid is None:
            warns.append(f"{k.ref}: no free path from {slot} to the module; cable not drawn")
            continue
        mid = _pull(fs, mid)
        pts = np.vstack([a_anchor, a_pt, mid, b_pt, b_anchor])
        keep = [pts[0]]
        for p in pts[1:]:
            if np.linalg.norm(p - keep[-1]) > 1.0:
                keep.append(p)
        raw = _densify(np.array(keep), 4.0)
        pts = raw
        for it in (6, 3, 1, 0):
            cand = _smooth(raw, it) if it else raw
            cand[0], cand[-1] = a_anchor, b_anchor
            s_ = np.cumsum(np.r_[0, np.linalg.norm(np.diff(cand, axis=0), axis=1)])
            mid_ = cand[(s_ > 8) & (s_ < s_[-1] - 8)]
            if not len(mid_) or float(fs.clear[fs.flat(fs.idx(mid_))].min()) >= d / 2 + 0.3:
                pts = cand
                break
            pts = cand
        length = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
        cable_len = float(h.get("length_mm") or 150)
        solid = sweep_cable(pts, d)
        nm = f"REFERENCE_Cable{camel(k.ref.lower())}"
        part = Part(nm, "REFERENCE", f"{h.get('cable') or 'cable'} (display-only route)", CABLE_COLOR.get(pins, (0.2, 0.2, 0.22)),
                    ref=k.ref, feature_id=k.feature_id, spec_path=feat.get("spec_path"))
        part.add("Cable", solid)
        part.notes.append("display-only occupied-space envelope; real cable bends are not modelled")
        M.parts.append(part)
        # wall clearance along the centreline (excluding 8 mm at each end, where it meets the plugs)
        s = np.cumsum(np.r_[0, np.linalg.norm(np.diff(pts, axis=0), axis=1)])
        inner = pts[(s > 8) & (s < s[-1] - 8)]
        clear = float(fs.clear[fs.flat(fs.idx(inner))].min()) if len(inner) else None
        routes.append({
            "id": h.get("id") or f"H_{k.ref}", "component": nm, "from": f"{bpref}.{slot}", "to": f"{k.ref}.CONN",
            "cable": h.get("cable") or f"{pins}p lead", "pins": pins, "diameter": d, "length_mm": round(length, 1),
            "cable_length_mm": cable_len, "length_ok": length + 20.0 <= cable_len,
            "min_clearance_mm": None if clear is None else round(clear - d / 2, 2),
            "points": [[round(float(x), 3) for x in p] for p in pts],
            "note": "display-only route; 20 mm allowance for plug entry and slack",
        })
    return routes, warns
