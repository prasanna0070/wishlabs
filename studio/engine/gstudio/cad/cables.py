"""Display-only cable routes from backplane slot connectors to module connectors.

Free space = inside the shell cavity, at least (cable radius + margin) away from every rigid component and from the
shell's own walls, bosses and pads (signed distances from VTK). Routes are shortest paths over the free voxels,
string-pulled, smoothed, and swept as a circle along a spline. Lengths are recorded and compared with the harness.
"""

from __future__ import annotations

import contextlib
import math
import os
import pickle
import subprocess
import sys

import cadquery as cq
import numpy as np

from .geom import V, camel, cyl_axis, sphere, tessellate, unit
from .parts import Part

CABLE_D = {2: 2.6, 3: 3.0, 4: 3.4, 5: 3.7, 6: 4.0, 7: 4.3, 8: 4.6}
CABLE_COLOR = {2: (0.62, 0.14, 0.12), 3: (0.20, 0.20, 0.22), 4: (0.16, 0.30, 0.62), 6: (0.30, 0.42, 0.20), 8: (0.42, 0.30, 0.52)}


def _mesh_arrays(shapes, tol=0.4, ang=0.6):
    """(verts[N, 3], legacy VTK faces [3, i, j, k, ...]) of the shapes' triangulation, or None."""
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
    return np.ascontiguousarray(np.vstack(vs), dtype=float), np.ascontiguousarray(np.vstack(fs).ravel(), dtype=np.int64)


def _vtk_signed(mesh, pts):
    """VTK implicit distance from points to a triangle mesh (negative inside)."""
    import pyvista as pv

    cloud = pv.PolyData(np.ascontiguousarray(pts, dtype=float))
    return np.array(cloud.compute_implicit_distance(mesh)["implicit_distance"], dtype=float)


def _vtk_mesh(verts, faces):
    import pyvista as pv

    return pv.PolyData(verts, faces, deep=True)


# The worker imports only numpy and pyvista (no cadquery/OCP): the same arrays never crashed in such a process.
_SD_WORKER = """
import pickle, sys
import numpy as np
import pyvista as pv


def _vtk_mesh(verts, faces):
    return pv.PolyData(verts, faces, deep=True)


def _vtk_signed(mesh, pts):
    cloud = pv.PolyData(np.ascontiguousarray(pts, dtype=float))
    return np.array(cloud.compute_implicit_distance(mesh)["implicit_distance"], dtype=float)


import os
out = os.fdopen(os.dup(1), "wb")  # the pipe to the parent; anything VTK prints to stdout goes to stderr instead
os.dup2(2, 1)
inp, meshes = sys.stdin.buffer, {}
while True:
    try:
        req = pickle.load(inp)
    except EOFError:
        break
    try:
        if req[0] == "mesh":
            meshes[req[1]] = _vtk_mesh(req[2], req[3])
            res = ("ok", True)
        else:
            res = ("ok", _vtk_signed(meshes[req[1]], req[2]))
    except Exception as e:
        res = ("err", repr(e))
    pickle.dump(res, out, protocol=pickle.HIGHEST_PROTOCOL)
    out.flush()
"""


class Distances:
    """Signed distances (VTK implicit distance) computed in a fresh Python process that holds the meshes.

    In the CAD stage process, after the OCC build, vtkImplicitPolyDataDistance segfaulted on some bodies, at random:
    on the VM p_wbgxdewi897y v1 died in 7 of 9 runs (p_fzumtfr9a5dn v1 in 1 of 4), also with deep, contiguous copies,
    while the very same mesh and query arrays, saved from a crashing run, never crashed when replayed in a fresh process. A segfault there kills the whole CAD
    stage ("stage process died (signal 11)"). In the worker it only loses the cable routes: the call raises, and
    build() records "cable routing failed" and finishes the CAD. GS_CABLE_SD_INPROCESS=1 restores in-process calls."""

    def __init__(self):
        self.p = None
        self.n = 0
        self.local: dict = {}
        if os.environ.get("GS_CABLE_SD_INPROCESS") == "1":
            return
        try:
            self.p = subprocess.Popen([sys.executable, "-c", _SD_WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        except OSError:  # no worker: in-process, as before
            self.p = None

    def _call(self, req):
        try:
            pickle.dump(req, self.p.stdin, protocol=pickle.HIGHEST_PROTOCOL)
            self.p.stdin.flush()
            kind, res = pickle.load(self.p.stdout)
        except (EOFError, OSError, pickle.UnpicklingError) as e:
            try:
                code = self.p.wait(5)
            except Exception:  # noqa: BLE001
                code = None
            how = f"signal {-code}" if code is not None and code < 0 else f"exit code {code}"
            raise RuntimeError(f"the signed-distance worker died ({how})") from e
        if kind != "ok":
            raise RuntimeError(f"signed-distance worker: {res}")
        return res

    def mesh(self, arrays) -> int:
        self.n += 1
        if self.p is None:
            self.local[self.n] = _vtk_mesh(*arrays)
        else:
            self._call(("mesh", self.n, arrays[0], arrays[1]))
        return self.n

    def signed(self, key: int, pts) -> np.ndarray:
        pts = np.ascontiguousarray(np.atleast_2d(np.asarray(pts, dtype=float)))
        if self.p is None:
            return _vtk_signed(self.local[key], pts)
        return self._call(("sd", key, pts))

    def close(self) -> None:
        if self.p is None:
            return
        with contextlib.suppress(Exception):
            self.p.stdin.close()
        try:
            self.p.wait(10)
        except Exception:  # noqa: BLE001
            self.p.kill()
        with contextlib.suppress(Exception):
            self.p.stdout.close()
        self.p = None


class FreeSpace:
    def __init__(self, cavity: cq.Shape, obstacles: list, h: float = 2.5, margin: float = 2.6, sd: Distances | None = None):
        b = cavity.BoundingBox()
        self.h = h
        self.o = np.array([b.xmin, b.ymin, b.zmin])
        self.n = np.maximum(np.ceil(np.array([b.xlen, b.ylen, b.zlen]) / h).astype(int) + 1, 2)
        ii = np.indices(self.n).reshape(3, -1).T
        self.pts = self.o + ii * h
        self.sd = sd if sd is not None else Distances()
        cav = _mesh_arrays([cavity], 0.3, 0.5)
        if cav is None:
            raise ValueError("the cavity has no triangulation")
        self.cav_key = self.sd.mesh(cav)
        dc = self.sd.signed(self.cav_key, self.pts)
        inside = dc < -margin
        do = np.full(len(self.pts), 1e3)
        obs = _mesh_arrays(obstacles)
        self.obs_key = self.sd.mesh(obs) if obs is not None else None
        if self.obs_key is not None and inside.any():
            do[inside] = self.sd.signed(self.obs_key, self.pts[inside])  # only points already inside the cavity
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

    def exact_clear(self, pts) -> np.ndarray:
        """Exact distance from points to the nearest obstacle or cavity wall (VTK implicit distance, not the voxel grid)."""
        pts = np.atleast_2d(np.asarray(pts, float))
        c = -self.sd.signed(self.cav_key, pts)
        if self.obs_key is not None:
            c = np.minimum(c, self.sd.signed(self.obs_key, pts))
        return c

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


def _push_clear(fs: FreeSpace, pts: np.ndarray, need: float, iters: int = 6) -> np.ndarray:
    """Move interior route points that come closer than `need` to an obstacle along the clearance gradient."""
    p = np.array(pts, float)
    for _ in range(iters):
        inner = np.arange(2, len(p) - 2)
        if not len(inner):
            break
        c = fs.exact_clear(p[inner])
        low = inner[c < need]
        if not len(low):
            break
        h = 0.6
        for i in low:
            g = np.array([float(fs.exact_clear([p[i] + e * h])[0] - fs.exact_clear([p[i] - e * h])[0]) for e in np.eye(3)])
            n = np.linalg.norm(g)
            if n < 1e-6:
                continue
            ci = float(fs.exact_clear([p[i]])[0])
            p[i] = p[i] + g / n * min(need - ci + 0.2, 2.0)
        p[1:-1] = 0.5 * p[1:-1] + 0.25 * (p[:-2] + p[2:])  # keep it smooth
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


LOOP_R = 12.0  # slack coil radius (mm, centre of the bundle): a 24 mm hand-coiled loop
PLUG_ALLOWANCE = 20.0  # mm of each harness kept for plug entry and gentle bends (as length_ok)


def place_slack_loop(fs: FreeSpace, near: list, slack: float, d: float, taken: list):
    """Flat coil holding `slack` mm of a d mm cable, lying on the nearest free spot to the points in `near`.
    Returns (centre, z0, height, r_outer, r_inner, turns) or None."""
    turns = slack / (2 * math.pi * LOOP_R)
    rb = max(d / 2 * math.sqrt(max(turns, 1.0)) * 1.15, d / 2)
    ro, ri, hh = LOOP_R + rb, max(LOOP_R - rb, 1.0), 2 * rb
    best = None
    pts_free = fs.pts[fs.free & (fs.clear > rb + 0.8)]
    if not len(pts_free):
        return None
    for p in near:
        p = np.asarray(p, float)
        dd = np.linalg.norm(pts_free - p, axis=1)
        for k in np.argsort(dd)[:400]:
            c = pts_free[k]
            ring = [c + np.array([rr * math.cos(a), rr * math.sin(a), dz]) for rr in (ri, LOOP_R, ro)
                    for a in np.linspace(0, 2 * math.pi, 48, endpoint=False) for dz in (-hh / 2, 0.0, hh / 2)]
            ok = all(fs.clear[fs.flat(fs.idx(q))] > 1.8 for q in ring)
            if ok:  # exact distances: the voxel grid is 3 mm, a loop must not graze anything between voxels
                ok = float(fs.exact_clear(np.array(ring)).min()) > 1.2  # sample points lie on the loop's own surface
            if ok and all(np.linalg.norm(c[:2] - t[:2]) > ro + t[2] + 1.0 or abs(c[2] - t[3]) > hh for t in taken):
                best = (c, float(dd[k]))
                break
        if best:
            break
    if best is None:
        return None
    c = best[0]
    return c, float(c[2] - hh / 2), hh, ro, ri, turns


def route_all(M, kits, cart, params, cavity, ties: list | None = None) -> tuple[list, list]:
    obstacles = [M.shapes["shell"], M.shapes["other"]] + [s for p in M.parts if p.name not in ("PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate")
                                                           for _, s, _ in p.subparts]
    sd = Distances()
    n_parts = len(M.parts)
    try:
        return _route_all(M, kits, cart, params, FreeSpace(cavity, obstacles, h=3.0, margin=2.6, sd=sd), ties)
    except Exception:
        del M.parts[n_parts:]  # no half-routed cable parts without their route records
        raise
    finally:
        sd.close()


def _route_all(M, kits, cart, params, fs: FreeSpace, ties: list | None) -> tuple[list, list]:
    warns = []
    G = _graph(fs)
    routes = []
    taken_loops: list = []
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
        best_c = None
        for it in (6, 3, 1, 0):
            cand = _smooth(raw, it) if it else raw
            cand[0], cand[-1] = a_anchor, b_anchor
            s_ = np.cumsum(np.r_[0, np.linalg.norm(np.diff(cand, axis=0), axis=1)])
            dense = _densify(cand, 0.8)
            sd = np.cumsum(np.r_[0, np.linalg.norm(np.diff(dense, axis=0), axis=1)])
            mid_ = dense[(sd > 8) & (sd < sd[-1] - 8)]
            cl = float(fs.exact_clear(mid_).min()) if len(mid_) else 99.0  # exact, not the 3 mm voxels
            if best_c is None or cl > best_c[0]:
                best_c = (cl, cand)
            if cl >= d / 2 + 0.3:
                break
        pts = best_c[1]
        if best_c[0] < d / 2 + 0.3:
            pts = _push_clear(fs, pts, d / 2 + 0.5)
        length = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
        cable_len = float(h.get("length_mm") or 150)
        solid = sweep_cable(pts, d)
        nm = f"REFERENCE_Cable{camel(k.ref.lower())}"
        part = Part(nm, "REFERENCE", f"cable route: {k.label or k.ref}, {h.get('cable') or 'cable'} (display-only)", CABLE_COLOR.get(pins, (0.2, 0.2, 0.22)),
                    host_ref=k.ref, role="cable", feature_id=k.feature_id, spec_path=feat.get("spec_path"))
        part.add("Cable", solid)
        part.notes.append("display-only occupied-space envelope; real cable bends are not modelled")
        M.parts.append(part)
        # slack: what the harness has beyond the route and the plug allowance, coiled flat near a tie anchor (MIN-5)
        slack = cable_len - length - PLUG_ALLOWANCE
        loop = None
        if slack > 25.0:
            near = [t for t in (ties or [])] + [pts[len(pts) // 3], pts[len(pts) // 2], a_pt]
            pl = place_slack_loop(fs, near, slack, d, taken_loops)
            if pl is None:
                warns.append(f"{k.ref}: no free space for the {slack:.0f} mm slack loop; slack not modelled")
            else:
                cc, z0, hh, ro, ri, turns = pl
                from .geom import ring_z

                lp = Part(f"REFERENCE_SlackLoop{camel(k.ref.lower())}", "REFERENCE", f"slack loop: {k.label or k.ref}, {slack:.0f} mm coiled",
                          CABLE_COLOR.get(pins, (0.2, 0.2, 0.22)), host_ref=k.ref, role="cable", feature_id=k.feature_id,
                          spec_path=feat.get("spec_path"))
                lp.add("Loop", ring_z(float(cc[0]), float(cc[1]), z0, z0 + hh, ro, ri))
                lp.notes.append(f"occupied space of {slack:.0f} mm of spare cable coiled about {turns:.1f} turns of {2 * LOOP_R:g} mm, "
                                "tied flat (display envelope; the real coil is not modelled)")
                M.parts.append(lp)
                taken_loops.append((float(cc[0]), float(cc[1]), ro, float(cc[2])))
                loop = {"component": lp.name, "slack_mm": round(slack, 1), "centre": [round(float(v), 2) for v in cc], "turns": round(turns, 2),
                        "outer_d_mm": round(2 * ro, 1), "height_mm": round(hh, 1)}
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
            "note": "display-only route; 20 mm allowance for plug entry and bends",
            "slack_mm": round(max(slack, 0.0), 1), "slack_loop": loop,
        })
    return routes, warns
