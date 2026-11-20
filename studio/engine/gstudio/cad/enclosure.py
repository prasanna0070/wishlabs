"""Shell split and enclosure hardware.

rear_cover  the body is cut by a vertical plane y = y_s: the front part is PRINT_Shell (open at the rear), the rear
            cap is PRINT_RearCover with a locating lip, screw tubes over M2.5 heat-set insert bosses on the shell
            wall (with webs to the wall), vents and the USB-C opening.
base_plate  the cavity is opened through the bottom: PRINT_Shell sits over PRINT_BasePlate, which carries the cartridge,
            feet seats and countersunk screws into insert bosses on the shell wall.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import cadquery as cq
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box as sbox
from shapely.ops import nearest_points, unary_union

from .geom import V, box_z, cyl_axis, cyl_z, largest_solid, poly_face, prism, rounded_rect_face_fast, try_fillet
from .internals import board_size, keepout_plan, usb_face_by
from .modules import pick_screw, screw_length
from .parts import Fastener, Part
from .probe import largest


@dataclass
class Split:
    kind: str
    y_s: Optional[float] = None
    shell_adds: list = field(default_factory=list)
    shell_cuts: list = field(default_factory=list)
    other_adds: list = field(default_factory=list)  # cover or plate
    other_cuts: list = field(default_factory=list)
    other_base: Optional[cq.Shape] = None  # base plate solid
    opening: Optional[Polygon] = None
    fasteners: list = field(default_factory=list)
    info: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    boss_points: list = field(default_factory=list)
    lip_root: list = field(default_factory=list)  # cover: ring that carries the lip segments (applied with a validity guard)


def _xz_to_face(poly: Polygon, y: float, length: float) -> Optional[cq.Shape]:
    """Prism of an (x, z) polygon from y to y + length (length may be negative)."""
    f = poly_face(poly, spacing=1.2)
    if f is None:
        return None
    pl = cq.Plane(origin=V(0, y, 0), xDir=V(1, 0, 0), normal=V(0, -1, 0))  # local x->X, y->Z, z->-Y
    s = prism(f, 0, abs(length)).moved(cq.Location(pl))
    return s.translate(V(0, abs(length), 0)) if length > 0 else s


def _xy_prism(poly: Polygon, z0: float, z1: float) -> Optional[cq.Shape]:
    f = poly_face(poly, spacing=1.2)
    return prism(f, z0, z1) if f is not None else None


def _box_hits(pt, boxes, pad=0.0) -> bool:
    x, y, z = pt
    for b in boxes:
        if b[0] - pad <= x <= b[1] + pad and b[2] - pad <= y <= b[3] + pad and b[4] - pad <= z <= b[5] + pad:
            return True
    return False


def _spread(cands: list, k: int, start_key=None) -> list:
    """Farthest-point sampling of k points from candidates (each a tuple whose first two items are 2D coords)."""
    if not cands:
        return []
    pts = np.array([c[:2] for c in cands], float)
    i0 = int(np.argmax(start_key(pts))) if start_key else 0
    chosen = [i0]
    d = np.linalg.norm(pts - pts[i0], axis=1)
    while len(chosen) < min(k, len(cands)):
        j = int(np.argmax(d))
        if d[j] < 1e-6:
            break
        chosen.append(j)
        d = np.minimum(d, np.linalg.norm(pts - pts[j], axis=1))
    return [cands[i] for i in chosen]


# ================================================================================================ cartridge placement
def place_cartridge(bp: dict, split: str, floor: float, probeC, y_s_max: Optional[float], hint: Optional[dict],
                    opening: Optional[Polygon] = None) -> tuple[float, float, list]:
    """Return (x0, y0, warnings): board lower-left in world, per backplane-v1 placement_rule."""
    W, H, T = board_size(bp)
    warns = []
    x0 = -W / 2
    zt = floor + 8.0
    by_face = usb_face_by(bp)
    usb_bx = 31.4
    for s in (bp.get("slots") or []):
        if isinstance(s, dict) and s.get("id") == "J_PWR" and s.get("courtyard"):
            usb_bx = (s["courtyard"][0] + s["courtyard"][1]) / 2
    o = np.array([[x0 + usb_bx, 0.0, zt + 1.6]])
    hit, _, _ = probeC.hits(o, [[0, 1.0, 0]])
    if not np.isfinite(hit[0, 1]):
        warns.append("could not find the inner rear wall behind the USB-C position")
        y_rear = 30.0
    else:
        y_rear = float(hit[0, 1])
    y0 = y_rear - 0.2 - by_face
    if hint and hint.get("board_origin") and any(abs(v) > 1e-6 for v in hint["board_origin"][:2]):
        hx, hy = float(hint["board_origin"][0]), float(hint["board_origin"][1])
        x0, y0 = hx, hy
    if split == "rear_cover" and y_s_max is not None:
        holes_y = max([h["at"][1] for h in (bp.get("board") or {}).get("holes", []) if str(h.get("id", "")).startswith("H")] or [H - 3.5])
        lim = y_s_max - 3.6 - 0.8 - holes_y
        if y0 > lim:
            y0 = lim
    kp, kh, cl = keepout_plan(bp)
    for step in range(0, 80):
        ok = _keepout_fits(probeC, x0, y0, floor, kp, kh, cl, opening)
        if ok:
            break
        y0 -= 1.0
    else:
        warns.append("the core cartridge keep-out does not fit inside the shell with clearance; check digital-checks")
    return x0, y0, warns


def _keepout_fits(probeC, x0, y0, floor, kp, kh, cl, opening) -> bool:
    xs = np.linspace(x0 + kp[0] - cl, x0 + kp[1] + cl, 9)
    ys = np.linspace(y0 + kp[2] - cl, y0 + kp[3] + cl - 0.3, 9)
    zs = [floor + 1.0, floor + kh * 0.5, floor + kh]
    pts = [(x, y, z) for z in zs for x in xs for y in (ys[0], ys[-1])] + [(x, y, z) for z in zs for y in ys for x in (xs[0], xs[-1])]
    inside = probeC.contains(np.array(pts))
    if not bool(np.all(inside)):
        return False
    if opening is not None:
        plan = sbox(x0 + kp[0], y0 + kp[2], x0 + kp[1], y0 + kp[3] - 0.3)
        return bool(opening.buffer(0.01).contains(plan))
    return True


# ================================================================================================ rear cover
LIP_GAP = 0.25  # mm between a cover piece that reaches into the shell (lip, screw tube) and the shell's inner wall


def _polys(g) -> list:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and not p.is_empty]


def tube_room(probeC, csec: Polygon, y_s: float, depth: float = 3.0) -> Optional[Polygon]:
    """(x, z) region a cover piece may fill between the boss tops (y_s - depth) and the seam: the cavity section at both
    ends, LIP_GAP inside the shell's inner wall. None when a section is missing (the caller keeps the plain tube)."""
    lo = largest(probeC.section("y", y_s - depth))
    if csec.is_empty or lo.is_empty:
        return None
    room = _polys(csec.buffer(-LIP_GAP).intersection(lo.buffer(-LIP_GAP)))
    return max(room, key=lambda p: p.area) if room else None


def _xz_slab(poly: Polygon, y0: float, y1: float) -> cq.Solid:
    """Exact prism of an (x, z) polygon's outer ring from y0 to y1 (> y0): planar facets, no spline fit."""
    w = cq.Wire.makePolygon([V(px, y0, pz) for px, pz in list(poly.exterior.coords)[:-1]], close=True)
    return cq.Solid.extrudeLinear(cq.Face.makeFromWires(w), V(0, y1 - y0, 0))


def cover_tube(O, x: float, z: float, y_ins: float, y_s: float, y_out: float, rb: float, room: Optional[Polygon],
               r_min: float) -> tuple[cq.Shape, bool]:
    """The cover's screw tube, from its joint face on the shell boss (y_ins) out through the cover. Returns (solid, clipped).

    The boss centre sits bg.wall + rb - 0.6 inside the outer surface (so the shell boss fuses with its wall), so on bodies
    without a flat rear rim (blob, revolve) the tube's circle reaches 0.6 mm into the shell wall. Behind the seam that is
    the cover's own wall; in front of it (y_ins..y_s) it is the shell's: 3-4 mm3 per screw of PRINT_Shell / PRINT_RearCover
    collision, which held the quote of every such design (live 2026-11-20, p_fzumtfr9a5dn). There the tube is clipped to
    `room` (the cavity, LIP_GAP inside the wall), as the lip is. Kept as the plain cylinder when it already fits, or when
    clipping would leave less than r_min around the screw axis."""
    full = cyl_axis((x, y_ins, z), (0, 1, 0), y_out - y_ins + 2, rb).intersect(
        O.intersect(cq.Solid.makeBox(2000, y_out - y_ins + 5, 2000, V(-1000, y_ins, -1000))))
    circ = Point(x, z).buffer(rb, quad_segs=24)
    if room is None or room.buffer(1e-6).contains(circ):
        return full, False
    keep = _polys(circ.intersection(room))
    if not keep or not max(keep, key=lambda p: p.area).contains(Point(x, z).buffer(r_min, quad_segs=24)):
        return full, False
    cut = [p for p in _polys(Point(x, z).buffer(rb + 0.5, quad_segs=24).difference(room)) if p.area > 1e-4]
    if not cut:
        return full, False
    try:  # 0.1 mm below the joint face so no cutter face coincides with it; flush with the seam above
        tube = full.cut(*[_xz_slab(p, y_ins - 0.1, y_s) for p in cut]).clean()
        if tube.isValid() and len(tube.Solids()) == 1:
            return tube, True
    except Exception:  # noqa: BLE001 - any OCC failure keeps the old tube (the collision check then reports it)
        pass
    return full, False


def seam_y(body: dict, bg, probeC, floor: float, bp: dict) -> float:
    bb = bg.outer.BoundingBox()
    if bg.family == "silhouette":
        return bb.ymax - bg.r_rear - 1.5
    W, H, _ = board_size(bp)
    need = sbox(-W / 2 - 3, floor + 0.5, W / 2 + 3, floor + 26)
    y = bb.ymax - bg.wall - 2.0
    while y > bb.ymin + 10:
        sec = largest(probeC.section("y", y))
        if not sec.is_empty and sec.contains(need):
            return y
        y -= 2.0
    return (bb.ymin + bb.ymax) / 2


def rear_cover(O, C, y_s: float, probeO, probeC, bg, floor, fz, keep_boxes, usb_pt, colors, n_screws=4) -> Split:
    sp = Split("rear_cover", y_s=y_s)
    ins, scr = fz["insert"], fz["screw"]
    rb = ins["boss_d"] / 2
    bb = O.BoundingBox()
    csec = largest(probeC.section("y", y_s))
    osec = largest(probeO.section("y", y_s))
    if csec.is_empty or osec.is_empty:
        sp.warnings.append("rear cover: empty section at the seam")
        return sp
    r_flat = (bg.r_rear + 2.8 + 0.5) if bg.family == "silhouette" else 0.0
    d_b = max(bg.wall + rb - 0.6, r_flat)
    ring = osec.buffer(-d_b)
    if ring.is_empty:
        ring = csec.buffer(-rb)
    ring = largest(ring)
    cands = []
    L = ring.exterior.length
    for s in np.arange(0, L, 2.0):
        p = ring.exterior.interpolate(s)
        x, z = p.x, p.y
        if z < floor + 14:
            continue
        ok = True
        for yy in (y_s - 3.0, y_s - 8.0, y_s - 13.0):
            if _box_hits((x, yy, z), keep_boxes, pad=rb + 1.0):
                ok = False
                break
        if ok and usb_pt is not None and math.hypot(x - usb_pt[0], z - usb_pt[2]) < 14:
            ok = False
        if ok:
            cands.append((x, z))
    pts = _spread(cands, n_screws, start_key=lambda a: a[:, 1])
    if len(pts) < 3:
        sp.warnings.append(f"rear cover: only {len(pts)} screw positions found clear of internals")
    room = tube_room(probeC, csec, y_s)
    n_clipped = 0
    lip_outer = csec.buffer(-0.25)
    lip_inner = csec.buffer(-0.25 - 1.6)
    lip = lip_outer.difference(lip_inner).difference(sbox(-1e3, -1e3, 1e3, floor + 12))
    relief = []
    for (x, z) in pts:
        q = nearest_points(csec.exterior, Point(x, z))[0]
        web = LineString([(x, z), (q.x, q.y)]).buffer(rb * 0.85, cap_style="flat")
        ext = LineString([(x, z), (q.x + (q.x - x) * 0.25, q.y + (q.y - z) * 0.25)]).buffer(rb * 0.85, cap_style="flat")
        prof = unary_union([Point(x, z).buffer(rb, quad_segs=24), web, ext]).intersection(osec.buffer(-0.8))
        relief.append(prof.buffer(1.0))
        boss = _xz_to_face(prof, y_s - 13.0, 10.0)
        if boss is not None:
            sp.shell_adds.append(boss)
        y_ins = y_s - 3.0
        # cover screw: head in a counterbore on the outer rear face
        hit, _, _ = probeO.hits([[x, bb.ymax + 50, z]], [[0, -1.0, 0]])
        y_out = float(hit[0, 1]) if np.isfinite(hit[0, 1]) else bb.ymax
        cb_depth = 2.0
        y_head = y_out - cb_depth
        grip = y_head - y_ins
        Ls, hole_depth = pick_screw(grip, scr["lengths"], ins["hole_depth"])
        sp.shell_cuts.append(cyl_axis((x, y_ins + 0.01, z), (0, -1, 0), hole_depth, ins["hole_d"] / 2))
        tube, clipped = cover_tube(O, x, z, y_ins, y_s, y_out, rb, room, scr["clear_d"] / 2 + 1.0)
        n_clipped += int(clipped)
        sp.other_adds.append(tube)
        sp.other_cuts.append(cyl_axis((x, y_ins - 1, z), (0, 1, 0), y_out - y_ins + 10, scr["clear_d"] / 2))
        sp.other_cuts.append(cyl_axis((x, y_head, z), (0, 1, 0), 30, scr["head_d"] / 2 + 0.35))
        sp.fasteners.append(Fastener("insert", "HeatSetInserts", np.array([x, y_ins, z]), np.array([0, -1.0, 0]), ins["length"], ins["od"], "cover insert"))
        sp.fasteners.append(Fastener("screw", "CoverScrews", np.array([x, y_head, z]), np.array([0, -1.0, 0]), Ls, scr["d"], "cover screw",
                                     hole_depth=hole_depth, joint=np.array([x, y_ins, z]), insert_len=ins["length"], clamp_t=grip))
        sp.boss_points.append([x, y_ins, z])
    lip = lip.difference(unary_union(relief)) if relief else lip
    if usb_pt is not None:
        lip = lip.difference(Point(usb_pt[0], usb_pt[2]).buffer(10))
    lip = lip.buffer(-0.7).buffer(0.7)  # no wedge tips thinner than 1.4 mm where the reliefs cut the ring (MIN-9)
    lip_parts = [g for g in getattr(lip, "geoms", [lip]) if g.area > 4.0]
    # The lip sits 0.25 mm inside the shell's inner wall, so on its own it never touches the cover's wall ring and was
    # dropped as floating fragments (REVIEW-1 MIN-10). A root ring just behind the seam, overlapping the cover wall by
    # 0.3 mm, carries every lip segment.
    # no face of the root may coincide with a lip face (OCC cannot fuse coincident spline faces): inner edge and the
    # relief ends sit 0.4 mm away from the lip's
    root = csec.buffer(0.3).difference(csec.buffer(-0.25 - 1.6 - 0.4)).difference(sbox(-1e3, -1e3, 1e3, floor + 12.4))
    root = root.difference(unary_union(relief).buffer(0.4)) if relief else root
    if usb_pt is not None:
        root = root.difference(Point(usb_pt[0], usb_pt[2]).buffer(10.4))
    root = root.buffer(-0.7).buffer(0.7)
    root_parts = [g for g in getattr(root, "geoms", [root]) if g.area > 4.0]
    for g in root_parts:  # built into the body before the split (cavity minus adds), so it lands on the cover side;
        # 1.43 mm along Y (the wall rule) and 0.05 mm short of where the cavity's rear round starts
        s = _xz_to_face(g, y_s + 0.02, 1.43)
        if s is not None:
            sp.shell_adds.append(s)
            sp.lip_root.append(s)
    for g in lip_parts:
        s = _xz_to_face(g, y_s - 3.0, 3.5)
        if s is not None:
            sp.other_adds.append(s)
    # vents in the upper back
    vent_region = osec.buffer(-(max(bg.r_rear, 4.0) + 5.0))
    if not vent_region.is_empty:
        vent_region = largest(vent_region)
        minx, minz, maxx, maxz = vent_region.bounds
        zc = minz + (maxz - minz) * 0.72
        slots = []
        for k in range(-2, 3):
            z = zc + k * 6.5
            line = vent_region.intersection(LineString([(minx - 5, z), (maxx + 5, z)]))
            if line.is_empty:
                continue
            x0, _, x1, _ = line.bounds
            cx = (x0 + x1) / 2
            ln = min(x1 - x0 - 10.0, 46.0)
            if ln < 10:
                continue
            sl = LineString([(cx - ln / 2, z), (cx + ln / 2, z)]).buffer(1.3, quad_segs=16)
            if any(math.hypot(p[0] - cx, p[2] - z) < ln / 2 + 6 and abs(p[2] - z) < 6 for p in sp.boss_points):
                continue
            slots.append(sl)
        if slots:
            vs = _xz_to_face(unary_union(slots), y_s + 0.5, bb.ymax - y_s + 5)
            if vs is not None:
                sp.other_cuts.append(vs)
        sp.info["vent_slots"] = len(slots)
    sp.info.update({"seam_y": round(y_s, 2), "cover_screws": len(pts), "lip_segments": len(lip_parts), "lip_root_segments": len(root_parts),
                    "lip_gap_mm": 0.25, "lip_thickness_mm": 1.6, "lip_depth_mm": 3.0})
    if n_clipped:
        sp.info["cover_tubes_clipped_to_cavity"] = n_clipped
    return sp


# ================================================================================================ base plate
def base_plate_opening(probeO, probeC, wall: float, t_bp: float):
    s0 = largest(probeO.section("z", 0.05))
    sc = largest(probeC.section("z", t_bp + 0.8))
    if s0.is_empty:
        return None
    op = s0.buffer(-wall)
    if not sc.is_empty:
        op = op.intersection(sc)
    op = largest(op.buffer(-0.01).buffer(0.01)) if not op.is_empty else op
    return op if not op.is_empty else None


def base_plate(O, C, opening: Polygon, t_bp: float, probeC, fz, keep_plan_boxes, colors, n_screws=4, keep_boxes3d=()) -> Split:
    sp = Split("base_plate", opening=opening)
    ins, scr = fz["insert"], fz["screw"]
    rb = ins["boss_d"] / 2
    plate_poly = opening.buffer(-0.3)
    plate = _xy_prism(plate_poly, 0.0, t_bp)
    cands = []
    ring = largest(opening.buffer(-(rb + 0.6)))
    sec_hi = largest(probeC.section("z", t_bp + 6.0))
    z_lo, z_hi = t_bp, t_bp + 2.2 + 10.0 + 1.0
    for s in np.arange(0, ring.exterior.length, 2.0):
        p = ring.exterior.interpolate(s)
        if any(b[0] - rb - 1 <= p.x <= b[1] + rb + 1 and b[2] - rb - 1 <= p.y <= b[3] + rb + 1 for b in keep_plan_boxes):
            continue
        if any(b[0] - rb - 1 <= p.x <= b[1] + rb + 1 and b[2] - rb - 1 <= p.y <= b[3] + rb + 1 and b[4] < z_hi and b[5] > z_lo
               for b in keep_boxes3d):
            continue
        cands.append((p.x, p.y))
    pts = _spread(cands, n_screws, start_key=lambda a: -a[:, 1] - np.abs(a[:, 0]) * 0.2)
    if len(pts) < 3:
        sp.warnings.append(f"base plate: only {len(pts)} screw positions found clear of the cartridge")
    cb_d, cb_depth = scr["head_d"] + 0.7, 1.9
    pad_h = 2.2
    zj = t_bp + pad_h  # joint face: top of the plate pad = bottom of the shell boss
    for (x, y) in pts:
        tgt = sec_hi if not sec_hi.is_empty else opening
        q = nearest_points(tgt.exterior, Point(x, y))[0]
        web = LineString([(x, y), (q.x + (q.x - x) * 0.3, q.y + (q.y - y) * 0.3)]).buffer(rb * 0.85, cap_style="flat")
        prof = unary_union([Point(x, y).buffer(rb, quad_segs=24), web])
        boss = _xy_prism(prof, zj, zj + 10.0)
        if boss is not None:
            sp.shell_adds.append(boss)
        sp.other_adds.append(cyl_z(x, y, t_bp - 0.01, zj, rb + 1.0))
        grip = zj - cb_depth
        Ls, hole_depth = pick_screw(grip, scr["lengths"], ins["hole_depth"])
        sp.shell_cuts.append(cyl_z(x, y, zj - 0.01, zj + hole_depth, ins["hole_d"] / 2))
        sp.other_cuts.append(cyl_z(x, y, -1, zj + 1, scr["clear_d"] / 2))
        sp.other_cuts.append(cyl_z(x, y, -1, cb_depth, cb_d / 2))
        sp.fasteners.append(Fastener("insert", "HeatSetInserts", np.array([x, y, zj]), np.array([0, 0, 1.0]), ins["length"], ins["od"], "plate insert"))
        sp.fasteners.append(Fastener("screw", "PlateScrews", np.array([x, y, cb_depth]), np.array([0, 0, 1.0]), Ls, scr["d"], "base plate screw",
                                     hole_depth=hole_depth, joint=np.array([x, y, zj]), insert_len=ins["length"], clamp_t=grip))
        sp.boss_points.append([x, y, zj])
    sp.other_base = plate
    sp.info.update({"plate_thickness": t_bp, "plate_screws": len(pts), "opening_area_mm2": round(opening.area, 1)})
    return sp


# ================================================================================================ feet, ties, usb
def feet_points(region: Polygon, k: int, seat_r: float) -> list:
    if k <= 0 or region is None or region.is_empty:
        return []
    r = largest(region.buffer(-(seat_r + 2.0)))
    if r.is_empty:
        return []
    minx, miny, maxx, maxy = r.bounds
    pts = []
    ring = [r.exterior.interpolate(s) for s in np.arange(0, r.exterior.length, 1.5)]
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    if k == 3:
        corners = [(minx, miny), (maxx, miny), ((minx + maxx) / 2, maxy)]
    if k == 6:
        corners += [(minx, (miny + maxy) / 2), (maxx, (miny + maxy) / 2)]
    for cx, cy in corners[:k]:
        best = min(ring, key=lambda p: (p.x - cx) ** 2 + (p.y - cy) ** 2)
        pts.append((best.x, best.y))
    return pts


def feet_solids(pts, foot, z_seat_top: float):
    sols = []
    for x, y in pts:
        f = cyl_z(x, y, z_seat_top - foot["h"], z_seat_top, foot["d"] / 2)
        f, _ = try_fillet(f, [e for e in f.Edges() if abs(e.Center().z - (z_seat_top - foot["h"])) < 1e-3], min(foot["h"] * 0.8, foot["d"] / 2 - 0.3), min_r=0.3)
        sols.append(f)
    return sols


def tie_anchor(x, y, floor, fz) -> tuple[cq.Shape, cq.Shape]:
    t = fz.get("tie", {"slot_w": 3.6, "slot_h": 1.8})
    w, d, h = t["slot_w"] + 5.0, 5.0, t["slot_h"] + 1.0 + 2.2
    block = box_z(x, y, floor - 0.5, floor + h, w, d)
    block, _ = try_fillet(block, [e for e in block.Edges() if e.Center().z > floor + h - 1e-3], 1.0, min_r=0.3)
    tunnel = box_z(x, y, floor + 1.0, floor + 1.0 + t["slot_h"], t["slot_w"], d + 2)
    return block, tunnel


def usb_tunnel(pt, bp: dict, y_end: float) -> cq.Shape:
    op = (bp.get("cartridge") or {}).get("usb_c_opening") or {"w": 13.0, "h": 7.0, "corner_r": 2.5}
    x, y, z = pt
    f = rounded_rect_face_fast(op["w"], op["h"], op.get("corner_r", 2.5))
    pl = cq.Plane(origin=V(x, y - 0.3, z), xDir=V(1, 0, 0), normal=V(0, 1, 0))
    return prism(f, 0, y_end - (y - 0.3) + 20).moved(cq.Location(pl))


def cord_tie_bridge(usb_pt, bp: dict, probeO, probeC, fz: dict, y_min: Optional[float] = None) -> Optional[dict]:
    """External USB-C cord strain relief (REVIEW-1 MIN-3): two tie slots through the rear wall above the USB-C opening,
    joined by a channel inside a thickened inner pad. A cable tie goes out of one slot, round the cord where it leaves the
    opening and back in the other, so a pull on the cord is taken by the wall bridge (and the part's screws), not by the
    receptacle. Returns {adds, cuts, info} in world coordinates, or None when the wall there cannot carry it."""
    op = (bp.get("cartridge") or {}).get("usb_c_opening") or {"w": 13.0, "h": 7.0}
    t = fz.get("tie", {"slot_w": 3.6, "slot_h": 1.8, "w": 2.5, "t": 1.0})
    x, y, z = (float(v) for v in usb_pt)
    sw, sh = float(t.get("slot_w", 3.6)), float(t.get("slot_h", 1.8))
    z_slot = z + op["h"] / 2 + 1.6 + sw / 2
    dx = op["w"] / 2 + 2.4
    ys_in, ys_out = [], []
    for xx in (x - dx, x, x + dx):
        h, _, _ = probeC.hits([[xx, y - 5.0, z_slot]], [[0, 1.0, 0]])
        o, _, _ = probeO.hits([[xx, y + 400.0, z_slot]], [[0, -1.0, 0]])
        if not (np.isfinite(h[0, 1]) and np.isfinite(o[0, 1])):
            return None
        ys_in.append(float(h[0, 1]))
        ys_out.append(float(o[0, 1]))
    y_in, y_out = min(ys_in), max(ys_out)
    ch_d = sh  # channel depth (tie thickness 1.0 + room)
    pad_t = ch_d + 1.8
    y0 = y_in - pad_t
    if y_min is not None and y0 < y_min:
        return None
    pad = cq.Solid.makeBox(2 * dx + sh + 6.0, y_in - y0 + 0.6, sw + 3.2, V(x - dx - sh / 2 - 3.0, y0, z_slot - sw / 2 - 1.6))
    chan = cq.Solid.makeBox(2 * dx + sh, ch_d, sw, V(x - dx - sh / 2, y_in - ch_d, z_slot - sw / 2))
    cuts = [chan]
    for sx in (-1, 1):
        cuts.append(cq.Solid.makeBox(sh, y_out - (y_in - ch_d) + 2.0, sw, V(x + sx * dx - sh / 2, y_in - ch_d, z_slot - sw / 2)))
    info = {"feature": "cord tie bridge: 2 tie slots above the USB-C opening joined by a channel in an inner pad",
            "slot_mm": [sh, sw], "slot_centres": [[round(x - dx, 2), round(z_slot, 2)], [round(x + dx, 2), round(z_slot, 2)]],
            "distance_from_opening_mm": round(z_slot - sw / 2 - (z + op["h"] / 2), 2), "pad_thickness_mm": round(pad_t, 2),
            "tie": "one nylon cable tie (STRAIN_TIE_NYLON66_100X2_5) from the tie kit",
            "path": "cord -> cable tie -> wall bridge between the slots -> part screws -> shell",
            "physical_check": "pull the cord along its axis: the plug must release before the receptacle or the board moves"}
    return {"adds": [pad], "cuts": cuts, "info": info}

