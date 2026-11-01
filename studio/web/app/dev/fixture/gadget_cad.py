"""Moon Hug: parametric CadQuery source for this design. DEV FIXTURE of Gadget Studio, not pipeline output.

Reads params.json (same folder by default) and builds every solid component of the assembly:

  PRINT_Shell        front + sides, hollow, 2.4 mm wall (3.2 mm front skin), engraved face, grille, holes, bosses
  PRINT_RearCover    the rear 3 mm cap, USB-C opening, counterbored screw holes
  PRINT_SpeakerRing  printed clamp ring for the 40 mm speaker
  PURCHASE_*         purchased parts as simple envelopes from params["parts"] (library dims, not factory CAD)

Coordinates: millimetres, +Z up, front faces -Y, the gadget stands on Z=0, origin at the footprint centre.

Run standalone (needs only cadquery, numpy, shapely):
    python gadget_cad.py [params.json] [outdir]
It writes <outdir>/assembly.step and one STL per PRINT_ part (assembly pose, not print pose).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cadquery as cq
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry import box as sbox

V = cq.Vector
BIG = 2000.0


# ================================================================================================ primitives
def box(x0, x1, y0, y1, z0, z1) -> cq.Solid:
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))


def cyl(c, axis, r, h) -> cq.Solid:
    ax = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis] if isinstance(axis, str) else axis
    return cq.Solid.makeCylinder(r, h, V(*c), V(*ax))


def cone(c, axis, r0, r1, h) -> cq.Solid:
    ax = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis] if isinstance(axis, str) else axis
    return cq.Solid.makeCone(r0, r1, h, V(*c), V(*ax))


def prism_xz(poly, y0: float, y1: float) -> cq.Solid:
    """Front-view polygon [(x, z)] extruded along +Y from y0 to y1."""
    pts = [V(float(x), y0, float(z)) for x, z in poly]
    w = cq.Wire.makePolygon(pts, close=True)
    return cq.Solid.extrudeLinear(cq.Face.makeFromWires(w), V(0, y1 - y0, 0))


def prism_xy(poly, z0: float, z1: float) -> cq.Solid:
    pts = [V(float(x), float(y), z0) for x, y in poly]
    w = cq.Wire.makePolygon(pts, close=True)
    return cq.Solid.extrudeLinear(cq.Face.makeFromWires(w), V(0, 0, z1 - z0))


def rrect_poly(cx, cz, w, h, r, res=6):
    return list(sbox(cx - w / 2 + r, cz - h / 2 + r, cx + w / 2 - r, cz + h / 2 - r).buffer(r, resolution=res).exterior.coords)[:-1]


def compound(shapes) -> cq.Compound:
    return cq.Compound.makeCompound([s for s in shapes if s is not None])


def prim(p: dict) -> cq.Shape:
    t = p["t"]
    if t == "box":
        (x0, y0, z0), (x1, y1, z1) = p["min"], p["max"]
        s = box(x0, x1, y0, y1, z0, z1)
        if p.get("fillet"):
            try:
                s = s.fillet(p["fillet"], s.Edges())
            except Exception:  # noqa: BLE001 - keep the sharp box
                pass
        return s
    if t == "cyl":
        return cyl(p["c"], p["axis"], p["r"], p["h"])
    if t == "cone":
        return cone(p["c"], p["axis"], p["r0"], p["r1"], p["h"])
    if t == "sphere":
        return cq.Solid.makeSphere(p["r"], V(*p["c"]), angleDegrees1=-90, angleDegrees2=90)
    if t == "ring":
        outer = cyl(p["c"], p["axis"], p["ro"], p["h"])
        return outer.cut(cyl(p["c"], p["axis"], p["ri"], p["h"]))
    raise ValueError(f"unknown primitive {t}")


# ================================================================================================ outline
def outline_sequence(outline):
    """Points from the right end of the flat bottom, counter-clockwise over the top, to its left end."""
    pts = [(float(x), float(z)) for x, z in outline]
    n = len(pts)
    on = [abs(z) < 1e-3 for _, z in pts]
    s = next(i for i in range(n) if on[i] and not on[i - 1])
    e = s
    while on[(e + 1) % n]:
        e = (e + 1) % n
    left, right = pts[s], pts[e]
    seq, k = [], e
    while True:
        seq.append(pts[k])
        if k == s:
            break
        k = (k + 1) % n
    return seq, left, right


def outline_wire(seq, left, right, y=0.0) -> cq.Wire:
    sp = cq.Edge.makeSpline([V(x, y, z) for x, z in seq])
    line = cq.Edge.makeLine(V(left[0], y, left[1]), V(right[0], y, right[1]))
    return cq.Wire.assembleEdges([sp, line])


def wire_xz(w: cq.Wire, n=240) -> np.ndarray:
    pts = []
    for e in w.Edges():
        for p in e.positions(list(np.linspace(0, 1, n, endpoint=False))):
            pts.append((p.x, p.z))
    return np.array(pts)


def resample_open(pts: np.ndarray, spacing: float) -> np.ndarray:
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    n = max(8, int(s[-1] / spacing))
    t = np.linspace(0, s[-1], n)
    return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1)


def inner_outline(outer_poly: Polygon, wall: float):
    """Inward offset of the outline as (spline sequence, left, right) with a straight bottom at z=wall."""
    inner = outer_poly.buffer(-wall, join_style=1)
    if inner.geom_type != "Polygon":
        inner = max(inner.geoms, key=lambda g: g.area)
    ring = np.asarray(inner.exterior.coords)[:-1]
    if Polygon(ring).exterior.is_ccw is False:
        ring = ring[::-1]
    zb = ring[:, 1].min()
    on = np.abs(ring[:, 1] - zb) < 1e-3
    xs = ring[on, 0]
    xl, xr = float(xs.min()), float(xs.max())
    n = len(ring)
    ir = int(np.where(on)[0][np.argmax(ring[on, 0])])
    il = int(np.where(on)[0][np.argmin(ring[on, 0])])
    seq, k = [], ir
    while True:
        seq.append(ring[k])
        if k == il:
            break
        k = (k + 1) % n
    seq = resample_open(np.asarray(seq), 2.0)
    seq[0], seq[-1] = (xr, zb), (xl, zb)
    return [tuple(p) for p in seq], (xl, zb), (xr, zb)


def try_fillet(solid: cq.Shape, edges, radius: float, factors=(1.0, 0.83, 0.67, 0.5, 0.33)):
    for f in factors:
        r = radius * f
        if r < 0.5:
            break
        try:
            out = solid.fillet(r, edges)
            if out.isValid() and out.Volume() > 0:
                return out, r
        except Exception:  # noqa: BLE001
            continue
    return solid, 0.0


def end_edges(s: cq.Shape, y: float):
    return [e for e in s.Edges() if abs(e.Center().y - y) < 1e-3]


# ================================================================================================ decor strokes
def arc_points(cx, cz, chord, sag, down=True, n=24):
    """Circular arc through both chord ends and the sagitta point. down=True: middle lower than ends (U shape)."""
    r = (chord ** 2 / 4 + sag ** 2) / (2 * sag)
    ccz = cz + (r - sag / 2) if down else cz - (r - sag / 2)
    half = math.asin(min(1.0, chord / 2 / r))
    pts = []
    for t in np.linspace(-half, half, n):
        x = cx + r * math.sin(t)
        z = ccz - r * math.cos(t) if down else ccz + r * math.cos(t)
        pts.append((x, z))
    return pts


def stroke(points, width):
    return list(LineString(points).buffer(width / 2, cap_style=1, resolution=8).exterior.coords)[:-1]


def decor_cuts(decor: list, y_front: float, plate_depth_at) -> list[cq.Shape]:
    tools = []
    for d in decor:
        t, (cx, cz) = d["type"], d["at"]
        w, h = d.get("size", [10, 10])
        depth = float(d.get("depth", 0.8))
        if t == "face_plate":
            tools.append(cyl((cx, y_front - 1, cz), "y", w / 2, 1 + depth))
            continue
        base = plate_depth_at(cx, cz)
        if t == "eye":
            sw = max(1.4, min(w, h) * 0.18)
            poly = stroke(arc_points(cx, cz, w - sw, (h - sw) * 0.5, down=d.get("style") != "arc_happy"), sw)
        elif t == "mouth":
            sw = max(1.6, h * 0.24)
            poly = stroke(arc_points(cx, cz, w - sw, h - sw, down=True), sw)
        else:
            continue
        tools.append(prism_xz(poly, y_front - 1, y_front + base + depth))
    return tools


def hex_grille(cx, cz, w, h, hole, pitch):
    """Hexagonal holes (hole = across flats) on a honeycomb grid, kept inside a w x h stadium."""
    rc = hole / 2 / math.cos(math.radians(30))
    region = Polygon(rrect_poly(cx, cz, w, h, h / 2 - 0.01, res=16)).buffer(-0.6)
    polys = []
    dz = pitch * math.sin(math.radians(60))
    nz = int(h / dz) + 2
    nx = int(w / pitch) + 2
    for j in range(-nz, nz + 1):
        z = cz + j * dz
        off = (pitch / 2) if j % 2 else 0.0
        for i in range(-nx, nx + 1):
            x = cx + i * pitch + off
            hexp = [(x + rc * math.cos(math.radians(30 + 60 * k)), z + rc * math.sin(math.radians(30 + 60 * k))) for k in range(6)]
            if region.contains(Polygon(hexp)):
                polys.append(hexp)
    return polys


# ================================================================================================ build
def build(params: dict) -> dict:
    """Returns {"components": {name: cq.Shape}, "info": {...}}."""
    b = params["body"]
    D, wall, skin = float(b["depth"]), float(b["wall"]), float(b["front_skin"])
    yF, yR = -D / 2, D / 2
    info: dict = {"warnings": []}

    # ---- outer solid: spline outline extruded along Y, front and rear perimeter filleted
    seq, left, right = outline_sequence(b["outline"])
    w0 = outline_wire(seq, left, right, yF)
    outer = cq.Solid.extrudeLinear(cq.Face.makeFromWires(w0), V(0, D, 0))
    r_req = float(b["edge_round"])
    r_rear_req = min(r_req, float(b.get("rear_round_max", r_req)))
    outer, r_rear = try_fillet(outer, end_edges(outer, yR), r_rear_req)
    outer, r_front = try_fillet(outer, end_edges(outer, yF), r_req)
    if r_front + 1e-6 < r_req or r_rear + 1e-6 < r_rear_req:
        info["warnings"].append(f"edge_round {r_req:g} reduced to front {r_front:.1f} / rear {r_rear:.1f} mm (OpenCascade fillet limit)")
    info.update(edge_round_front=round(r_front, 2), edge_round_rear=round(r_rear, 2))
    opoly = Polygon(wire_xz(w0))
    info["outline_poly"] = opoly

    # ---- cavity: inner offset outline, front skin thicker so engraving keeps >= 1 mm
    iseq, il, ir = inner_outline(opoly, wall)
    iw = outline_wire(iseq, il, ir, yF + skin)
    cav = cq.Solid.extrudeLinear(cq.Face.makeFromWires(iw), V(0, D - skin - wall, 0))
    cav, _ = try_fillet(cav, end_edges(cav, yR - wall), max(r_rear - wall, 0.6))
    cav, _ = try_fillet(cav, end_edges(cav, yF + skin), max(r_front - skin, 0.6))
    body = outer.cut(cav)

    # ---- internal bosses (clipped to the outer solid so nothing pokes out)
    bosses, holes = [], []
    floor = float(b["floor_z"])
    bp = params["backplane_mount"]
    for (x, y) in bp["holes_xy"]:
        bosses.append(cyl((x, y, 0.0), "z", bp["boss_d"] / 2, bp["bottom_z"]))
        holes.append(cyl((x, y, bp["bottom_z"] - bp["insert_depth"]), "z", bp["insert_hole_d"] / 2, bp["insert_depth"] + 0.2))
    for post in bp.get("posts", []):     # printed support posts through the board (backplane-v1 cartridge.support_posts)
        x, y = post["xy"]
        bosses.append(cyl((x, y, 0.0), "z", post["d"] / 2, post["top_z"]))
        holes.append(cyl((x, y, post["top_z"] - 6.0), "z", post["pilot_d"] / 2, 6.2))
    cs = params["cover_screws"]
    for s in cs["positions"]:
        x, z = s["xz"]
        bosses.append(cyl((x, cs["boss_y0"], z), "y", cs["boss_d"] / 2, b["split_y"] - cs["boss_y0"]))
        dx, dz = s["rib_dir"]
        L = s["rib_len"]
        nx, nz = -dz, dx
        hw = cs["rib_w"] / 2
        rib = [(x + nx * hw, z + nz * hw), (x + dx * L + nx * hw, z + dz * L + nz * hw),
               (x + dx * L - nx * hw, z + dz * L - nz * hw), (x - nx * hw, z - nz * hw)]
        bosses.append(prism_xz(rib, cs["boss_y0"], b["split_y"]))
        holes.append(cyl((x, b["split_y"] - cs["insert_depth"], z), "y", cs["insert_hole_d"] / 2, cs["insert_depth"] + 0.2))
    inner_add = compound(bosses).intersect(outer)
    body = body.fuse(inner_add).clean()

    # ---- front: decor engraving, grille, holes; base: feet recesses
    plate = next((d for d in params["decor"] if d["type"] == "face_plate"), None)

    def plate_depth_at(x, z):
        if plate and math.hypot(x - plate["at"][0], z - plate["at"][1]) <= plate["size"][0] / 2:
            return float(plate.get("depth", 1.2))
        return 0.0

    cuts = decor_cuts(params["decor"], yF, plate_depth_at)
    for f in params["apertures"]:
        cx, cz = f["at"]
        if f["kind"] == "round":
            cuts.append(cyl((cx, yF - 2, cz), "y", f["d"] / 2, skin + 4))
        elif f["kind"] == "rrect":
            cuts.append(prism_xz(rrect_poly(cx, cz, f["w"], f["h"], f.get("r", 1.0)), yF - 2, yF + skin + 2))
        elif f["kind"] == "grille":
            for hp in hex_grille(cx, cz, f["w"], f["h"], f["hole"], f["pitch"]):
                cuts.append(prism_xz(hp, yF - 2, yF + skin + 2))
            info["grille_holes"] = sum(1 for _ in hex_grille(cx, cz, f["w"], f["h"], f["hole"], f["pitch"]))
    ft = params["feet"]
    for (x, y) in ft["xy"]:
        cuts.append(cyl((x, y, -1.0), "z", ft["recess_d"] / 2, 1.0 + ft["recess_depth"]))
    body = body.cut(*cuts, *holes).clean()

    # ---- split: shell (front + sides) and rear cover
    ys = float(b["split_y"])
    shell = body.intersect(box(-BIG, BIG, -BIG, ys, -BIG, BIG))
    cover = body.intersect(box(-BIG, BIG, ys, BIG, -BIG, BIG))
    ccuts = []
    for s in cs["positions"]:
        x, z = s["xz"]
        ccuts.append(cyl((x, ys - 1, z), "y", cs["clear_d"] / 2, D))
        ccuts.append(cyl((x, yR - cs["counterbore_depth"], z), "y", cs["counterbore_d"] / 2, 10))
    u = params["usb_opening"]
    ccuts.append(prism_xz(rrect_poly(u["at"][0], u["at"][1], u["w"], u["h"], u["r"]), ys - 1, yR + 2))
    cover = cover.cut(*ccuts).clean()

    comps = {"PRINT_Shell": shell, "PRINT_RearCover": cover}
    for name, spec in params["parts"].items():
        shapes = [prim(p) for p in spec["prims"]]
        s = shapes[0] if len(shapes) == 1 else compound(shapes)
        if spec.get("fuse") and len(shapes) > 1:
            s = shapes[0].fuse(*shapes[1:]).clean()
        comps[name] = s
    info["cavity_volume_mm3"] = round(cav.Volume(), 1)
    return {"components": comps, "info": info}


def print_pose(name: str, shape: cq.Shape, params: dict) -> cq.Shape:
    """Rotate a printed part into its print orientation and put it on the bed at Z=0, centred in XY."""
    pp = params["print_pose"].get(name, {"rot_x": 0})
    s = shape.rotate(V(0, 0, 0), V(1, 0, 0), pp.get("rot_x", 0))
    bb = s.BoundingBox()
    return s.translate(V(-(bb.xmin + bb.xmax) / 2, -(bb.ymin + bb.ymax) / 2, -bb.zmin))


def write_stl(shape: cq.Shape, path: str, tol: float = 0.1, ang: float = 0.2) -> None:
    """Binary STL from an absolute-tolerance mesh (CadQuery's exporter defaults to a relative tolerance)."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.StlAPI import StlAPI_Writer

    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, ang, True)
    w = StlAPI_Writer()
    w.ASCIIMode = False
    w.Write(shape.wrapped, str(path))


def main(argv: list[str]) -> None:
    here = Path(__file__).resolve().parent
    pfile = Path(argv[1]) if len(argv) > 1 else here / "params.json"
    out = Path(argv[2]) if len(argv) > 2 else here / "_cad_out"
    out.mkdir(parents=True, exist_ok=True)
    params = json.loads(pfile.read_text())
    res = build(params)
    assy = cq.Assembly(name=params.get("slug", "gadget"))
    for name, shp in res["components"].items():
        col = params["colors"].get(name, "#CCCCCC").lstrip("#")
        rgb = tuple(int(col[i:i + 2], 16) / 255 for i in (0, 2, 4))
        assy.add(shp, name=name, color=cq.Color(*rgb))
        if name.startswith("PRINT_"):
            write_stl(print_pose(name, shp, params), str(out / f"{name}.stl"))
    assy.save(str(out / "assembly.step"))
    print(json.dumps({k: v for k, v in res["info"].items() if k != "outline_poly"}, indent=1))


if __name__ == "__main__":
    main(sys.argv)
