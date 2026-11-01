"""The full outer envelope the CAD will build for a spec, in pure Python (the web image has no CadQuery or numpy).

    from gstudio.envelope import full_envelope
    env = full_envelope(spec, geom)     # geom = normalize.ShellGeometry(spec)
    env["bbox"]  -> (x0, x1, y0, y1, z0, z1) mm, body frame (table plane z = 0)
    env["ears"]  -> one record per placed ear / antenna: x used, base z, tilt, bounds

Why it exists (live failure 2026-11-02, "Storybot Pal"): normalize estimated 148 x 105 x 195 mm, while the CAD shell
came out 167.6 x 97.6 x 212.7 mm and could not be sliced upright on a 210 mm Z printer. Two causes, both fixed here:
  1. The CAD's silhouette outline is an OpenCascade interpolating spline (cadquery Edge.makeSpline, no tangents), which
     overshoots between far-apart points far more than the Catmull-Rom approximation in spec.outline_points:
     cad_spline() reproduces it (bounds within 0.01 mm of cadquery 2.8 on the benchmark spec, the three examples and
     two more outlines, checked on the dev VM 2026-11-02).
  2. Ears and antennae are not placed at their spec `at`: gstudio.cad.decor.apply_ears stands them on the top surface
     above x, tilted to the outline normal plus `tilt`, sunk until their base is inside, and moves them inward until
     their outboard edge stays 2 mm inside the side outline. place_ears() repeats that search on the y = 0 section.

The ear outline is a conservative box (w x h, bunny 1.1 h) except the antenna, whose rod, ball and base disc follow
apply_ears exactly. Revolve and blob bodies keep ShellGeometry's bbox; their ears use its distance field.
"""

from __future__ import annotations

import math
from typing import Any, Optional

EAR_MARGIN = 2.0  # mm the CAD keeps between an ear's outboard edge and the side outline (decor.ear_envelope)


# =============================================================================================== the CAD's outline spline
def outline_sequence(outline) -> tuple[list[tuple[float, float]], tuple[float, float], tuple[float, float]]:
    """(points from the right end of the flat bottom, over the top, to its left end; left; right), as
    gstudio.cad.body._outline_sequence orders them for the CAD spline."""
    pts = [(float(x), float(z)) for x, z in outline]
    for _ in range(2):
        n = len(pts)
        on = [abs(z) < 1e-3 for _, z in pts]
        s = next(i for i in range(n) if on[i] and not on[i - 1])  # first point of the bottom run
        e = s
        while on[(e + 1) % n]:
            e = (e + 1) % n
        left, right = pts[s], pts[e]
        if left[0] > right[0]:
            pts = pts[::-1]
            continue
        seq, k = [], e
        while True:
            seq.append(pts[k])
            if k == s:
                break
            k = (k + 1) % n
        return seq, left, right
    raise ValueError("outline has no flat bottom run")


def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-15:
            raise ValueError("singular spline system")
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * q for a, q in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _lagrange_slope(ts: list[float], ys: list[float], t0: float) -> float:
    """d/dt at t0 of the polynomial through (ts, ys)."""
    d = 0.0
    for j in range(len(ts)):
        tot = 0.0
        for m in range(len(ts)):
            if m == j:
                continue
            p = 1.0 / (ts[j] - ts[m])
            for k in range(len(ts)):
                if k not in (j, m):
                    p *= (t0 - ts[k]) / (ts[j] - ts[k])
            tot += p
        d += ys[j] * tot
    return d


def _clamped_second_derivs(ts: list[float], ys: list[float], d0: float, d1: float) -> list[float]:
    n = len(ts)
    h = [ts[i + 1] - ts[i] for i in range(n - 1)]
    A = [[0.0] * n for _ in range(n)]
    b = [0.0] * n
    A[0][0], A[0][1], b[0] = 2 * h[0], h[0], 6 * ((ys[1] - ys[0]) / h[0] - d0)
    for i in range(1, n - 1):
        A[i][i - 1], A[i][i], A[i][i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
        b[i] = 6 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])
    A[n - 1][n - 2], A[n - 1][n - 1], b[n - 1] = h[n - 2], 2 * h[n - 2], 6 * (d1 - (ys[n - 1] - ys[n - 2]) / h[n - 2])
    return _solve(A, b)


def cad_spline(seq: list[tuple[float, float]], samples: int = 24) -> list[tuple[float, float]]:
    """Points along the curve OpenCascade's GeomAPI_Interpolate puts through seq (what cadquery Edge.makeSpline(points)
    builds): chord-length parameters; 2 points a line, 3 a parabola, otherwise a C2 cubic clamped at each end to the
    slope of the cubic through the first / last four points."""
    pts = [(float(x), float(z)) for x, z in seq]
    # coincident points carry no parameter span; OCC would reject them too, so drop repeats
    clean = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, clean[-1]) > 1e-9:
            clean.append(p)
    pts = clean
    n = len(pts)
    if n < 2:
        return pts
    ts = [0.0]
    for a, b in zip(pts, pts[1:]):
        ts.append(ts[-1] + math.dist(a, b))
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    if n == 2:
        return [(xs[0] + (xs[1] - xs[0]) * s / samples, zs[0] + (zs[1] - zs[0]) * s / samples) for s in range(samples + 1)]
    if n == 3:
        def quad(ys, t):
            return sum(ys[j] * math.prod((t - ts[k]) / (ts[j] - ts[k]) for k in range(3) if k != j) for j in range(3))
        T = ts[-1]
        return [(quad(xs, T * s / (2 * samples)), quad(zs, T * s / (2 * samples))) for s in range(2 * samples + 1)]
    q = 4
    Mx = _clamped_second_derivs(ts, xs, _lagrange_slope(ts[:q], xs[:q], ts[0]), _lagrange_slope(ts[-q:], xs[-q:], ts[-1]))
    Mz = _clamped_second_derivs(ts, zs, _lagrange_slope(ts[:q], zs[:q], ts[0]), _lagrange_slope(ts[-q:], zs[-q:], ts[-1]))
    out: list[tuple[float, float]] = []
    for i in range(n - 1):
        h = ts[i + 1] - ts[i]
        for s in range(samples):
            t = ts[i] + h * s / samples
            a, b = ts[i + 1] - t, t - ts[i]

            def S(y, M):
                return M[i] * a ** 3 / (6 * h) + M[i + 1] * b ** 3 / (6 * h) + (y[i] / h - M[i] * h / 6) * a + (y[i + 1] / h - M[i + 1] * h / 6) * b
            out.append((S(xs, Mx), S(zs, Mz)))
    out.append(pts[-1])
    return out


def cad_outline_polygon(outline, samples: int = 24) -> list[tuple[float, float]]:
    """Closed front-view (x, z) polygon of a silhouette body as the CAD builds it: the spline over the top, closed by
    the straight bottom line (the polygon runs right bottom -> top -> left bottom)."""
    seq, _left, _right = outline_sequence(outline)
    return cad_spline(seq, samples)


# =============================================================================================== y = 0 sections
class PolySection:
    """The body's y = 0 section as a closed polygon (x, z)."""

    def __init__(self, pts: list[tuple[float, float]]):
        self.pts = list(pts)
        n = len(self.pts)
        self.segs = [(self.pts[i], self.pts[(i + 1) % n]) for i in range(n)]
        xs, zs = [p[0] for p in self.pts], [p[1] for p in self.pts]
        self.bounds = (min(xs), min(zs), max(xs), max(zs))

    def top_z(self, x: float) -> Optional[float]:
        best = None
        for (ax, az), (bx, bz) in self.segs:
            if (ax - x) * (bx - x) > 0 or ax == bx:
                continue
            z = az + (bz - az) * (x - ax) / (bx - ax)
            best = z if best is None else max(best, z)
        return best

    def half_width(self, z: float) -> Optional[float]:
        best = None
        for (ax, az), (bx, bz) in self.segs:
            if (az - z) * (bz - z) > 0 or az == bz:
                continue
            x = ax + (bx - ax) * (z - az) / (bz - az)
            best = x if best is None else max(best, x)
        return best

    def ray(self, o: tuple[float, float], d: tuple[float, float]) -> Optional[float]:
        best = None
        for (ax, az), (bx, bz) in self.segs:
            ex, ez = bx - ax, bz - az
            den = d[0] * ez - d[1] * ex
            if abs(den) < 1e-12:
                continue
            wx, wz = ax - o[0], az - o[1]
            t = (wx * ez - wz * ex) / den
            u = (wx * d[1] - wz * d[0]) / den
            if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                best = t if best is None else min(best, t)
        return best


class SdfSection:
    """The y = 0 section of a revolve or blob body from ShellGeometry's distance field (approximate)."""

    def __init__(self, geom, far: float = 400.0):
        self.geom, self.far = geom, far
        x0, x1, _, _, z0, z1 = geom.bbox
        self.bounds = (x0, z0, x1, z1)

    def top_z(self, x: float) -> Optional[float]:
        t = self.geom.surface_depth("top", x, 0.0, self.far)
        return None if t is None else self.far - t

    def half_width(self, z: float) -> Optional[float]:
        t = self.geom.surface_depth("left", 0.0, z, self.far)
        return None if t is None else self.far - t

    def ray(self, o: tuple[float, float], d: tuple[float, float]) -> Optional[float]:
        t = 0.0
        for _ in range(400):
            s = self.geom.outer_sdf(o[0] + d[0] * t, 0.0, o[1] + d[1] * t)
            if s <= 0.05:
                return t
            t += max(s * 0.9, 0.25)
            if t > 2 * self.far:
                return None
        return None


def section_for(spec, geom):
    b = spec.body
    if getattr(b, "family", None) == "silhouette":
        return PolySection(cad_outline_polygon(b.outline))
    return SdfSection(geom)


# =============================================================================================== ears (decor.apply_ears)
def side_top(sec) -> float:
    """decor.side_top: the height where the right side wall ends (the outline turns more horizontal than 45 deg)."""
    _x0, minz, _x1, maxz = sec.bounds
    z = max(minz + 2.0, 1.0)
    zs = []
    while z < maxz:
        zs.append(z)
        z += 1.0
    if not zs:
        return maxz
    W = [sec.half_width(z) for z in zs]
    zt = zs[0]
    for z, a, b in zip(zs[:-1], W[:-1], W[1:]):
        if a is None or b is None:
            break
        if b - a < -1.0 and z > minz + 0.3 * (maxz - minz):
            return z
        zt = z
    return zt


def _ear_positions(at_x: float, side: str) -> list[float]:
    if side == "both":
        return [at_x, -at_x] if abs(at_x) > 1e-6 else [at_x]
    if side == "left":  # robot's left = +X
        return [abs(at_x)]
    return [-abs(at_x)]


def _seg_points(a, b, step: float = 0.25) -> list[tuple[float, float]]:
    n = max(1, int(math.ceil(math.dist(a, b) / step)))
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]


def _rect(x0, x1, z0, z1, step=0.25):
    c = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    return [p for i in range(4) for p in _seg_points(c[i], c[(i + 1) % 4], step)]


def ear_outline_points(style: str, w: float, h: float) -> list[tuple[float, float]]:
    """Boundary samples of the ear's visible part (v >= 0) in its own frame: base centre at (0, 0), tip toward +v."""
    if style == "antenna":  # decor.apply_ears: ball + rod (rod_r) + base disc, each 0.2 mm generous
        rod_r = max(2.4, min(w * 0.24, 3.6))
        top = h - w / 2
        r = w / 2 + 0.2
        ball = [(r * math.cos(2 * math.pi * k / 96), top + r * math.sin(2 * math.pi * k / 96)) for k in range(96)]
        return ball + _rect(-rod_r - 0.2, rod_r + 0.2, 0.0, top) + _rect(-2.4 * rod_r - 0.2, 2.4 * rod_r + 0.2, 0.0, 2.2)
    # cat, bear, owl_tuft stay inside w x h; the bunny's oval reaches 1.08 h (cad.shapes2d.ear)
    return _rect(-w / 2, w / 2, 0.0, h * (1.1 if style == "bunny" else 1.0))


def _surface_normal_at(sec, x: float):
    z0, z1, z2 = sec.top_z(x - 1.5), sec.top_z(x), sec.top_z(x + 1.5)
    if z1 is None:
        return None, None
    slope = (z2 - z0) / 3.0 if z0 is not None and z2 is not None else 0.0
    nn = math.hypot(slope, 1.0)
    return z1, (-slope / nn, 1.0 / nn)


def _sink_depth(sec, x: float, z: float, n, w: float) -> float:
    t = (n[1], -n[0])
    worst = 0.0
    for a in (-0.45 * w, -0.3 * w, 0.3 * w, 0.45 * w):
        p = (x + t[0] * a, z + t[1] * a)
        o = (p[0] + n[0] * 30.0, p[1] + n[1] * 30.0)
        dist = sec.ray(o, (-n[0], -n[1]))
        if dist is not None:
            worst = max(worst, dist - 30.0)
    return worst + 0.8


def _placed(pts, phi_deg: float, x: float, z: float, sink: float, n) -> list[tuple[float, float]]:
    c, s = math.cos(math.radians(phi_deg)), math.sin(math.radians(phi_deg))
    ox, oz = x - n[0] * sink, z - n[1] * sink
    return [(px * c - pz * s + ox, px * s + pz * c + oz) for px, pz in pts]


def _excess(P, E) -> float:
    """decor.apply_ears: how far the ear's outboard edge passes E(z), in 0.5 mm bands every 1 mm of height."""
    minz = min(p[1] for p in P)
    reach: dict[int, float] = {}
    for px, pz in P:
        k = int(round(pz - minz))
        if abs(pz - (minz + k)) <= 0.25:
            reach[k] = max(reach.get(k, 0.0), abs(px))
    return max([0.0] + [r - E(minz + k) for k, r in reach.items()])


def place_ears(spec, sec) -> tuple[list[dict], list[str]]:
    """Where the CAD will put each ear: [{x, x_requested, z_base, tilt_deg, sink_mm, bounds: (x0, x1, z0, z1)}]."""
    ears = [d for d in spec.decor if getattr(d, "type", None) == "ear"]
    if not ears:
        return [], []
    notes: list[str] = []
    z_side = side_top(sec)
    w_top = sec.half_width(z_side) or 0.0
    cache: dict[float, float] = {}

    def E(z: float) -> float:
        k = round(z, 2)
        if k not in cache:
            hw = sec.half_width(z) if z <= z_side else w_top
            cache[k] = (hw if hw is not None else w_top) - EAR_MARGIN
        return cache[k]

    out = []
    for d in ears:
        w, h = float(d.size[0]), float(d.size[1])
        base = ear_outline_points(d.style, w, h)
        for x_req in _ear_positions(float(d.at[0]), d.side):
            sign = 1.0 if x_req >= 0 else -1.0
            pts = base if sign > 0 else [(-px, pz) for px, pz in base]
            user_tilt = -sign * float(d.tilt or 0.0)
            x_min = 0.0 if abs(x_req) < 1e-6 else (w / 2 + 1.0)
            if abs(x_req) < 1e-6:
                xs = [x_req]
            else:
                xs, xx = [], abs(x_req)
                while xx > x_min - 1e-6:
                    xs.append(sign * xx)
                    xx -= 1.0
            best = None
            for x in xs:
                zs, n = _surface_normal_at(sec, x)
                if zs is None:
                    continue
                phi = -math.degrees(math.atan2(n[0], n[1])) + user_tilt
                sink = _sink_depth(sec, x, zs, n, w)
                P = _placed(pts, phi, x, zs, sink, n)
                ex = _excess(P, E)
                cand = {"x": x, "z": zs, "phi": phi, "sink": sink, "P": P, "excess": ex}
                if best is None or ex < best["excess"] - 1e-9:
                    best = cand
                if ex <= 0.0:
                    best = cand
                    break
            if best is None:
                notes.append(f"ear at x={x_req:g} misses the top of the body (the CAD skips it)")
                continue
            P = best["P"]
            out.append({"style": d.style, "x": round(best["x"], 1), "x_requested": round(x_req, 1), "z_base": round(best["z"], 1),
                        "tilt_deg": round(-best["phi"] * sign, 1), "sink_mm": round(best["sink"], 2),
                        "bounds": (min(p[0] for p in P), max(p[0] for p in P), min(p[1] for p in P), max(p[1] for p in P))})
    return out, notes


# =============================================================================================== envelope
def full_envelope(spec, geom) -> dict[str, Any]:
    """{bbox, body_bbox, ears, notes, source}: the body as the CAD shapes it plus every ear and antenna."""
    b = spec.body
    notes: list[str] = []
    sec = None
    try:
        sec = section_for(spec, geom)
    except Exception as exc:  # noqa: BLE001 - a bad outline is reported by the spec checks; fall back to ShellGeometry
        notes.append(f"CAD outline not reproduced ({exc}); using the approximate outline")
    if isinstance(sec, PolySection):
        x0, z0, x1, z1 = sec.bounds
        body_bbox = (x0, x1, -b.depth / 2, b.depth / 2, 0.0, z1)
        source = "CAD spline (OpenCascade interpolation reproduced)"
    else:
        body_bbox = tuple(geom.bbox)
        source = "approximate outline (ShellGeometry)"
    ears: list[dict] = []
    if sec is None:
        sec = SdfSection(geom)
    try:
        ears, en = place_ears(spec, sec)
        notes += en
    except Exception as exc:  # noqa: BLE001 - never crash normalize; fall back to the spec boxes
        notes.append(f"ear placement not simulated ({exc}); using the spec ear boxes")
        for d in spec.decor:
            if getattr(d, "type", None) != "ear":
                continue
            for xe in _ear_positions(float(d.at[0]), d.side):
                ears.append({"style": d.style, "x": xe, "x_requested": xe, "z_base": float(d.at[1]),
                             "bounds": (xe - d.size[0] / 2, xe + d.size[0] / 2, float(d.at[1]) - d.size[1] / 2, float(d.at[1]) + d.size[1] / 2)})
    x0, x1, y0, y1, z0, z1 = body_bbox
    for e in ears:
        ex0, ex1, _ez0, ez1 = e["bounds"]
        x0, x1, z1 = min(x0, ex0), max(x1, ex1), max(z1, ez1)
    return {"bbox": (x0, x1, y0, y1, z0, z1), "body_bbox": tuple(body_bbox), "ears": ears, "notes": notes, "source": source}


def fit_orientations(size: tuple[float, float, float], bed: tuple[float, float, float]) -> dict[str, Any]:
    """Can a part of this size print upright (as built, optionally turned about Z) or only tipped on a side?"""
    x, y, z = size
    upright = z <= bed[2] + 1e-6 and ((x <= bed[0] + 1e-6 and y <= bed[1] + 1e-6) or (y <= bed[0] + 1e-6 and x <= bed[1] + 1e-6))
    srt, bs = sorted(size, reverse=True), sorted(bed, reverse=True)
    anyway = all(a <= b + 1e-6 for a, b in zip(srt, bs))
    scale = min([1.0] + [b / a for a, b in zip(srt, bs) if a > 0])
    xy = max(min(bed[0] / x, bed[1] / y), min(bed[0] / y, bed[1] / x)) if x > 0 and y > 0 else 1.0
    up_scale = min(1.0, bed[2] / z if z > 0 else 1.0, xy)
    return {"upright": upright, "any": anyway, "scale_to_fit": scale, "scale_to_fit_upright": up_scale}


__all__ = ["cad_spline", "cad_outline_polygon", "outline_sequence", "PolySection", "SdfSection", "place_ears", "full_envelope",
           "fit_orientations", "side_top", "ear_outline_points"]
