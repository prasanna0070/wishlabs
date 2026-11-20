"""Pure-Python placeholder geometry for the fake builder: a flat front-view PNG and a coarse GLB of the body.

These are NOT CAD output. They exist so the frontend can be developed before the real pipeline runs, and
every file made here says so ("placeholder").
"""
from __future__ import annotations

import json
import math
import struct
import zlib


def _hex_rgb(h: str | None, default=(140, 201, 192)) -> tuple[int, int, int]:
    try:
        h = (h or "").lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return default


# ------------------------------------------------------------------ csg bodies (pack freeform)
CSG_GRID = (72, 32)          # silhouette rows and columns on a csg body: with the engine's numpy sdf, point by point
CSG_DEPTH_SAMPLES = (20, 10)  # samples through the body along the view direction (same two cases)


def _csg_shape(body: dict):
    """The engine's compiled csg shape (gstudio.csg.compile_tree; light, no CadQuery), or None without it."""
    try:
        from gstudio import csg  # noqa: PLC0415

        return csg.compile_tree(body.get("csg"))
    except Exception:  # noqa: BLE001 - engine without the pack, or a tree the engine refuses
        return None


def _csg_rough_bounds(node, off=(0.0, 0.0, 0.0)) -> tuple[float, float, float, float, float, float]:
    """Box around a csg tree from sizes and move offsets only (turns ignored): the fallback without the engine."""
    if not isinstance(node, dict):
        return (-50.0, 50.0, -50.0, 50.0, 0.0, 100.0)
    op = node.get("op")
    ox, oy, oz = off
    try:
        if op == "move":
            t = node.get("to") or [0, 0, 0]
            return _csg_rough_bounds(node.get("node"), (ox + float(t[0]), oy + float(t[1]), oz + float(t[2])))
        if op in ("union", "intersect"):
            bs = [_csg_rough_bounds(n, off) for n in node.get("nodes") or []] or [_csg_rough_bounds(None)]
            return (min(b[0] for b in bs), max(b[1] for b in bs), min(b[2] for b in bs), max(b[3] for b in bs),
                    min(b[4] for b in bs), max(b[5] for b in bs))
        if op == "cut":
            return _csg_rough_bounds(node.get("node"), off)
        if op == "box":
            sx, sy, sz = (float(v) for v in node.get("size") or [100, 100, 100])
            return (ox - sx / 2, ox + sx / 2, oy - sy / 2, oy + sy / 2, oz, oz + sz)
        if op in ("cylinder", "cone"):
            r = max(float(node.get("r") or 0), float(node.get("r1") or 0), float(node.get("r2") or 0))
            return (ox - r, ox + r, oy - r, oy + r, oz, oz + float(node.get("h") or 0))
        if op == "sphere":
            r = float(node.get("r") or 0)
            return (ox - r, ox + r, oy - r, oy + r, oz - r, oz + r)
        if op == "torus":
            R, r = float(node.get("R") or 0), float(node.get("r") or 0)
            return (ox - R - r, ox + R + r, oy - R - r, oy + R + r, oz - r, oz + r)
        if op == "revolve":
            pts = node.get("points") or [[50, 0], [50, 100]]
            r, zs = max(float(q[0]) for q in pts), [float(q[1]) for q in pts]
            return (ox - r, ox + r, oy - r, oy + r, oz + min(zs), oz + max(zs))
        if op == "extrude":
            pts, h, ax = node.get("points") or [[0, 0]], float(node.get("h") or 0), node.get("axis") or "z"
            a, b = [float(q[0]) for q in pts], [float(q[1]) for q in pts]
            if ax == "y":
                return (ox + min(a), ox + max(a), oy - h / 2, oy + h / 2, oz + min(b), oz + max(b))
            if ax == "x":
                return (ox - h / 2, ox + h / 2, oy + min(a), oy + max(a), oz + min(b), oz + max(b))
            return (ox + min(a), ox + max(a), oy + min(b), oy + max(b), oz, oz + h)
    except (TypeError, ValueError, IndexError):
        pass
    return (ox - 50, ox + 50, oy - 50, oy + 50, oz, oz + 100)


_CSG_CACHE: dict[str, dict] = {}


def csg_views(body: dict) -> dict:
    """csg_views_uncached, remembered per tree (a preview asks for the outline, the envelope and the GLB)."""
    key = json.dumps(body.get("csg"), sort_keys=True, default=str)
    if key not in _CSG_CACHE:
        if len(_CSG_CACHE) > 64:
            _CSG_CACHE.clear()
        _CSG_CACHE[key] = csg_views_uncached(body)
    return _CSG_CACHE[key]


def csg_views_uncached(body: dict) -> dict:
    """Front (x, z) and side (y, z) silhouettes of a csg body as row-span polygons, sampled from the engine's signed
    distance; a box from the tree's sizes without the engine. {front, side, bounds}. Not CAD."""
    shape = _csg_shape(body)
    if shape is None:
        b = _csg_rough_bounds(body.get("csg"))
        x0, x1, y0, y1, z0, z1 = b[0], b[1], b[2], b[3], max(0.0, b[4]), b[5]
        return {"front": [(x0, 0.0), (x1, 0.0), (x1, z1), (x0, z1)], "side": [(y0, 0.0), (y1, 0.0), (y1, z1), (y0, z1)],
                "bounds": (x0, x1, y0, y1, 0.0, z1)}
    x0, x1, y0, y1, z0, z1 = (float(v) for v in shape.bounds)
    z0 = max(0.0, z0)
    fast = 0 if callable(getattr(shape, "sdf_np", None)) else 1
    n, m = CSG_GRID[fast], CSG_DEPTH_SAMPLES[fast]

    def lin(a, b, cnt):
        return [a + (b - a) * (k + 0.5) / cnt for k in range(cnt)]

    zs, xs, ys = lin(z0, z1, n), lin(x0, x1, n), lin(y0, y1, n)
    xd, yd = lin(x0, x1, m), lin(y0, y1, m)
    front_mask = _csg_mask(shape, zs, xs, yd, front=True)       # [z][x]: any sample along y inside
    side_mask = _csg_mask(shape, zs, ys, xd, front=False)       # [z][y]: any sample along x inside

    def spans(us, mask, u0, u1):
        right, left = [], []
        half = (us[1] - us[0]) / 2 if len(us) > 1 else 1.0      # a hit cell spans half a cell either side
        for z, row in zip(zs, mask):
            hit = [u for u, h in zip(us, row) if h]
            if hit:
                right.append((max(hit) + half, z))
                left.append((min(hit) - half, z))
        if not right:
            return [(u0, 0.0), (u1, 0.0), (u1, z1), (u0, z1)]
        return [(left[0][0], 0.0), (right[0][0], 0.0)] + right + list(reversed(left))

    return {"front": spans(xs, front_mask, x0, x1), "side": spans(ys, side_mask, y0, y1),
            "bounds": (x0, x1, y0, y1, 0.0, z1)}


def _csg_mask(shape, zs: list, us: list, ws: list, front: bool) -> list[list[bool]]:
    """[z][u] True where any sample w along the view direction is inside the shape (front: u = x, w = y; side: u = y,
    w = x). The engine's vectorised sdf_np when there is one, else its scalar sdf."""
    try:
        import numpy as np

        Z, U, W = np.meshgrid(np.asarray(zs), np.asarray(us), np.asarray(ws), indexing="ij")
        P = np.stack([U, W, Z], -1) if front else np.stack([W, U, Z], -1)
        d = np.asarray(shape.sdf_np(P.reshape(-1, 3))).reshape(Z.shape)
        return (d <= 0).any(axis=2).tolist()
    except Exception:  # noqa: BLE001 - no numpy or no sdf_np: sample point by point
        pass

    def inside(x, y, z):
        try:
            return float(shape.sdf(x, y, z)) <= 0
        except Exception:  # noqa: BLE001
            return False
    return [[any(inside(u, w, z) if front else inside(w, u, z) for w in ws) for u in us] for z in zs]


# ------------------------------------------------------------------ outline from any body family
def front_outline(spec: dict) -> list[tuple[float, float]]:
    body = spec.get("body") or {}
    fam = body.get("family")
    if fam == "csg" and isinstance(body.get("csg"), dict):
        return csg_views(body)["front"]
    if fam == "silhouette" and body.get("outline"):
        return [(float(x), float(z)) for x, z in body["outline"]]
    if fam == "revolve" and body.get("profile"):
        prof = [(float(r), float(z)) for r, z in body["profile"]]
        right = prof
        left = [(-r, z) for r, z in reversed(prof)]
        return right + left
    if fam == "blob" and body.get("primitives"):
        xs, zs = [], []
        for pr in body["primitives"]:
            c = pr.get("center", [0, 0, 0])
            s = pr.get("size", [50, 50, 50])
            xs += [c[0] - s[0] / 2, c[0] + s[0] / 2]
            zs += [c[2] - s[2] / 2, c[2] + s[2] / 2]
        x0, x1, z0, z1 = min(xs), max(xs), max(0.0, min(zs)), max(zs)
        cx, cz, rx, rz = (x0 + x1) / 2, (z0 + z1) / 2, (x1 - x0) / 2, (z1 - z0) / 2
        return [(cx + rx * math.cos(t), max(0.0, cz + rz * math.sin(t)))
                for t in (i * 2 * math.pi / 48 for i in range(48))]
    return [(-60, 0), (60, 0), (70, 60), (50, 140), (0, 160), (-50, 140), (-70, 60)]


def envelope_mm(spec: dict) -> dict:
    """Approximate W x D x H from the spec geometry (front outline and depth). Not a CAD measurement."""
    pts = front_outline(spec)
    xs = [p[0] for p in pts]
    zs = [p[1] for p in pts]
    body = spec.get("body") or {}
    depth = body.get("depth")
    if depth is None and body.get("family") == "revolve":
        depth = (max(xs) - min(xs)) * float(body.get("squash_y") or 1.0)
    if depth is None and body.get("family") == "csg" and isinstance(body.get("csg"), dict):
        b = csg_views(body)["bounds"]
        depth = b[3] - b[2]
    if depth is None and body.get("primitives"):
        ys = []
        for pr in body["primitives"]:
            c, s = pr.get("center", [0, 0, 0]), pr.get("size", [0, 0, 0])
            ys += [c[1] - s[1] / 2, c[1] + s[1] / 2]
        depth = max(ys) - min(ys) if ys else None
    h = max(zs)
    for d in spec.get("decor") or []:
        if d.get("type") == "ear" and d.get("at") and d.get("size"):
            h = max(h, d["at"][1] + d["size"][1] / 2)
    return {"w_mm": round(max(xs) - min(xs)), "d_mm": round(depth) if depth else None, "h_mm": round(h)}


# ------------------------------------------------------------------ PNG
def _png(w: int, h: int, rows: list[bytearray]) -> bytes:
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _inside(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def _render_csg_png(spec: dict, size: int) -> bytes:
    """A csg body (pack freeform): its front view (with the feature and decor marks) and, to its right, its side
    view (front to the left), both silhouettes sampled from the engine's csg shape. Not CAD."""
    bg, ground = (236, 234, 228), (200, 197, 188)
    body_c = _hex_rgb((spec.get("colors") or {}).get("body"))
    side_c = tuple(int(c * 0.82) for c in body_c)
    detail_c = _hex_rgb((spec.get("colors") or {}).get("detail"), (43, 58, 66))
    accent_c = _hex_rgb((spec.get("colors") or {}).get("accent"), (244, 241, 234))
    v = csg_views(spec.get("body") or {})
    front, side = v["front"], v["side"]                             # side view (y, z): the front (-y) on the left
    fx = [q[0] for q in front]
    sx = [q[0] for q in side]
    zt = max(max(q[1] for q in front), max(q[1] for q in side), 1.0)
    fw, sw = max(fx) - min(fx), max(sx) - min(sx)
    gap = 0.12 * max(fw, sw, zt)
    span = max(fw + sw + gap, zt) * 1.2
    off_f = -span / 2 + (span - (fw + sw + gap)) / 2 - min(fx)      # shift front so the pair is centred
    off_s = off_f + max(fx) + gap - min(sx)
    cz = zt / 2
    marks = []
    for d in spec.get("decor") or []:
        at, sz = d.get("at"), d.get("size") or [8, 8]
        if at and isinstance(d, dict):
            col = detail_c if d.get("type") in ("eye", "mouth", "text") else accent_c
            marks.append((at[0], at[1], sz[0] / 2, sz[1] / 2, col))
    for f in spec.get("features") or []:
        at = f.get("at")
        if at and (f.get("face") or "front") == "front":
            g = f.get("grille")
            marks.append((at[0], at[1], (g or {}).get("w", 12) / 2, (g or {}).get("h", 12) / 2,
                          (120, 128, 134) if g else accent_c))
    marks.sort(key=lambda m: -(m[2] * m[3]))
    rows = []
    for py in range(size):
        row = bytearray()
        z = cz + (0.5 - py / size) * span
        for px in range(size):
            u = (px / size - 0.5) * span
            c = bg
            if z >= 0 and _inside(u - off_f, z, front):
                c = body_c
                for mx, mz, rx, rz, mc in marks:
                    if ((u - off_f - mx) / max(rx, 0.5)) ** 2 + ((z - mz) / max(rz, 0.5)) ** 2 <= 1:
                        c = mc
            elif z >= 0 and _inside(u - off_s, z, side):
                c = side_c
            elif abs(z) < span / size:
                c = ground
            row += bytes(c)
        rows.append(row)
    return _png(size, size, rows)


def render_png(spec: dict, size: int = 512) -> bytes:
    """Front view: body silhouette in the body colour, eyes/mouth/features as simple marks."""
    if (spec.get("body") or {}).get("family") == "csg" and isinstance((spec.get("body") or {}).get("csg"), dict):
        return _render_csg_png(spec, size)
    bg = (236, 234, 228)
    body_c = _hex_rgb((spec.get("colors") or {}).get("body"))
    detail_c = _hex_rgb((spec.get("colors") or {}).get("detail"), (43, 58, 66))
    accent_c = _hex_rgb((spec.get("colors") or {}).get("accent"), (244, 241, 234))
    poly = front_outline(spec)
    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
    span = max(max(xs) - min(xs), max(zs) - min(zs), 1.0) * 1.25
    cx, cz = (max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2

    def to_mm(px: int, py: int) -> tuple[float, float]:
        return cx + (px / size - 0.5) * span, cz + (0.5 - py / size) * span

    marks: list[tuple[float, float, float, float, tuple]] = []   # x, z, rx, rz, colour
    for d in spec.get("decor") or []:
        at, sz = d.get("at"), d.get("size") or [8, 8]
        if not at:
            continue
        col = detail_c if d.get("type") in ("eye", "mouth", "text") else accent_c
        if d.get("type") == "face_plate":
            col = accent_c
        marks.append((at[0], at[1], sz[0] / 2, sz[1] / 2, col))
        if d.get("type") == "ear" and d.get("side", "both") == "both" and at[0] != 0:
            marks.append((-at[0], at[1], sz[0] / 2, sz[1] / 2, col))
    for f in spec.get("features") or []:
        at = f.get("at")
        if not at:
            continue
        g = f.get("grille")
        if g:
            marks.append((at[0], at[1], g.get("w", 30) / 2, g.get("h", 20) / 2, (120, 128, 134)))
        else:
            marks.append((at[0], at[1], 6, 6, accent_c))
    marks.sort(key=lambda m: -(m[2] * m[3]))   # big plates first, small eyes on top
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            x, z = to_mm(px, py)
            c = bg
            if z >= 0 and _inside(x, z, poly):
                c = body_c
                for mx, mz, rx, rz, mc in marks:
                    if ((x - mx) / max(rx, 0.5)) ** 2 + ((z - mz) / max(rz, 0.5)) ** 2 <= 1:
                        c = mc
            elif abs(z) < span / size:
                c = (200, 197, 188)
            row += bytes(c)
        rows.append(row)
    return _png(size, size, rows)


# ------------------------------------------------------------------ GLB
def _extrude(poly: list[tuple[float, float]], depth: float):
    """Fan-triangulated prism from the outline's centroid (fine for star-shaped outlines). Glb axes: Y up."""
    n = len(poly)
    cx = sum(p[0] for p in poly) / n
    cz = sum(p[1] for p in poly) / n
    verts: list[tuple[float, float, float]] = []
    idx: list[int] = []
    y0, y1 = -depth / 2, depth / 2
    # gadget coords (x, y, z) -> glTF (x, z, -y)  (glTF is +Y up, +Z toward viewer = gadget -Y front)
    def g(x, y, z):
        return (x / 1000, z / 1000, -y / 1000)
    for y in (y0, y1):
        base = len(verts)
        verts.append(g(cx, y, cz))
        for x, z in poly:
            verts.append(g(x, y, z))
        for i in range(n):
            a, b = base + 1 + i, base + 1 + (i + 1) % n
            idx += [base, a, b] if y == y0 else [base, b, a]
    for i in range(n):
        (xa, za), (xb, zb) = poly[i], poly[(i + 1) % n]
        base = len(verts)
        verts += [g(xa, y0, za), g(xb, y0, zb), g(xb, y1, zb), g(xa, y1, za)]
        idx += [base, base + 2, base + 1, base, base + 3, base + 2]
    return verts, idx


def render_glb(spec: dict, name: str = "PRINT_Shell") -> bytes:
    body = spec.get("body") or {}
    poly = front_outline(spec)
    env = envelope_mm(spec)
    verts, idx = _extrude(poly, float(body.get("depth") or env.get("d_mm") or 100))
    r, g_, b = _hex_rgb((spec.get("colors") or {}).get("body"))
    vb = b"".join(struct.pack("<3f", *v) for v in verts)
    ib = b"".join(struct.pack("<I", i) for i in idx)
    pad = lambda bts, ch=b"\x00": bts + ch * ((4 - len(bts) % 4) % 4)  # noqa: E731
    vb, ib = pad(vb), pad(ib)
    xs, ys, zs = zip(*verts)
    gltf = {
        "asset": {"version": "2.0", "generator": "gstudio-web placeholder (not CAD)"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0, "extras": {"placeholder": True}}],
        "meshes": [{"name": name, "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"name": "body", "pbrMetallicRoughness": {
            "baseColorFactor": [r / 255, g_ / 255, b / 255, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
        "buffers": [{"byteLength": len(vb) + len(ib)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(vb), "target": 34962},
                        {"buffer": 0, "byteOffset": len(vb), "byteLength": len(ib), "target": 34963}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3",
             "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"}],
    }
    js = pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    binc = vb + ib
    total = 12 + 8 + len(js) + 8 + len(binc)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(binc), 0x004E4942) + binc)
