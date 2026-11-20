"""Digital checks for the CAD stage (QB-05..QB-16 + tip-over + tool access).

Every check returns {id, title, status: passed|failed|warning|not_run, detail, qb?, data?}. Nothing here claims a physical
result: the limits list says what is not modelled.
"""

from __future__ import annotations

import itertools
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .geom import bbox, to_trimesh, unit

PLA_DENSITY = 1.24e-3  # g/mm^3
BED = (250.0, 210.0, 210.0)
MIN_WALL = 1.2
TIGHT_GAP = 0.2
VOL_EPS = 0.01  # mm^3 mesh-intersection noise floor; above it an exact OCC boolean decides (REVIEW-1 BLK-1: was 0.05)

LIMITS = [
    "Purchased parts are simplified envelopes from the library's dimensions; connector internals, pins, clips and cable bends are not modelled.",
    "Cable routes are display-only occupied-space envelopes; real bend radii and pinching are not checked.",
    "Fastener threads are simplified; heat-set insert interference with its printed boss is by design.",
    "Masses of printed parts assume solid material at a typical density (PLA 1.24, PETG 1.27 g/cm3); real prints with infill are lighter; purchased masses are library values or estimates.",
    "No physical print, fit, acoustic, thermal, stability or drop test has been performed. Physical checks still needed.",
]


def _c(id_, title, status, detail, qb=None, data=None):
    d = {"id": id_, "title": title, "status": status, "detail": detail}
    if qb:
        d["qb"] = qb
    if data is not None:
        d["data"] = data
    return d


# ------------------------------------------------------------------------------------------------ allow-list
def allowed(a: str, b: str) -> Optional[str]:
    s = {a, b}
    if any(n.startswith("PURCHASE_HeatSetInserts") for n in s) and any(n.startswith("PRINT_") for n in s):
        return "heat-set insert melts into its printed boss (interference fit by design)"
    if any("Screws" in n for n in s) and any(n.startswith("PURCHASE_HeatSetInserts") for n in s):
        return "screw threads into its insert (threads simplified)"
    if any("Gasket" in n for n in s):
        return "foam gasket modelled at its compressed thickness"
    return None


# ------------------------------------------------------------------------------------------------ safe manifolds
# Mesh -> manifold3d for the collision and clearance checks. geom.to_manifold hands a non-manifold mesh (a swept cable
# route) to manifold3d piece by piece, and those pieces include degenerate triangles (two or three equal corners, left by
# to_trimesh's vertex merge; the LED cable of BENCH tomato-station had 527, as 516 one- or two-triangle pieces). The CAD
# stage of that build died there (signal 11 in geom.py:512 to_manifold, called from _manifolds), while the same saved
# shapes replayed in a fresh process did not crash, like the VTK crash cables.Distances isolates. So: degenerate faces are
# dropped first; a watertight mesh converts in this process as before; any other mesh converts in a small worker process
# (numpy, trimesh, manifold3d only) with the same fallback minus slivers under 4 triangles, so a native crash there only
# loses that one shape (reported by the collision check), never the CAD stage. GS_MANIFOLD_INPROCESS=1 = old behaviour.
_MF_WORKER = r"""
import os, pickle, sys
import numpy as np
import trimesh
import manifold3d as mf

out = os.fdopen(os.dup(1), "wb")  # the pipe to the parent; anything printed to stdout goes to stderr instead
os.dup2(2, 1)
inp = sys.stdin.buffer
CRASH = os.environ.get("GS_MANIFOLD_WORKER_TEST_CRASH") == "1"  # tests only: die like a native crash


def man(v, f):  # writable C-order copies (unpickled arrays can sit on read-only buffers)
    return mf.Manifold(mf.Mesh(vert_properties=np.array(v, dtype=np.float32, order="C"),
                               tri_verts=np.array(f, dtype=np.uint32, order="C")))


def convert(v, f):
    m0 = man(v, f)
    if m0.status() == mf.Error.NoError and not m0.is_empty():
        return m0, "direct"
    try:
        mans = []
        for p in trimesh.Trimesh(vertices=v, faces=f, process=False).split(only_watertight=False):
            if len(p.faces) < 4 or len(p.vertices) < 4:
                continue  # a sliver of 1-3 triangles encloses no volume
            mm = man(p.vertices, p.faces)
            if mm.status() == mf.Error.NoError and not mm.is_empty():
                mans.append(mm)
            else:
                mans.append(mf.Manifold.hull_points(np.array(p.vertices, dtype=np.float32, order="C")))
        if not mans:
            return None, "no piece of 4 or more triangles"
        return (mf.Manifold.batch_boolean(mans, mf.OpType.Add) if len(mans) > 1 else mans[0]), "pieces"
    except Exception:
        return mf.Manifold.hull_points(np.array(v, dtype=np.float32, order="C")), "hull"


while True:
    try:
        req = pickle.load(inp)
    except EOFError:
        break
    if CRASH:
        os.kill(os.getpid(), 11)
    try:
        m, how = convert(req[0], req[1])
        if m is None or m.is_empty():
            res = ("ok", (None, None, how))
        else:
            g = m.to_mesh()
            res = ("ok", (np.array(np.asarray(g.vert_properties)[:, :3], dtype=np.float32, order="C"),
                          np.array(g.tri_verts, dtype=np.uint32, order="C"), how))
    except Exception as e:
        res = ("err", repr(e))
    pickle.dump(res, out, protocol=pickle.HIGHEST_PROTOCOL)
    out.flush()
"""


def clean_mesh(m):
    """The trimesh without degenerate faces (two or three equal corners) and without unreferenced vertices."""
    import trimesh

    f = np.asarray(m.faces)
    if not len(f):
        return m
    keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
    if keep.all():
        return m
    m = trimesh.Trimesh(vertices=np.asarray(m.vertices), faces=f[keep], process=False)
    m.remove_unreferenced_vertices()
    return m


class Manifolder:
    """Shape -> manifold3d.Manifold (None when nothing closed is left); see the note above _MF_WORKER."""

    def __init__(self, timeout: float = 300.0):
        import os

        self.p = None
        self.timeout = timeout
        self.inprocess = os.environ.get("GS_MANIFOLD_INPROCESS") == "1"
        self.worker_calls = 0
        self.worker_deaths: list[str] = []  # one line per worker that died or timed out
        self.failed: list[str] = []  # shapes not converted because the worker died twice

    def _stop(self, kill: bool = False) -> None:
        import contextlib

        if self.p is None:
            return
        with contextlib.suppress(Exception):
            if kill:
                self.p.kill()
            self.p.stdin.close()
        try:
            self.p.wait(10)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                self.p.kill()
                self.p.wait(5)
        with contextlib.suppress(Exception):
            self.p.stdout.close()
        self.p = None

    close = _stop

    def _call(self, v, f):
        import pickle
        import select
        import subprocess
        import sys

        if self.p is None or self.p.poll() is not None:
            self._stop()
            self.p = subprocess.Popen([sys.executable, "-c", _MF_WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        self.worker_calls += 1
        pickle.dump((v, f), self.p.stdin, protocol=pickle.HIGHEST_PROTOCOL)
        self.p.stdin.flush()
        if not select.select([self.p.stdout], [], [], self.timeout)[0]:
            raise TimeoutError(f"no answer in {self.timeout:.0f} s")
        kind, res = pickle.load(self.p.stdout)
        if kind != "ok":
            raise RuntimeError(f"manifold worker: {res}")
        return res

    def convert(self, shape, tol: float = 0.08, ang: float = 0.3, name: str = ""):
        if self.inprocess:
            from .geom import to_manifold

            m = to_manifold(shape, tol, ang)
            return None if m.is_empty() else m
        return self.convert_mesh(to_trimesh(shape, tol, ang), name)

    def convert_mesh(self, m, name: str = ""):
        """The same from a trimesh (degenerate faces are dropped first)."""
        import manifold3d as mf

        m = clean_mesh(m)
        if len(m.faces) < 4:
            return None
        v = np.ascontiguousarray(m.vertices, np.float32)
        f = np.ascontiguousarray(m.faces, np.uint32)
        if m.is_watertight:
            man = mf.Manifold(mf.Mesh(vert_properties=v, tri_verts=f))
            if man.status() == mf.Error.NoError and not man.is_empty():
                return man
        for _ in range(2):
            try:
                v2, f2, _how = self._call(v, f)
            except Exception as e:  # noqa: BLE001  (worker died, timed out or answered garbage)
                code = None
                if self.p is not None:
                    try:
                        code = self.p.wait(2)
                    except Exception:  # noqa: BLE001  (still running: a timeout; killed below)
                        code = None
                self.worker_deaths.append(f"{name or 'shape'}: {type(e).__name__}"
                                          + (f", signal {-code}" if code is not None and code < 0 else ""))
                self._stop(kill=True)
                continue
            if v2 is None:
                return None
            # unpickled arrays sit on read-only buffers, which manifold3d refuses: writable C-order copies
            man = mf.Manifold(mf.Mesh(vert_properties=np.array(v2, dtype=np.float32, order="C"),
                                      tri_verts=np.array(f2, dtype=np.uint32, order="C")))
            return None if man.is_empty() else man
        self.failed.append(name or "shape")
        return None


def _manifolds(model, conv: Optional[Manifolder] = None):
    import manifold3d as mf

    own = conv is None
    conv = conv or Manifolder()
    out = {}
    try:
        for p in model.parts:
            if p.kind == "REFERENCE" and p.role != "cable":  # cable routes and slack loops are in the matrix (MIN-5)
                continue
            mans = []
            for _, s, _ in p.subparts:
                try:
                    for sol in (s.Solids() or [s]):
                        m = conv.convert(sol, 0.08, 0.3, p.name)
                        if m is not None:
                            mans.append(m)
                except Exception:
                    continue
            if mans:
                out[p.name] = mf.Manifold.batch_boolean(mans, mf.OpType.Add) if len(mans) > 1 else mans[0]
    finally:
        if own:
            conv.close()
    return out


def cable_rule(model, a: str, b: str, inter_center=None) -> Optional[str]:
    """Allowed contacts for cable routes and slack loops: its own module/kit parts, its backplane socket, its own loop,
    other flexible harnesses, and the shell only at its plug ends (within 12 mm of either end of the route)."""
    pa, pb = model.part(a), model.part(b)
    ca, cb = pa is not None and pa.role == "cable", pb is not None and pb.role == "cable"
    if not (ca or cb):
        return None
    c, o = (pa, pb) if ca else (pb, pa)
    if ca and cb:
        if c.host_ref and c.host_ref == o.host_ref:
            return "a harness and its own slack loop"
        return "flexible harnesses may touch each other"
    if o.feature_id and o.feature_id == c.feature_id:
        return "the harness plugs into its own module"
    if o.name in ("PURCHASE_Backplane",):
        return "the harness plugs into its backplane socket"
    near_end = o.name.startswith("PRINT_") or o.name in ("PURCHASE_CoreBoard", "PURCHASE_Amplifier", "PURCHASE_MicroSdModule")
    if near_end and inter_center is not None and c.name.startswith("REFERENCE_Cable"):
        r = next((x for x in model.cables if x.get("component") == c.name), None)
        if r and r.get("points"):
            ends = [np.asarray(r["points"][0]), np.asarray(r["points"][-1])]
            if min(float(np.linalg.norm(np.asarray(inter_center) - e)) for e in ends) < 12.0:
                return "plug end at its connector (within 12 mm of the route's end)"
    return None


def collisions(model, mans: dict, exact: bool = True) -> tuple[dict, list]:
    names = list(mans)
    boxes = {p.name: bbox(p.shape) for p in model.parts if p.name in mans}
    pairs = list(itertools.combinations(names, 2))
    checked, hits, allowed_l, overl = [], [], [], 0
    for a, b in pairs:
        A, B = boxes[a], boxes[b]
        if A[1] < B[0] or B[1] < A[0] or A[3] < B[2] or B[3] < A[2] or A[5] < B[4] or B[5] < A[4]:
            continue
        overl += 1
        t = time.monotonic()
        inter = mans[a] ^ mans[b]
        v = float(inter.volume())
        why = allowed(a, b)
        if why is None and v > VOL_EPS:
            ic = None
            try:
                bb_ = inter.bounding_box()
                ic = [(bb_[0] + bb_[3]) / 2, (bb_[1] + bb_[4]) / 2, (bb_[2] + bb_[5]) / 2]
            except Exception:
                pass
            why = cable_rule(model, a, b, ic)
        if v > VOL_EPS and exact and why is None:
            try:
                pa = model.part(a).shape
                pb = model.part(b).shape
                v = float(pa.intersect(pb).Volume())
            except Exception:
                pass
        rec = {"parts": [a, b], "intersection_mm3": round(v, 4), "seconds": round(time.monotonic() - t, 3)}
        if why:
            rec["allowed"] = why
            allowed_l.append(rec)
        elif v > VOL_EPS:
            hits.append(rec)
        checked.append(rec)
    summ = {"components": len(names), "pairs_total": len(pairs), "pairs_bbox_overlap": overl, "intersections": hits,
            "allowed": allowed_l, "threshold_mm3": VOL_EPS,
            "cable_components": [n for n in names if (model.part(n) is not None and model.part(n).role == "cable")]}
    return summ, checked


CARTRIDGE_SEATS = ("PURCHASE_Backplane", "PURCHASE_MicroSdModule")  # on their printed floor bosses / S1 support posts


def kit_add_manifolds(model, conv: Optional[Manifolder] = None) -> dict:
    """feature_id -> manifold of the shell material that kit adds inside the cavity (posts, pads, tubes, cup walls)."""
    import manifold3d as mf

    cav = (model.shapes or {}).get("cavity")
    if cav is None:
        return {}
    own = conv is None
    conv = conv or Manifolder()
    try:
        try:
            mc = conv.convert(cav, 0.15, 0.4, "cavity")
        except Exception:
            return {}
        if mc is None:
            return {}
        out = {}
        lift = float(getattr(model, "lift", 0.0) or 0.0)
        for k in model.kits:
            mans = []
            for a in k.adds:
                try:
                    m = conv.convert(a, 0.1, 0.35, f"kit {k.feature_id}")
                    if m is None:
                        continue
                    if lift:
                        m = m.translate((0.0, 0.0, lift))
                    mans.append(m)
                except Exception:
                    continue
            if mans:
                u = mf.Manifold.batch_boolean(mans, mf.OpType.Add) if len(mans) > 1 else mans[0]
                out[k.feature_id] = u ^ mc
        return out
    finally:
        if own:
            conv.close()


def clearances(model, mans: dict, kit_adds: Optional[dict] = None) -> list:
    """Purchased part vs printed neighbour within 3 mm. Contact is allowed only for declared seats (REVIEW-1 MAJ-7):
    a kit's module against its own retainer/clip/ring/diffuser or its own shell window (and not another kit's shell
    material), and the cartridge against its bosses. Each row says which seat it is, or why it is not one."""
    out = []
    kit_adds = kit_adds or {}
    floor_part = "PRINT_BasePlate" if model.info.get("split") == "base_plate" else "PRINT_Shell"
    prints = [p.name for p in model.parts if p.kind == "PRINT" and p.name in mans]
    buys = [p.name for p in model.parts if p.kind == "PURCHASE" and p.name in mans and not p.name.startswith(("PURCHASE_HeatSet", "PURCHASE_Feet"))
            and "Screws" not in p.name and "Gasket" not in p.name]
    for b in buys:
        pb = model.part(b)
        for a in prints:
            pa = model.part(a)
            A, B = bbox(pa.shape), bbox(pb.shape)
            if A[1] + 3 < B[0] or B[1] + 3 < A[0] or A[3] + 3 < B[2] or B[3] + 3 < A[2] or A[5] + 3 < B[4] or B[5] + 3 < A[4]:
                continue
            try:
                g = float(mans[b].min_gap(mans[a], 3.0))
            except Exception:
                continue
            if g >= 3.0:
                continue
            row = {"parts": [b, a], "gap_mm": round(g, 3), "contact": g < 0.02}
            if g < TIGHT_GAP:
                seat = None
                if pb.feature_id and pa.feature_id == pb.feature_id:
                    seat = "own retainer"
                elif pb.feature_id and a == "PRINT_Shell":
                    near = []
                    for fid, m in kit_adds.items():
                        if fid == pb.feature_id:
                            continue
                        try:
                            gg = float(mans[b].min_gap(m, TIGHT_GAP + 0.5))
                        except Exception:
                            continue
                        if gg < TIGHT_GAP:
                            near.append({"kit": fid, "gap_mm": round(gg, 3)})
                    if near:
                        row["touches_other_kit"] = near
                    else:
                        seat = "own shell window / pocket / pad"
                elif b in CARTRIDGE_SEATS and a == floor_part:
                    seat = "cartridge on its printed bosses / support posts"
                if seat:
                    row["seat"] = seat
                else:
                    row["not_a_seat"] = True
            out.append(row)
    return out


def wall_thickness(mesh, n: int = 1500, seed: int = 1) -> dict:
    import trimesh

    pts, fi = trimesh.sample.sample_surface(mesh, n, seed=seed)
    nrm = mesh.face_normals[fi]
    o = pts - nrm * 0.02
    d = -nrm
    ray = trimesh.ray.ray_triangle.RayMeshIntersector(mesh)
    locs, ri, _ = ray.intersects_location(o, d, multiple_hits=False)
    th = np.full(len(o), np.nan)
    if len(locs):
        th[ri] = np.linalg.norm(locs - o[ri], axis=1)
    ok = np.isfinite(th)
    P, th = pts[ok], th[ok]
    if not len(th):
        return {}
    thin = np.where(th < MIN_WALL)[0]
    thin = thin[np.argsort(th[thin])][:300]
    return {"samples": int(len(th)), "min": round(float(th.min()), 2), "p1": round(float(np.percentile(th, 1)), 2),
            "p5": round(float(np.percentile(th, 5)), 2), "median": round(float(np.median(th)), 2),
            "below_min_wall": int((th < MIN_WALL).sum()), "min_wall_rule": MIN_WALL,
            "thin_points": [[round(float(v), 2) for v in P[i]] + [round(float(th[i]), 3)] for i in thin]}


def tip_over(model) -> dict:
    tot, mom = 0.0, np.zeros(3)
    rows = []
    for p in model.parts:
        if p.kind == "REFERENCE":
            continue
        s = p.shape
        try:
            c = np.array(s.Center().toTuple())
            vol = float(s.Volume())
        except Exception:
            continue
        if p.kind == "PRINT":
            pm = (getattr(model, "params", None) or {}).get("print_material") or {}
            dens = float(pm.get("density_g_cm3", 1.24)) * 1e-3
            m = vol * dens
            src = f"computed (solid {pm.get('name', 'PLA')}, {dens * 1e3:g} g/cm3 estimate)"
        else:
            m = float(p.mass_g or 0.0)
            src = p.mass_source
        tot += m
        mom += m * c
        rows.append({"name": p.name, "mass_g": round(m, 2), "source": src})
    if tot <= 0:
        return {}
    cog = mom / tot
    feet = model.info.get("feet") or []
    pts = np.array(feet, float) if len(feet) >= 3 else None
    if pts is None:
        b = bbox(model.part("PRINT_Shell").shape)
        pts = np.array([[b[0], b[2]], [b[1], b[2]], [b[1], b[3]], [b[0], b[3]]])
    from scipy.spatial import ConvexHull

    hull = pts[ConvexHull(pts).vertices]
    h = cog[2]
    angles = []
    inside = True

    def cross2(a, b):
        return float(a[0] * b[1] - a[1] * b[0])

    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        e = b - a
        L = max(float(np.linalg.norm(e)), 1e-9)
        cr = cross2(e, cog[:2] - a)
        if cr < 0:
            inside = False
        d = abs(cr) / L
        angles.append(math.degrees(math.atan2(d, h)))
    return {"mass_g": round(tot, 1), "cog_mm": [round(float(v), 2) for v in cog], "support": "feet" if len(feet) >= 3 else "base outline",
            "min_tip_angle_deg": round(min(angles), 1) if inside else 0.0, "cog_inside_support": inside, "masses": rows}


OVERHANG_DEG = 45.0  # facets flatter than this from horizontal, facing down, need support
CEILING_LIMIT = 500.0  # mm^2 of unsupported down-facing area allowed per printed part (REVIEW-1 MAJ-9)
NARROW_SPAN = 6.0  # regions whose mean width is <= this print as short bridges/overhangs (hole tops, post undersides, ledges)


def unsupported_ceiling(part, tol: float = 0.25) -> dict:
    """Down-facing area that sits above another surface of the same part in its print orientation (so a
    build-plate-only support cannot reach it). Regions narrower than NARROW_SPAN are reported apart as bridges."""
    import trimesh

    from .export import print_orient
    from .geom import to_trimesh

    s, _ = print_orient(part)
    mesh = to_trimesh(s, tol, 0.35)
    n, c, A = mesh.face_normals, mesh.triangles_center, mesh.area_faces
    zmin = float(mesh.bounds[0][2])
    down = (n[:, 2] < -math.cos(math.radians(90.0 - OVERHANG_DEG))) & (c[:, 2] > zmin + 0.3)
    idx = np.where(down)[0]
    out = {"down_facing_mm2": round(float(A[idx].sum()), 1), "unsupported_mm2": 0.0, "bridge_mm2": 0.0, "counted_mm2": 0.0, "regions": []}
    if not len(idx):
        return out
    ray = trimesh.ray.ray_triangle.RayMeshIntersector(mesh)
    o = c[idx] - np.array([0.0, 0.0, 0.05])
    hit = ray.intersects_any(o, np.tile([0.0, 0.0, -1.0], (len(o), 1)))
    uns = idx[hit]
    if not len(uns):
        return out
    inset = set(uns.tolist())
    adj = mesh.face_adjacency
    keep = np.array([a in inset and b in inset for a, b in adj], bool) if len(adj) else np.zeros(0, bool)
    comps = trimesh.graph.connected_components(adj[keep] if len(adj) else np.zeros((0, 2), int), nodes=uns, min_len=1)
    regions = []
    for cc in comps:
        cc = np.asarray(cc)
        pts = mesh.triangles[cc].reshape(-1, 3)
        lo, hi = pts.min(0), pts.max(0)
        area = float(A[cc].sum())
        # mean width = 2 x area / boundary length: a strip w wide reads w, a ring ledge reads its radial width
        fe = np.sort(mesh.faces[cc][:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        uq, ct = np.unique(fe, axis=0, return_counts=True)
        be = uq[ct == 1]
        perim = float(np.linalg.norm(mesh.vertices[be[:, 0]][:, :2] - mesh.vertices[be[:, 1]][:, :2], axis=1).sum()) if len(be) else 0.0
        width = 2.0 * area / perim if perim > 1e-6 else float(min(hi[0] - lo[0], hi[1] - lo[1]))
        regions.append({"area_mm2": round(area, 1), "narrow": width <= NARROW_SPAN, "mean_width_mm": round(width, 2),
                        "bbox_print": [round(float(v), 1) for v in (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])]})
    regions.sort(key=lambda r: -r["area_mm2"])
    out["unsupported_mm2"] = round(sum(r["area_mm2"] for r in regions), 1)
    out["bridge_mm2"] = round(sum(r["area_mm2"] for r in regions if r["narrow"]), 1)
    out["counted_mm2"] = round(sum(r["area_mm2"] for r in regions if not r["narrow"]), 1)
    out["regions"] = regions[:12]
    return out


def _corner_radius(poly, corner: str, z0: float, zone: float = 7.0) -> Optional[float]:
    """Tightest bend radius of a section outline near one bottom corner ('left'/'right' = min/max of the first
    coordinate), from circumradii of points 1.5 mm apart along the resampled boundary."""
    from shapely.geometry import LineString

    ring = LineString(np.asarray(poly.exterior.coords))
    L = ring.length
    pts = np.array([ring.interpolate(t).coords[0] for t in np.arange(0, L, 0.5)])
    if len(pts) < 10:
        return None
    sel = pts[:, 1] < z0 + zone
    if corner == "left":
        sel &= pts[:, 0] < pts[sel, 0].min() + zone if sel.any() else sel
    else:
        sel &= pts[:, 0] > pts[sel, 0].max() - zone if sel.any() else sel
    ids = np.where(sel)[0]
    best = None
    for i in ids:
        a, b, c = pts[(i - 3) % len(pts)], pts[i], pts[(i + 3) % len(pts)]
        ab, bc, ca = np.linalg.norm(b - a), np.linalg.norm(c - b), np.linalg.norm(a - c)
        cr = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2.0
        if cr < 1e-6:
            continue
        r = ab * bc * ca / (4 * cr)
        best = r if best is None else min(best, r)
    return best


def bottom_edge_round(model) -> dict:
    """Bottom perimeter radius of the shell's outer surface (the body before the cover / plate split, so the seam cut and
    the foot seats do not count as corners), from its front-view (y = 0) and side-view (x = 0) sections."""
    from .probe import Probe

    body = (model.shapes or {}).get("outer")
    if body is None:
        return {}
    pr = Probe(body, tol=0.1)
    z0 = float(body.BoundingBox().zmin)
    out = {}
    from .probe import largest

    for axis, names in (("y", ("left side", "right side")), ("x", ("front", "rear"))):
        sec = largest(pr.section(axis, 0.0))
        if sec.is_empty:
            continue
        from shapely.geometry import Polygon

        outer = Polygon(sec.exterior.coords)
        for nm, cn in zip(names, ("left", "right")):
            r = _corner_radius(outer, cn, z0)
            out[nm] = None if r is None else round(float(r), 2)
    return out


def fastener_engagement(model) -> list:
    out = []
    for f in model.fasteners:
        if f.kind != "screw" or f.joint is None:
            continue
        a = unit(f.axis)
        tip = f.head + a * f.length
        pen = float(np.dot(tip - f.joint, a))
        eng = min(pen, f.insert_len or 0.0)
        bottom = pen > (f.hole_depth or 0) - 0.2
        ok = eng >= 3.0 and not bottom
        out.append({"screw": f.label, "group": f.group, "length_mm": f.length, "clamped_mm": round(f.clamp_t or 0, 2),
                    "penetration_mm": round(pen, 2), "thread_engagement_mm": round(eng, 2), "hole_depth_mm": round(f.hole_depth or 0, 2),
                    "bottoms_out": bool(bottom), "ok": bool(ok)})
    return out


def tool_access(model, mans: dict) -> list:
    import manifold3d as mf

    out = []
    soft = {p.name for p in model.parts if p.role == "cable"}  # flexible harnesses can be pushed aside by the driver
    allm = [m for n, m in mans.items() if not n.startswith("PURCHASE_HeatSet") and "Screws" not in n and n not in soft]
    if not allm:
        return out
    world = mf.Manifold.batch_boolean(allm, mf.OpType.Add)
    # base-plate designs: the cartridge is screwed to the plate before the shell goes on
    noshell = [m for n, m in mans.items() if not n.startswith("PURCHASE_HeatSet") and "Screws" not in n and n != "PRINT_Shell" and n not in soft]
    world_open = mf.Manifold.batch_boolean(noshell, mf.OpType.Add) if noshell else world
    for f in model.fasteners:
        if f.kind != "screw" or f.group not in ("CartridgeScrews", "CoverScrews", "PlateScrews"):
            continue
        a = unit(f.axis)
        start = f.head - a * (f.length * 0 + 2.6)
        cyl = mf.Manifold.cylinder(40.0, 3.0, 3.0, 24)
        z = np.array([0, 0, 1.0])
        v = -a
        ax = np.cross(z, v)
        s = np.linalg.norm(ax)
        if s < 1e-9:
            R = np.eye(3) if v[2] > 0 else np.diag([1, -1, -1])
        else:
            ax /= s
            ang = math.atan2(s, float(np.dot(z, v)))
            K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
            R = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
        M = np.hstack([R, start.reshape(3, 1)])
        tool = cyl.transform(M.tolist())
        w = world_open if (f.group in ("CartridgeScrews", "PlateScrews") and model.info.get("split") == "base_plate") else world
        vol = float((tool ^ w).volume())
        out.append({"screw": f.label, "group": f.group, "blocked_mm3": round(vol, 2), "ok": vol < 1.0,
                    "tool": "6 mm driver shaft, 40 mm free length along the screw axis"})
    return out


def decor_inside_silhouette(model) -> Optional[dict]:
    """Shell section at y = 0 minus the bare-body silhouette = decor outside the body; it must lie inside the union of
    the ears' visible footprints (no stem or neck outside the body)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from .probe import Probe, largest

    sil = model.info.get("silhouette_xz")
    ears = model.info.get("ears") or []
    if not sil:
        return None
    lift = float(model.lift or 0.0)
    S = Polygon(sil).buffer(0)
    sh = model.part("PRINT_Shell")
    sec = largest(Probe(sh.shape, tol=0.15).section("y", 0.0))
    if sec.is_empty:
        return None
    from shapely import affinity

    sec = affinity.translate(Polygon(sec.exterior.coords), 0.0, -lift)
    outside = sec.difference(S.buffer(0.6))  # both outlines come from tessellations (0.15 and 0.25 mm chord error)
    # the ear's own footprint plus its root blend (a fillet in the corner between ear and head is not a stem)
    allowed = unary_union([Polygon(e["visible_xz"]).buffer(0.6 + float(e.get("blend_r") or 0.0)) for e in ears if e.get("visible_xz")]) \
        if ears else Polygon()
    left = outside.difference(allowed) if not allowed.is_empty else outside
    allp = [g for g in getattr(left, "geoms", [left]) if g.area > 0.05]
    # a stem or neck is a real piece; slivers under 0.8 mm mean width along the outline are the two tessellations differing
    pieces = [g for g in allp if 2.0 * g.area / max(g.length, 1e-9) >= 0.8]
    slivers = [g for g in allp if g not in pieces]
    return {"ears": len(ears), "outside_silhouette_mm2": round(float(outside.area), 2), "outside_footprint_mm2": round(float(sum(g.area for g in pieces)), 2),
            "slivers_mm2": round(float(sum(g.area for g in slivers)), 2),
            "max_outboard_excess_mm": round(max([float(e.get("outboard_excess_mm", 0.0)) for e in ears] or [0.0]), 2),
            "side_top_z": model.info.get("side_top_z"),
            "rule": "outline_x(z) = the silhouette half-width at height min(z, side_top_z); ears stay 2 mm inside it",
            "pieces": [[round(v, 1) for v in g.bounds] for g in pieces[:8]]}


def decor_on_flat_face(model) -> Optional[dict]:
    regs = [r for r in (model.info.get("decor_regions") or []) if r.get("type") != "ear"]
    if not regs:
        return None
    flat = model.info.get("family") == "silhouette"
    rows = []
    for r in regs:
        b = r["bbox"]
        dy = b[3] - b[2]
        dep = float(r.get("depth") or 0.0)
        ok = (dy <= dep + 0.1) if flat else True
        rows.append({"spec_paths": r.get("spec_paths"), "type": r.get("type"), "depth_mm": dep, "y_extent_mm": round(dy, 3), "ok": ok})
    adj = model.info.get("decor_adjustments") or []
    det = (f"{len(rows)} relief regions; max Y extent over depth {max(r['y_extent_mm'] - r['depth_mm'] for r in rows):+.2f} mm"
           if flat else f"{len(rows)} relief regions on a curved front (no flat face): each follows the surface at constant depth")
    if adj:
        det += "; adjusted to fit: " + "; ".join(f"{a.get('spec_path')} {a.get('action')}" + (f" {a['requested_mm']} -> {a['built_mm']} mm" if a.get("built_mm") else "")
                                                for a in adj)
    return {"regions": rows, "adjustments": adj, "detail": det, "flat_face": flat}


def coupon_coverage(model) -> tuple[set, set, list]:
    """(required interface ids, covered ids, sorted missing ids): every tight interface a kit exports (REVIEW-1 MIN-11)
    plus the enclosure ones, against what the TEST_ coupons cover."""
    covered = set()
    for c in model.coupons.values():
        covered |= set((c.get("features") or {}).keys())
        covered |= set(c.get("covers") or [])
    need = {"insert_holes", "tie_slot", "usb_c_opening"}
    if any(k.kind in ("snap_button", "tactile") for k in model.kits):
        need.add("button_hole_d")
    if model.info.get("split") == "rear_cover":
        need.add("lip_gap_mm")
    need |= {i["id"] for k in model.kits for i in (k.interfaces or [])}
    return need, covered, sorted(need - covered)


def run_checks(model, files: dict, step_path: Optional[Path] = None, exact: bool = True) -> dict:
    import trimesh

    t0 = time.time()
    checks = []
    comps = files.get("components", {})
    # QB-07/validity -----------------------------------------------------------
    bad = [n for n, c in comps.items() if not c["valid"]]
    multi = [n for n, c in comps.items() if c["kind"] == "PRINT" and c["solids"] != 1]
    checks.append(_c("occ_validity", "Every component is a valid OpenCascade shape", "failed" if bad else "passed",
                     f"{len(comps)} components, {len(bad)} invalid" + (f": {bad}" if bad else ""), "QB-07"))
    frags = model.info.get("fragments") or {}
    big = {n: [f for f in fl if (f.get("volume_mm3") or 0.0) > 1.0] for n, fl in frags.items()}
    big = {n: fl for n, fl in big.items() if fl}
    fr_txt = "; ".join(f"{n}: {len(fl)} dropped, largest {max((f.get('volume_mm3') or 0) for f in fl):.3f} mm3"
                       for n, fl in frags.items() if fl)
    checks.append(_c("print_single_solid", "Each printed part is exactly one solid; no dropped split fragment over 1 mm3",
                     "failed" if (multi or big) else "passed",
                     ("all printed parts are one solid" if not multi else f"multi-solid: {multi}")
                     + (f"; fragments: {fr_txt}" if fr_txt else "; no fragments dropped")
                     + (f"; over 1 mm3: {sorted(big)}" if big else ""), "QB-07",
                     {"fragments_dropped": model.info.get("fragments_dropped"), "fragments": frags}))
    # QB-06 prefixes ------------------------------------------------------------
    unlinked = [n for n, c in comps.items() if c["kind"] == "PURCHASE" and not (c.get("module_id") or c.get("ref"))]
    badpref = [n for n in comps if not n.startswith(("PRINT_", "PURCHASE_", "REFERENCE_"))]
    checks.append(_c("role_prefix", "Role prefixes and purchase links", "failed" if (unlinked or badpref) else "passed",
                     f"{sum(1 for c in comps.values() if c['kind'] == 'PURCHASE')} purchased components all carry module_id or ref" if not unlinked
                     else f"no module_id/ref: {unlinked}", "QB-06"))
    # meshes QB-07/10/16 --------------------------------------------------------
    meshes = {}
    for name, pinfo in files.get("print", {}).items():
        f = Path(files.get("print_dir", "")) / Path(pinfo["file"]).name if files.get("print_dir") else None
        if f is None or not f.exists():
            continue
        m = trimesh.load_mesh(f)
        ext = m.bounds[1] - m.bounds[0]
        dims = sorted(ext.tolist(), reverse=True)
        fits = ext[0] <= BED[0] and ext[1] <= BED[1] and ext[2] <= BED[2]
        fits_any = dims[0] <= BED[0] and dims[1] <= BED[1] and dims[2] <= BED[2]
        bv = pinfo.get("brep_volume_mm3") or 0
        vdiff = abs(float(m.volume) - bv) / max(bv, 1e-9) * 100 if bv else None
        meshes[name] = {"watertight": bool(m.is_watertight), "components": int(len(m.split(only_watertight=False))),
                        "volume_mm3": round(float(m.volume), 1), "brep_volume_mm3": round(bv, 1),
                        "volume_diff_pct": None if vdiff is None else round(vdiff, 3), "bounds_mm": [round(float(x), 2) for x in ext],
                        "on_bed": bool(abs(m.bounds[0][2]) < 1e-3), "fits_bed": bool(fits), "fits_bed_rotated": bool(fits_any),
                        "orientation_note": pinfo.get("orientation_note")}
    nw = [n for n, v in meshes.items() if not v["watertight"] or v["components"] != 1]
    checks.append(_c("print_watertight", "Print STLs are watertight with one connected component", "failed" if nw else "passed",
                     f"{len(meshes)} STL files; " + ("all watertight, 1 component each" if not nw else f"problems: {nw}"), "QB-07",
                     {"meshes": meshes}))
    vbad = [n for n, v in meshes.items() if v["volume_diff_pct"] is not None and v["volume_diff_pct"] > 1.0]
    checks.append(_c("brep_vs_stl_volume", "B-rep volume and STL volume agree within 1 %", "failed" if vbad else "passed",
                     "max difference " + f"{max([v['volume_diff_pct'] or 0 for v in meshes.values()] or [0]):.3f} %"
                     + (f"; over 1 %: {vbad}" if vbad else ""), "QB-16"))
    nb = [n for n, v in meshes.items() if not v["fits_bed"] or not v["on_bed"]]
    checks.append(_c("bed_fit", "Each print lies on z=0 in its stated orientation and fits a 250 x 210 x 210 mm bed",
                     "failed" if nb else "passed", "all fit" if not nb else f"do not fit or not on the bed: {nb}", "QB-10",
                     {n: {"bounds_mm": v["bounds_mm"], "orientation_note": v["orientation_note"]} for n, v in meshes.items()}))
    # zmin QB-05 ----------------------------------------------------------------
    allb = np.array([c["bbox"] for c in comps.values() if c["kind"] != "REFERENCE"])
    zmin = float(allb[:, 4].min())
    size = [float(allb[:, 1].max() - allb[:, 0].min()), float(allb[:, 3].max() - allb[:, 2].min()), float(allb[:, 5].max() - zmin)]
    checks.append(_c("frame_zmin", "mm, +Z up, front -Y, assembly rests on z = 0", "passed" if abs(zmin) < 0.01 else "failed",
                     f"zmin = {zmin:.3f} mm; size {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm", "QB-05",
                     {"assembly_size_mm": [round(v, 2) for v in size]}))
    if step_path and Path(step_path).exists():
        try:
            import cadquery as cq

            st = cq.importers.importStep(str(step_path)).val()
            checks.append(_c("step_valid", "STEP re-imports as a valid shape", "passed" if st.isValid() else "failed",
                             f"{Path(step_path).stat().st_size / 1e6:.1f} MB, valid={st.isValid()}", "QB-04"))
        except Exception as e:
            checks.append(_c("step_valid", "STEP re-imports as a valid shape", "failed", str(e), "QB-04"))
    # collisions QB-08/09 -------------------------------------------------------
    conv = Manifolder()
    try:
        mans = _manifolds(model, conv)
        unmeshed = sorted(set(conv.failed))
        kadds = kit_add_manifolds(model, conv)
        kit_unmeshed = sorted(set(conv.failed) - set(unmeshed))
    finally:
        conv.close()
    cable_names = {p.name for p in model.parts if p.role == "cable"}
    rigid_unmeshed = [n for n in unmeshed if n not in cable_names]
    summ, pairs = collisions(model, mans, exact=exact)
    summ["mesh_worker"] = {"calls": conv.worker_calls, "deaths": conv.worker_deaths, "not_checked": unmeshed}
    checks.append(_c("collisions", "Full pair matrix of rigid components: no unintended intersection",
                     "failed" if (summ["intersections"] or rigid_unmeshed) else "passed",
                     f"{summ['components'] - len(summ['cable_components'])} rigid components + {len(summ['cable_components'])} cable routes "
                     f"and slack loops, {summ['pairs_total']} pairs, {summ['pairs_bbox_overlap']} with overlapping boxes "
                     f"checked by boolean, {len(summ['intersections'])} unintended intersections, {len(summ['allowed'])} allowed by rule"
                     + (f"; NOT CHECKED (the mesh worker died twice): {rigid_unmeshed}" if rigid_unmeshed else "")
                     + (f"; cable routes not checked (the mesh worker died twice): {[n for n in unmeshed if n in cable_names]}"
                        if any(n in cable_names for n in unmeshed) else ""),
                     "QB-08", {**summ, "pairs": pairs}))
    cl = clearances(model, mans, kadds)
    bad_cl = [c for c in cl if c.get("not_a_seat")]
    checks.append(_c("clearances", f"Clearance from each purchased part to its printed neighbours: under {TIGHT_GAP} mm only at declared seats",
                     "failed" if bad_cl else ("warning" if kit_unmeshed else "passed"),
                     f"{len(cl)} purchased/printed pairs within 3 mm; {sum(1 for c in cl if c.get('seat'))} declared seats; "
                     f"{len(bad_cl)} undeclared contacts or gaps under {TIGHT_GAP} mm"
                     + (": " + ", ".join("/".join(c["parts"]) + f" ({c['gap_mm']} mm)" for c in bad_cl) if bad_cl else "")
                     + (f"; kit shell material not checked (the mesh worker died twice): {kit_unmeshed}" if kit_unmeshed else ""),
                     "QB-09",
                     {"pairs": cl, "rule": "contact only for a kit's module against its own retainer, gasket or shell window, "
                                           "and the cartridge against its bosses"}))
    # cartridge + feature module clearance --------------------------------------
    cables = {p.name for p in model.parts if p.role == "cable"}  # judged in the collision matrix (cable_rule)
    cart_names = [n for n in ("PURCHASE_Backplane", "PURCHASE_CoreBoard", "PURCHASE_Amplifier", "PURCHASE_MicroSdModule") if n in mans]
    others = [n for n in mans if n not in cart_names and n not in cables and not n.startswith(("PURCHASE_HeatSet", "PURCHASE_Cartridge", "PURCHASE_Feet"))]
    rows = []
    for a in cart_names:
        for b in others:
            try:
                g = float(mans[a].min_gap(mans[b], 10.0))
            except Exception:
                continue
            if g < 10.0:
                rows.append({"parts": [a, b], "gap_mm": round(g, 2)})
    shell_like = {"PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate"}
    floor_part = "PRINT_BasePlate" if model.info.get("split") == "base_plate" else "PRINT_Shell"
    seated = {("PURCHASE_Backplane", floor_part), ("PURCHASE_MicroSdModule", floor_part)}  # bosses / S1 support posts
    bad_c = [r for r in rows if r["gap_mm"] < 0.02 and tuple(r["parts"]) not in seated and "Screws" not in r["parts"][1]]
    for r in rows:
        if tuple(r["parts"]) in seated:
            r["note"] = "seated on its printed bosses / support posts"
    kb = model.info.get("cartridge", {}).get("keepout_box")
    checks.append(_c("cartridge_clearance", "Core cartridge clears the shell and the feature modules", "failed" if bad_c else "passed",
                     f"{len(rows)} neighbours within 10 mm; min gap to a non-mount neighbour "
                     f"{min([r['gap_mm'] for r in rows if r['parts'][1] not in shell_like] or [99]):.1f} mm",
                     None, {"pairs": rows, "keepout_box": kb}))
    frows = []
    feat_parts = [p.name for p in model.parts if p.kind == "PURCHASE" and p.feature_id and "Gasket" not in p.name]
    for a in feat_parts:
        for b in mans:
            if b == a or b in cables or b.startswith(("PURCHASE_HeatSet",)) or "Screws" in b or "Gasket" in b:
                continue
            pa, pb = model.part(a), model.part(b)
            if pb.feature_id == pa.feature_id and pb.kind == "PRINT" and pb.name != "PRINT_Shell":
                continue
            try:
                g = float(mans[a].min_gap(mans[b], 5.0))
            except Exception:
                continue
            if g < 5.0 and b not in shell_like:
                frows.append({"parts": [a, b], "gap_mm": round(g, 2)})
    fbad = [r for r in frows if r["gap_mm"] < 0.5]
    checks.append(_c("feature_module_clearance", "Feature modules clear each other and the cartridge (>= 0.5 mm)",
                     "failed" if fbad else "passed", f"{len(frows)} module neighbour pairs within 5 mm; {len(fbad)} under 0.5 mm", None,
                     {"pairs": frows}))
    # walls ---------------------------------------------------------------------
    walls = {}
    for p in model.parts:
        if p.kind == "PRINT" and p.name in ("PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate"):
            try:
                walls[p.name] = wall_thickness(to_trimesh(p.shape, 0.1, 0.3), 1200)
            except Exception as e:
                walls[p.name] = {"error": str(e)}
    thin = {n: w for n, w in walls.items() if w.get("p1", 9) < MIN_WALL}
    checks.append(_c("min_wall", f"Wall thickness sampling (rule: >= {MIN_WALL} mm at the 1st percentile)",
                     "failed" if thin else "passed",
                     "; ".join(f"{n}: min {w.get('min')} / p1 {w.get('p1')} / median {w.get('median')} mm, "
                               f"{w.get('below_min_wall', 0)} of {w.get('samples', 0)} samples under {MIN_WALL} mm" for n, w in walls.items())
                     + ("; thin samples (x, y, z, mm) are in data.<part>.thin_points" + ("; overlay: cad/renders/thin-walls.png" if thin else "")
                        if any(w.get("below_min_wall") for w in walls.values()) else ""),
                     "QB-11", walls))
    # fasteners QB-14 -----------------------------------------------------------
    fe = fastener_engagement(model)
    fbad2 = [f for f in fe if not f["ok"]]
    checks.append(_c("fastener_engagement", "Every screw engages >= 3 mm of its insert and does not bottom out",
                     "failed" if fbad2 else "passed", f"{len(fe)} screws; {len(fbad2)} problems", "QB-14", {"screws": fe}))
    ta = tool_access(model, mans)
    tbad = [t for t in ta if not t["ok"]]
    checks.append(_c("tool_access", "A 6 mm driver reaches every cartridge, cover and plate screw along its axis",
                     "warning" if tbad else "passed", f"{len(ta)} screws; {len(tbad)} blocked", None, {"screws": ta}))
    # cables QB-15 --------------------------------------------------------------
    cr = model.cables
    cbad = [c for c in cr if not c["length_ok"]]
    cwall = [c for c in cr if c.get("min_clearance_mm") is not None and c["min_clearance_mm"] < -0.01]
    feats_with_cable = [k for k in model.kits if k.cable_end is not None]
    checks.append(_c("cable_routes", "Cable routes named after their cable, length within the harness, clear of walls",
                     "failed" if (cbad or cwall) else ("warning" if len(cr) < len(feats_with_cable) else "passed"),
                     f"{len(cr)} of {len(feats_with_cable)} cables routed; {len(cbad)} too long; {len(cwall)} touch a wall",
                     "QB-15", {"routes": [{k: v for k, v in c.items() if k != "points"} for c in cr]}))
    # coupons QB-13 -------------------------------------------------------------
    printed = model.info.get("kind") == "printed"  # catalog package A: no inserts, ties, USB-C or kits on a printed-only object
    cps = {n: c.get("features") for n, c in model.coupons.items()}
    need, covered, miss = coupon_coverage(model)
    exported = {i["id"]: i.get("name") for k in model.kits for i in (k.interfaces or [])}
    if printed:
        checks.append(_c("fit_coupon", "Fit coupons cover every tight interface the kits export", "not_run",
                         "not applicable: a printed-only object has no inserts, cable ties, USB-C opening or module kits", "QB-13"))
    else:
        checks.append(_c("fit_coupon", "Fit coupons cover every tight interface the kits export", "failed" if miss else "passed",
                     f"{len(cps)} coupons; {len(exported)} kit interfaces exported; covers {len(need & covered)} of {len(need)}"
                     + (f"; missing {miss}" if miss else ""), "QB-13",
                     {"coupons": {n: {"features": c.get("features"), "covers": c.get("covers"), "tests": c.get("tests")} for n, c in model.coupons.items()},
                      "interfaces": [{k_: v_ for k_, v_ in i.items() if k_ != "region"} for k in model.kits for i in (k.interfaces or [])],
                      "required": sorted(need)}))
    # print overhangs (MAJ-9) ---------------------------------------------------
    ceil = {}
    for p in model.parts:
        if p.kind != "PRINT":
            continue
        try:
            ceil[p.name] = unsupported_ceiling(p)
        except Exception as e:  # pragma: no cover
            ceil[p.name] = {"error": str(e)[:200]}
    over = {n: v for n, v in ceil.items() if v.get("counted_mm2", 0.0) > CEILING_LIMIT}
    sh_c = ceil.get("PRINT_Shell", {})
    roof = (model.info.get("body") or {}).get("roof") or {}
    checks.append(_c("unsupported_ceiling", f"No printed part has more than {CEILING_LIMIT:g} mm2 of unsupported down-facing area "
                     f"(steeper than {OVERHANG_DEG:g} deg) above its own surfaces", "failed" if over else "passed",
                     f"PRINT_Shell: {sh_c.get('counted_mm2', '?')} mm2 counted, {sh_c.get('bridge_mm2', '?')} mm2 in short bridges and ledges "
                     f"(mean width <= {NARROW_SPAN:g} mm), {sh_c.get('down_facing_mm2', '?')} mm2 down-facing in total"
                     + (f"; cavity roof self-supporting at {roof.get('angle_deg')} deg" if roof.get("self_supporting") else "")
                     + (f"; over the limit: {sorted(over)}" if over else ""), "QB-10",
                     {"parts": ceil, "limit_mm2": CEILING_LIMIT, "overhang_deg": OVERHANG_DEG, "narrow_span_mm": NARROW_SPAN,
                      "method": "facets of the print-oriented mesh facing down more than 45 deg whose downward ray hits the same part "
                                "(build-plate-only supports cannot reach them); connected regions with a mean width (2 x area / perimeter) "
                                "of 6 mm or less are short bridges or ledges"}))
    # decor stays on the body (MAJ-30) and on the flat face (MAJ-31) -------------
    di = decor_inside_silhouette(model)
    if di is not None:
        bad = di["outside_footprint_mm2"] > 1.0 or di["max_outboard_excess_mm"] > 0.05
        checks.append(_c("decor_inside_silhouette", "Decor outside the body silhouette is only the visible ear itself (no stems), "
                         "and every ear stays inside outline_x(z) - 2 mm", "failed" if bad else "passed",
                         f"{di['ears']} ears; {di['outside_footprint_mm2']:.2f} mm2 of decor outside the silhouette and outside the ear footprints; "
                         f"max outboard excess {di['max_outboard_excess_mm']:.2f} mm", None, di))
    df = decor_on_flat_face(model)
    if df is not None:
        badf = [r for r in df["regions"] if not r["ok"]]
        checks.append(_c("decor_on_flat_face", "Every relief sits on the flat front face (depth along Y <= relief depth + 0.1 mm)",
                         "failed" if badf else "passed",
                         df["detail"] + (f"; off the flat face: {[r['spec_paths'] for r in badf]}" if badf else ""), None, df))
    br = bottom_edge_round(model) if model.info.get("family") == "silhouette" else {}
    if br:
        vals = [v for v in br.values() if v is not None]
        low = [k for k, v in br.items() if v is None or v < 3.0 - 0.05]  # 0.05 mm: below the 0.1 mm section probe resolution
        radii = ", ".join(f"{k} {v} mm" for k, v in br.items())
        data = {"radii_mm": br, "method": "tightest bend of the y = 0 and x = 0 section outlines within 7 mm of each bottom corner",
                "built": (model.info.get("body") or {}).get("bottom_round")}
        if printed:  # a gadget shell's look (REVIEW-1 MIN-39); a printed object prints flat on its base, where a sharp edge holds best
            checks.append(_c("bottom_edge_round", "Shell bottom perimeter is rounded (>= 3 mm) on every side", "not_run",
                             f"not applicable: a printed-only object prints flat on its base (radii {radii}); check its edges by hand",
                             None, data))
        else:
            checks.append(_c("bottom_edge_round", "Shell bottom perimeter is rounded (>= 3 mm) on every side", "failed" if low else "passed",
                             radii + (f"; under 3 mm: {low}" if low else ""), None, data))
    usb = model.info.get("usb_c") or {}
    lp = usb.get("load_path")
    if printed:
        checks.append(_c("power_entry", "External USB-C cord: a defined load path takes a pull before the receptacle or board",
                         "not_run", "not applicable: a printed-only object has no power input", "QB-21"))
    else:
        checks.append(_c("power_entry", "External USB-C cord: a defined load path takes a pull before the receptacle or board",
                     "passed" if lp else "failed",
                     (f"load path: {lp.get('feature')} on {lp.get('part')}, {lp.get('distance_from_opening_mm')} mm from the opening; "
                      "physical pull check still needed") if lp else "no cord tie point or plug capture modelled", "QB-21",
                     {"usb_c": usb}))
    # labels (MIN-40) and refs (MIN-7) ------------------------------------------
    labs: dict = {}
    for n, c in comps.items():
        labs.setdefault(c.get("label"), []).append(n)
    dup = {k: v for k, v in labs.items() if len(v) > 1}
    refs: dict = {}
    for n, c in comps.items():
        if c.get("ref"):
            refs.setdefault(c["ref"], []).append(n)
    dref = {k: v for k, v in refs.items() if len(v) > 1}
    checks.append(_c("unique_labels", "Every component has its own label and every electrical ref names one component",
                     "failed" if (dup or dref) else "passed",
                     f"{len(comps)} labels, {len(dup)} duplicated; {len(refs)} refs, {len(dref)} shared"
                     + (f"; duplicate labels: {dup}" if dup else "") + (f"; shared refs: {dref}" if dref else ""), "QB-06",
                     {"duplicate_labels": dup, "shared_refs": dref}))
    # tip-over ------------------------------------------------------------------
    tp = tip_over(model)
    if tp:
        st = "passed" if tp["min_tip_angle_deg"] >= 15 else ("warning" if tp["min_tip_angle_deg"] >= 10 else "failed")
        checks.append(_c("tip_over", "Static tip-over angle from CAD centre of gravity (>= 15 deg)", st,
                         f"mass {tp['mass_g']} g (estimate), CoG {tp['cog_mm']} mm, minimum tip angle {tp['min_tip_angle_deg']} deg on {tp['support']}",
                         None, tp))
    return {"schema": "gadget-cad-checks/1", "checks": checks, "limits": LIMITS, "seconds": round(time.time() - t0, 1)}
