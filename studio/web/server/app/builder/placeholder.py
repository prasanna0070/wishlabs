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


# ------------------------------------------------------------------ outline from any body family
def front_outline(spec: dict) -> list[tuple[float, float]]:
    body = spec.get("body") or {}
    fam = body.get("family")
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


def render_png(spec: dict, size: int = 512) -> bytes:
    """Front view: body silhouette in the body colour, eyes/mouth/features as simple marks."""
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
