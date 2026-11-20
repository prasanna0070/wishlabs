"""params -> Model: the whole parametric geometry pipeline (no file output here).

Order: body -> ears -> decor relief -> (draft) -> feature kits -> cartridge placement -> split (cover / base plate)
-> feet, tie anchors, USB-C tunnel -> shell booleans -> components -> cable routes -> lift onto feet.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cadquery as cq
import numpy as np
from shapely.geometry import Point, box as sbox
from shapely.ops import unary_union

from .body import build_body
from .decor import MIN_SKIN, apply_ears, apply_relief, decor_stack
from .enclosure import (base_plate, base_plate_opening, cord_tie_bridge, feet_points, feet_solids, place_cartridge, rear_cover, seam_y,
                        tie_anchor, usb_tunnel)
from .features import build_kit
from .geom import V, Frame, bbox, cyl_axis, halfspace_y, largest_solid, poly_face, prism, split_fragments, unit
from .internals import build_cartridge
from .parts import Fastener, Part
from .probe import Probe, largest

HW_MODULE = {"HeatSetInserts": "INSERT_HEATSET_M2_5X4_MEC8005", "CartridgeScrews": "SCREW_SET_M2_M2_5_SHCS_A2",
             "CoverScrews": "SCREW_SET_M2_M2_5_SHCS_A2", "PlateScrews": "SCREW_SET_M2_M2_5_SHCS_A2",
             "ModuleScrews": "SCREW_SET_M2_M2_5_SHCS_A2", "Feet": "FEET_RUBBER_8X2_5_SUNROM_4436"}


@dataclass
class Model:
    params: dict
    parts: list = field(default_factory=list)
    fasteners: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)
    cables: list = field(default_factory=list)
    face_colors: dict = field(default_factory=dict)  # part name -> {face index: rgb}
    coupons: dict = field(default_factory=dict)  # TEST_ name -> shape (print only)
    lift: float = 0.0
    info: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    kits: list = field(default_factory=list)
    shapes: dict = field(default_factory=dict)  # internal: outer, cavity...

    def part(self, name: str) -> Optional[Part]:
        return next((p for p in self.parts if p.name == name), None)


class Timer:
    def __init__(self):
        self.t0 = time.time()
        self.last = self.t0
        self.marks: dict[str, float] = {}

    def mark(self, k: str):
        now = time.time()
        self.marks[k] = round(now - self.last, 2)
        self.last = now


def default_split(fam: str) -> str:
    return "rear_cover" if fam == "silhouette" else "base_plate"


def build_shell_preview(params: dict) -> dict:
    """Fast path for previews and the live draft: body + ears + decor (no internals)."""
    body = params["body"]
    fam = body["family"]
    wall = float(body.get("wall", 2.4))
    stack = decor_stack(params["decor"])
    # 0.3 mm over the 1.2 mm rule under the deepest relief, so tessellated wall sampling never reads it as thin (MIN-9)
    skin = max(wall, stack + MIN_SKIN + 0.3) if fam == "silhouette" else wall
    t = time.time()
    bg = build_body(body, skin)
    t_body = time.time() - t
    pr0 = Probe(bg.outer, tol=0.25)
    bb = bg.outer.BoundingBox()
    decor = [{**d, "_idx": i} for i, d in enumerate(params["decor"])]
    t = time.time()
    er = apply_ears(bg.outer, decor, pr0, bb.ylen)
    t_ears = time.time() - t
    t = time.time()
    flat = None
    if fam == "silhouette":  # the flat front face: the outline inset by the front edge round actually built (MAJ-31)
        from .body import face_polygon, outline_face

        try:
            flat = face_polygon(outline_face(body["outline"])).buffer(-(bg.r_front + FLAT_MARGIN))
            flat = None if flat.is_empty else flat
        except Exception:
            flat = None
    dr = apply_relief(er.outer, decor, pr0, cavity_pads=(fam != "silhouette"), wall=wall, flat_region=flat)
    t_relief = time.time() - t
    regions = []
    for r in dr.records:
        b = r.tool.BoundingBox()
        idx = list(getattr(r, "idx", []))
        regions.append({"name": f"PRINT_Shell#decor[{idx[0]}]" if idx else None, "spec_path": f"decor[{idx[0]}]" if idx else None,
                        "spec_paths": [f"decor[{i}]" for i in idx], "type": r.type, "label": DECOR_LABEL.get(r.type, r.type),
                        "style": r.label, "relief": r.relief, "depth": r.depth,
                        "bbox": [round(v, 2) for v in (b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax)]})
    for e in er.ears:
        regions.append({"name": f"PRINT_Shell#decor[{e['idx']}]", "spec_path": f"decor[{e['idx']}]", "spec_paths": [f"decor[{e['idx']}]"],
                        "type": "ear", "label": "ears", "style": e["style"], "bbox": e["bbox"]})
    return {"bg": bg, "outer": dr.outer, "records": dr.records, "pads": dr.pads, "regions": regions,
            "warnings": bg.warnings + er.warnings + dr.warnings, "ears": er.ears, "adjustments": dr.adjustments,
            "silhouette": getattr(er, "silhouette", None), "side_top_z": getattr(er, "side_top_z", None), "flat_region": flat,
            "skin_front": skin, "ear_solids": getattr(er, "solids", []),
            "timings": {"body": round(t_body, 2), "ears": round(t_ears, 2), "relief": round(t_relief, 2)}}


FLAT_MARGIN = 2.0  # decor stays this far inside the flat front face (REVIEW-1 MAJ-31)
DECOR_LABEL = {"face_plate": "face plate", "eye": "eyes", "mouth": "mouth", "blush": "cheeks", "text": "name text", "ear": "ears"}


def classify_faces(shape: cq.Shape, records: list, colors: dict) -> dict:
    """Map face index -> rgb for faces that belong to coloured decor (paint-fill / multi-material suggestion)."""
    out = {}
    tools = []
    for r in records:
        if not r.color or r.color == "body":
            continue
        col = colors.get(r.color) or colors.get("detail")
        b = r.tool.BoundingBox()
        tools.append((r.tool, col, (b.xmin - 0.2, b.xmax + 0.2, b.ymin - 0.2, b.ymax + 0.2, b.zmin - 0.2, b.zmax + 0.2)))
    if not tools:
        return out
    for i, f in enumerate(shape.Faces()):
        fb = f.BoundingBox()
        for tool, col, tb in tools:
            if fb.xmin < tb[0] or fb.xmax > tb[1] or fb.ymin < tb[2] or fb.ymax > tb[3] or fb.zmin < tb[4] or fb.zmax > tb[5]:
                continue
            try:
                # a point ON the face (the centroid of a curved stroke face lies off the face)
                from .geom import tessellate

                v, _, t, _ = tessellate(f, 0.2, 0.6)
                p = V(v[t[0]].mean(0)) if len(t) else f.Center()
                try:  # snap onto the face's surface (a chord centroid sits off curved faces)
                    from OCP.BRep import BRep_Tool
                    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
                    from OCP.gp import gp_Pnt

                    pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(p.x, p.y, p.z), BRep_Tool.Surface_s(f.wrapped))
                    if pr.NbPoints():
                        q = pr.NearestPoint()
                        p = V(q.X(), q.Y(), q.Z())
                except Exception:
                    pass
                sols = tool.Solids() or [tool]
                if any(sol.isInside(p, 0.08) for sol in sols):
                    out[i] = tuple(col)
                    break
            except Exception:
                continue
    return out


FIN_T = 1.6  # ceiling fin thickness (>= the 1.6 mm print rule margin over MIN_WALL 1.2)
FIN_PITCH = FIN_T + 5.8  # centre to centre: the ceiling between two fins is a 5.8 mm bridge


def ceiling_fins(region, y0: float, y1: float, keep_boxes: list, probeO, margin: float = 1.0, probeC=None) -> tuple[list, dict]:
    """Thin vertical fins (XZ plates FIN_T thick every FIN_PITCH along Y) filling the cavity section above the 45 deg
    roof line (body.roof_clip), so the head ceiling prints without support: each fin's lower edge rises at 45 deg and
    the ceiling between two fins is a short bridge (REVIEW-1 MAJ-9). Everything is clipped in 2D (no spline booleans):
    to the outer body section at both faces of the fin (0.6 mm inside) and away from every kit keep-out box."""
    from shapely.geometry import MultiPolygon, box as _sb

    from .probe import largest as _lg

    if (region is None and probeC is None) or y1 - y0 < FIN_T + 1.0:
        return [], {}
    from .body import roof_clip

    n = int((y1 - y0 - FIN_T) // FIN_PITCH) + 1
    ys = [y0 + FIN_T / 2 + i * FIN_PITCH for i in range(n)]
    if ys and y1 - (ys[-1] + FIN_T / 2) > 1.0:
        ys.append(y1 - FIN_T / 2)
    fins, ncut, area_total = [], 0, 0.0
    if region is None:  # revolve / blob: skip when the ceiling flatter than 45 deg is already small (2D estimate)
        est = 0.0
        for yc in ys:
            I = _lg(probeC.section("y", yc))
            if I.is_empty:
                continue
            try:
                R = I.difference(roof_clip(I))
            except Exception:
                continue
            if R.is_empty:
                continue
            Rb = R.buffer(0.2)
            ring = np.asarray(I.exterior.coords)
            for p0, p1 in zip(ring[:-1], ring[1:]):  # ceiling segments: down-facing and flatter than 45 deg
                dx, dz = p1[0] - p0[0], p1[1] - p0[1]
                L = math.hypot(dx, dz)
                if L < 1e-6 or abs(dz) > abs(dx):
                    continue
                mid = Point((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
                if not Rb.contains(mid) or not I.contains(Point(mid.x, mid.y - 0.3)):
                    continue
                est += L * FIN_PITCH
        if est < 400.0:
            return [], {"fins": 0, "skipped": f"ceiling flatter than 45 deg is about {est:.0f} mm2 (< 400): no fins needed",
                        "estimate_mm2": round(est, 1)}
    for yc in ys:
        g = region
        if region is None:  # revolve / blob: the roof region of the cavity section at this fin's own Y
            I = _lg(probeC.section("y", yc))
            if I.is_empty:
                continue
            try:
                rc = roof_clip(I)
                R = I.difference(rc).buffer(-0.05).buffer(0.05)
            except Exception:
                continue
            if R.is_empty or R.area < 5.0:
                continue
            area_total = max(area_total, R.area)
            g = R.buffer(1.2).difference(rc)
        for yy in (yc - FIN_T / 2 - 0.05, yc + FIN_T / 2 + 0.05):
            sec = _lg(probeO.section("y", yy))
            if sec.is_empty:
                g = None
                break
            g = g.intersection(sec.buffer(-0.6))
        if g is None or g.is_empty:
            continue
        holes = [_sb(b[0] - margin, b[4] - margin, b[1] + margin, b[5] + margin) for b in keep_boxes
                 if b[2] - margin < yc + FIN_T / 2 and b[3] + margin > yc - FIN_T / 2]
        if holes:
            from shapely.ops import unary_union as _uu

            g2 = g.difference(_uu(holes))
            if g2.area < g.area - 1e-6:
                ncut += 1
            g = g2
        polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
        for q in polys:
            q = q.buffer(-0.05).buffer(0.05)
            if q.is_empty or q.area < 4.0:
                continue
            for qq in (list(q.geoms) if isinstance(q, MultiPolygon) else [q]):
                f = poly_face(qq, spacing=1.5, smooth=False)
                if f is None:
                    continue
                pl = cq.Plane(origin=V(0, yc - FIN_T / 2, 0), xDir=V(1, 0, 0), normal=V(0, -1, 0))
                fins.append(prism(f, 0, FIN_T).moved(cq.Location(pl)).translate(V(0, FIN_T, 0)))
    info = {"fins": len(ys), "pitch_mm": FIN_PITCH, "thickness_mm": FIN_T, "bridge_mm": round(FIN_PITCH - FIN_T, 2),
            "y_range": [round(y0, 2), round(y1, 2)], "region_mm2": round(region.area if region is not None else area_total, 1),
            "fins_cut_around_kits": ncut, "pieces": len(fins), "per_section": region is None}
    return fins, info


def classify_regions(shape: cq.Shape, regions: list) -> dict:
    """face index -> region name for faces of `shape` lying in/on one of the region solids [(name, solid)]."""
    from .geom import tessellate

    out = {}
    boxes = []
    for name, sol in regions:
        if sol is None:
            continue
        b = sol.BoundingBox()
        boxes.append((name, sol, (b.xmin - 0.3, b.xmax + 0.3, b.ymin - 0.3, b.ymax + 0.3, b.zmin - 0.3, b.zmax + 0.3)))
    if not boxes:
        return out
    # smallest region first: cheeks embossed on a face plate belong to the cheeks, not to the plate
    boxes.sort(key=lambda t: (t[2][1] - t[2][0]) * (t[2][3] - t[2][2]) * (t[2][5] - t[2][4]))
    for i, f in enumerate(shape.Faces()):
        fb = f.BoundingBox()
        cands = [(n, s_) for n, s_, tb in boxes if not (fb.xmin < tb[0] or fb.xmax > tb[1] or fb.ymin < tb[2] or fb.ymax > tb[3]
                                                       or fb.zmin < tb[4] or fb.zmax > tb[5])]
        if not cands:
            continue
        try:
            v, _, t, _ = tessellate(f, 0.2, 0.6)
            p = V(v[t[0]].mean(0)) if len(t) else f.Center()
        except Exception:
            p = f.Center()
        for n, s_ in cands:
            try:
                sols = s_.Solids() or [s_]
                if any(so.isInside(p, 0.1) for so in sols):
                    out[i] = n
                    break
            except Exception:
                continue
    return out


def fastener_parts(fasteners: list, fz: dict) -> list:
    groups: dict[str, list] = {}
    for f in fasteners:
        groups.setdefault(f.group, []).append(f)
    out = []
    head_d, head_h = fz["screw"]["head_d"], fz["screw"]["head_h"]
    for g, items in groups.items():
        col = (0.72, 0.62, 0.30) if g == "HeatSetInserts" else (0.12, 0.12, 0.13)
        label = {"HeatSetInserts": f"M2.5 heat-set inserts x{len(items)}", "CartridgeScrews": f"M2.5 cartridge screws x{len(items)}",
                 "CoverScrews": f"M2.5 cover screws x{len(items)}", "PlateScrews": f"M2.5 base plate screws x{len(items)}",
                 "ModuleScrews": f"M2.5 module clamp screws x{len(items)}"}.get(g, g)
        p = Part(f"PURCHASE_{g}", "PURCHASE", label, col, module_id=(fz.get("hw_modules") or {}).get(g) or HW_MODULE.get(g), mass_g=round(len(items) * (0.35 if g == "HeatSetInserts" else 0.3), 2),
                 role="hardware")
        sols = []
        for f in items:
            a = unit(f.axis)
            if f.kind == "insert":
                s = cyl_axis(f.head, a, f.length, f.d / 2).cut(cyl_axis(f.head - a, a, f.length + 2, 1.3))
            else:
                shank = cyl_axis(f.head, a, f.length, f.d / 2)
                head = cyl_axis(f.head - a * head_h, a, head_h, head_d / 2).cut(cyl_axis(f.head - a * (head_h + 0.1), a, 1.3, 1.0))
                s = shank.fuse(head).clean()
            sols.append(s)
        p.add(g, cq.Compound.makeCompound(sols), col)
        p.notes.append("threads simplified; insert interference with its boss is by design")
        sizes: dict[str, int] = {}
        for f in items:
            key = f"M2.5x{f.length:g}" if f.kind == "screw" else f"M2.5x{f.length:g} insert (OD {f.d:g})"
            sizes[key] = sizes.get(key, 0) + 1
        p.extra = {"qty": len(items), "size": next(iter(sizes)) if len(sizes) == 1 else ", ".join(f"{k} x{v}" for k, v in sizes.items()),
                   "items": [{"size": k, "qty": v} for k, v in sizes.items()],
                   "uses": sorted({f.label for f in items})}
        out.append(p)
    return out


def build(params: dict, progress: Optional[Callable] = None, on_draft: Optional[Callable] = None, route_cables: bool = True) -> Model:
    T = Timer()
    say = progress or (lambda msg, pct=None: None)
    M = Model(params=params)
    body = params["body"]
    fam = body["family"]
    split = body.get("split") or default_split(fam)
    wall = float(body.get("wall", 2.4))
    fz = params["fasteners"]
    colors = {k: tuple(v) if isinstance(v, (list, tuple)) else v for k, v in params["colors"].items()}
    bp = params["backplane"]
    # ---------------------------------------------------------------- body + decor
    say("shaping the body", 5)
    pv = build_shell_preview(params)
    bg, O = pv["bg"], pv["outer"]
    M.warnings += pv["warnings"]
    C = bg.cavity
    if pv["pads"]:
        C = largest_solid(C.cut(*pv["pads"]).clean())
    T.mark("body_decor")
    T.marks.update({f"body_decor.{k}": v for k, v in pv.get("timings", {}).items()})
    if on_draft:
        try:
            on_draft(O, pv["records"], colors)
        except Exception as e:  # pragma: no cover
            M.warnings.append(f"draft export failed: {e}")
    T.mark("draft")
    probeO = Probe(O)
    probeC = Probe(C)
    bb = O.BoundingBox()
    T.mark("probes")
    # ---------------------------------------------------------------- features
    say("cutting apertures and mount kits", 25)
    kits = []
    skin = bg.skin_front if fam == "silhouette" else wall
    for f in params["features"]:
        k = build_kit(f, f["desc"], f["ref"], probeO, probeC, O, fz, colors, f["spec_path"], others=params["features"], skin=skin)
        kits.append(k)
        M.warnings += k.warnings
    M.kits = kits
    T.mark("features")
    # ---------------------------------------------------------------- cartridge + split
    say("placing the core cartridge", 40)
    t_bp = 3.5
    opening = None
    if split == "base_plate":
        opening = base_plate_opening(probeO, probeC, wall, t_bp)
        if opening is None:
            M.warnings.append("base plate: no bottom opening found; falling back to a rear cover")
            split = "rear_cover"
    floor = t_bp if split == "base_plate" else bg.floor_z
    y_s = seam_y(body, bg, probeC, floor, bp) if split == "rear_cover" else None
    x0, y0, w = place_cartridge(bp, split, floor, probeC, y_s, params.get("cartridge_hint"), opening)
    M.warnings += w
    refs = {"backplane": params.get("backplane_ref", "BP1"), "core": params["core"]["ref"]}
    cart = build_cartridge(bp, x0, y0, floor, fz, colors, refs, params["core"]["desc"], params.get("internal", []), {})
    usb_pt = np.array(cart.anchors.get(f"{refs['backplane']}.J_PWR", [x0 + 31.4, y0 + 78.9, cart.zt + 1.6]))
    keep_boxes = [bbox(s) for k in kits for s in k.keepouts] + [list(cart.keepout)]
    T.mark("cartridge")
    say("splitting the shell", 50)
    if split == "rear_cover":
        sp = rear_cover(O, C, y_s, probeO, probeC, bg, floor, fz, keep_boxes, usb_pt, colors)
    else:
        plan_boxes = [(b[0], b[1], b[2], b[3]) for b in keep_boxes if b[4] < floor + 12]
        sp = base_plate(O, C, opening, t_bp, probeC, fz, plan_boxes, colors, keep_boxes3d=keep_boxes[:-1])
    M.warnings += sp.warnings
    # ---------------------------------------------------------------- feet + ties + usb
    foot = fz["foot"]
    nfeet = int(body.get("feet", 4))
    if split == "rear_cover":
        reg = largest(probeO.section("z", 0.05)).intersection(sbox(-1e3, -1e3, 1e3, (y_s or 1e3) - 4.0))
    else:
        reg = sp.opening.buffer(-0.3)
        holes = unary_union([Point(p[0], p[1]).buffer(5.5) for p in sp.boss_points]) if sp.boss_points else None
        if holes is not None:
            reg = reg.difference(holes)
    fpts = feet_points(reg, nfeet, foot["seat_d"] / 2)
    seats = [cq.Solid.makeCylinder(foot["seat_d"] / 2, foot["seat_depth"] + 1, V(x, y, -1.0)) for x, y in fpts]
    feet = feet_solids(fpts, foot, foot["seat_depth"])
    ties, tunnels = [], []
    cav_floor = largest(probeC.section("z", floor + 2.0)) if split == "rear_cover" else sp.opening.buffer(-1.0)
    tie_cands = [(x0 - 7.0, y0 + 12.0), (x0 + cart.w + 7.0, y0 + 12.0), (x0 + 10.0, y0 - 7.0), (x0 + cart.w - 10.0, y0 - 7.0)]
    for (tx, ty) in tie_cands:
        if len(ties) >= 2:
            break
        if cav_floor.is_empty or not cav_floor.buffer(-4.0).contains(sbox(tx - 4.5, ty - 2.5, tx + 4.5, ty + 2.5)):
            continue
        if any(b[0] - 1 <= tx <= b[1] + 1 and b[2] - 1 <= ty <= b[3] + 1 and b[4] < floor + 8 for b in keep_boxes[:-1]):
            continue
        if split == "rear_cover" and ty > (y_s or 1e3) - 8:
            continue
        blk, tun = tie_anchor(tx, ty, floor, fz)
        ties.append(blk)
        tunnels.append(tun)
    tunnel = usb_tunnel(usb_pt, bp, bb.ymax)
    cord = None
    try:
        cord = cord_tie_bridge(usb_pt, bp, probeO, probeC, fz, y_min=(y_s + 0.5) if split == "rear_cover" else None)
    except Exception as e:  # pragma: no cover
        M.warnings.append(f"cord tie bridge skipped: {e}")
    if cord is None:
        M.warnings.append("USB-C cord: no room for the cord tie bridge above the opening; the cord has no strain relief")
    else:
        cord["info"]["part"] = "PRINT_RearCover" if split == "rear_cover" else "PRINT_Shell"
    T.mark("split_prep")
    # ---------------------------------------------------------------- shell booleans
    say("building the shell solids", 60)
    kit_adds = [a for k in kits for a in k.adds]
    kit_cuts = [c for k in kits for c in k.cuts]
    C_open = C
    if split == "base_plate":
        from .enclosure import _xy_prism

        pr = _xy_prism(sp.opening, -1.0, t_bp + 0.8)
        C_open = C.fuse(pr).clean()
    floor_adds = cart.adds + ties
    floor_cuts = cart.cuts + tunnels
    fin_adds, fin_info = [], {}
    roof_on = bool(body.get("self_supporting_roof", True))
    if roof_on and (getattr(bg, "roof_region", None) is not None or fam != "silhouette"):
        ycav0 = bb.ymin + bg.skin_front + 2.0  # clear of the front skin plane; the gap to the wall stays a short bridge
        ycav1 = (y_s - 3.6) if split == "rear_cover" and y_s is not None else (bb.ymax - wall - 2.0)  # clear of the cover lip
        fin_keep = list(keep_boxes)
        plate_fz = []
        if split == "base_plate":
            # The base plate is its own part, and the fins come out of the cavity, which the plate opening extends down
            # to the cavity floor: they reached through the plate, its screw pads, tie anchors and screw heads
            # (939 mm3 PRINT_Shell / PRINT_BasePlate on p_wbgxdewi897y v2). They now stop above the plate and clear of
            # everything standing on it (each keep box gets the fins' 1 mm margin).
            fin_keep.append([-1e3, 1e3, -1e3, 1e3, -1e3, t_bp])
            fin_keep += [bbox(s) for s in floor_adds + sp.other_adds]
            plate_fz = list(sp.fasteners)
        hd = fz["screw"]["head_d"] / 2 + 0.6
        for f_ in [f for k in kits for f in k.fasteners] + cart.fasteners + plate_fz:  # screws and inserts are kit parts too
            a_ = unit(f_.axis)
            ends = [np.asarray(f_.head) - a_ * (fz["screw"]["head_h"] + 0.5), np.asarray(f_.head) + a_ * (f_.length + 0.5)]
            lo_, hi_ = np.minimum(*ends) - hd, np.maximum(*ends) + hd
            fin_keep.append([lo_[0], hi_[0], lo_[1], hi_[1], lo_[2], hi_[2]])
        try:
            fin_adds, fin_info = ceiling_fins(getattr(bg, "roof_region", None), ycav0, ycav1, fin_keep, probeO,
                                              probeC=probeC if fam != "silhouette" else None)
        except Exception as e:  # pragma: no cover
            M.warnings.append(f"ceiling fins skipped: {e}")
    M.info["ceiling_fins"] = fin_info
    adds = kit_adds + sp.shell_adds + (floor_adds if split == "rear_cover" else [])
    # Mount material is carved out of the cavity, not fused onto the shell: S = O - (C - adds) = (O - C) + (adds & O).
    # This avoids fusing many pieces that share the outer B-spline surface (slow) and clips every add to the body.
    dbg = bool(os.environ.get("GS_CAD_DEBUG"))
    C_solid = C_open.cut(*adds) if adds else C_open
    T.mark("shell_bool.cavity_minus_adds")
    fins_in_cavity = False
    if fin_adds:  # fastest when it closes: fins taken out of the (simpler) cavity solid; else fused onto the shell below
        try:
            t_ = C_solid.cut(*fin_adds)
            if t_.isValid() and len(t_.Solids()) == 1:
                C_solid, fins_in_cavity = t_, True
                M.info["ceiling_fins"].update({"kept": len(fin_adds), "skipped": 0, "method": "cut from the cavity"})
        except Exception:
            pass
        T.mark("shell_bool.fins_cavity")
    S = O.cut(C_solid)
    T.mark("shell_bool.outer_minus_cavity")
    if dbg:
        print("[dbg] O", O.isValid(), "C", C_open.isValid(), "C-adds", C_solid.isValid(), len(C_solid.Solids()), "S", S.isValid(), len(S.Solids()), flush=True)
        if not C_solid.isValid():
            named = [(f"kit {k.feature_id} add {i}", a) for k in kits for i, a in enumerate(k.adds)] + \
                    [(f"split add {i}", a) for i, a in enumerate(sp.shell_adds)] + \
                    ([(f"floor add {i}", a) for i, a in enumerate(floor_adds)] if split == "rear_cover" else [])
            for nm, a in named:
                try:
                    ok = C_open.cut(a).isValid()
                except Exception as e:
                    ok = f"error {e}"
                if ok is not True:
                    print("[dbg] bad add:", nm, ok, a.isValid(), flush=True)
    cuts = kit_cuts + sp.shell_cuts + [tunnel] + ((floor_cuts + seats) if split == "rear_cover" else [])
    S = S.cut(*cuts)
    T.mark("shell_bool.cuts")
    if dbg:
        print("[dbg] S-cuts", S.isValid(), len(S.Solids()), flush=True)
    S = S.clean()
    if dbg:
        print("[dbg] clean", S.isValid(), len(S.Solids()), flush=True)
    if fin_adds and not fins_in_cavity:  # fins fused onto the shell (their tops reach into the wall); a bad fin is skipped
        kept, skipped = 0, 0

        def _try(base, items):
            try:
                t_ = base.fuse(*items).clean()
                if t_.isValid() and len(t_.Solids()) == 1:
                    return t_
            except Exception:
                pass
            return None

        trial = _try(S, fin_adds)
        if trial is not None:
            S, kept = trial, len(fin_adds)
        else:  # batches of 8, one at a time only inside a batch that will not close
            for i0 in range(0, len(fin_adds), 8):
                batch = fin_adds[i0:i0 + 8]
                t_ = _try(S, batch)
                if t_ is not None:
                    S, kept = t_, kept + len(batch)
                    continue
                for fa in batch:
                    t_ = _try(S, [fa])
                    if t_ is not None:
                        S, kept = t_, kept + 1
                    else:
                        skipped += 1
        M.info["ceiling_fins"].update({"kept": kept, "skipped": skipped})
        if skipped:
            M.warnings.append(f"ceiling fins: {skipped} of {len(fin_adds)} fin pieces skipped (boolean would not close)")
        T.mark("shell_bool.fins")
        if dbg:
            print("[dbg] fins", kept, skipped, S.isValid(), len(S.Solids()), flush=True)
    if not S.isValid():
        from .body import fix_shape

        S = fix_shape(S)
        M.warnings.append("shell solid needed a shape fix after the booleans")
    T.mark("shell_bool.clean")
    T.mark("shell_bool")
    other = None
    def guarded(base, adds_=(), cuts_=(), what=""):
        """Apply optional geometry; keep it only when the part stays one valid solid (else report it)."""
        try:
            t_ = base.fuse(*adds_).clean() if adds_ else base
            t_ = t_.cut(*cuts_).clean() if cuts_ else t_
            if t_.isValid() and t_.Solids():
                return t_, True
        except Exception:
            pass
        M.warnings.append(f"{what}: skipped (the boolean would not close)")
        return base, False

    if split == "rear_cover":
        shell = S.intersect(halfspace_y(y_s, "below"))
        other = S.intersect(halfspace_y(y_s, "above"))
        if sp.other_adds:
            base_o = other
            try:
                other = other.fuse(*sp.other_adds).clean()
                batch_ok = other.isValid() and len(other.Solids()) == 1
            except Exception:  # e.g. "Null TopoDS_Shape object" from OCC on one bad piece (seen on an e2e blob body)
                batch_ok = False
            if not batch_ok:  # something did not merge: add the pieces one at a time and keep what joins
                cur, skipped_a = base_o, 0
                for a in sp.other_adds:
                    try:
                        t_ = cur.fuse(a).clean()
                        if t_.isValid() and len(t_.Solids()) <= len(cur.Solids()):
                            cur = t_
                            continue
                    except Exception:
                        pass
                    skipped_a += 1
                other = cur
                sp.info["cover_adds_not_joined"] = skipped_a
                if skipped_a:
                    M.warnings.append(f"rear cover: {skipped_a} of {len(sp.other_adds)} lip/tube pieces do not join the cover; left out")
        other = other.cut(*(sp.other_cuts + [tunnel])).clean()
        other_name, other_label = "PRINT_RearCover", "rear cover"
    else:
        shell = S
        plate = sp.other_base
        extra = floor_adds + sp.other_adds
        plate = plate.fuse(*extra).clean() if extra else plate
        other = plate.cut(*(sp.other_cuts + floor_cuts + seats)).clean()
        other_name, other_label = "PRINT_BasePlate", "base plate"
    if cord is not None:  # cord tie bridge on the part the USB-C tunnel leaves through (MIN-3)
        if split == "rear_cover":
            other, ok_c = guarded(other, cord["adds"], cord["cuts"], "cord tie bridge")
        else:
            shell, ok_c = guarded(shell, cord["adds"], cord["cuts"], "cord tie bridge")
        if not ok_c:
            cord = None
    shell, frag_s = split_fragments(shell)
    other, frag_o = split_fragments(other)
    M.info["fragments_dropped"] = {"PRINT_Shell": len(frag_s), other_name: len(frag_o)}
    M.info["fragments"] = {"PRINT_Shell": frag_s, other_name: frag_o}
    T.mark("split_bool")
    # ---------------------------------------------------------------- components
    say("assembling components", 72)
    mat_name = (params.get("print_material") or {}).get("name", "PLA")
    sh = Part("PRINT_Shell", "PRINT", "main shell", colors["body"], spec_path="body", mass_source="computed", role="shell",
              print_info={"up": "z", "reason": "upright on its flat base: face and decor print on vertical walls; "
                          "the head ceiling is carried by 45 deg fins (no support inside)" if M.info.get("ceiling_fins") else
                          "upright on its flat base: face and decor print on vertical walls", "material": mat_name})
    sh.add("Shell", shell)
    ot = Part(other_name, "PRINT", other_label, colors["body"], spec_path="body", mass_source="computed", role="shell",
              print_info={"up": "-y" if split == "rear_cover" else "z",
                          "reason": "outer back face on the bed, lip and tubes up; no supports" if split == "rear_cover"
                          else "flat bottom on the bed; counterbores and feet seats open to the bed; no supports", "material": mat_name})
    ot.add("Part", other)
    parts = [sh, ot]
    for k in kits:
        parts += k.parts
    parts += cart.parts
    fasteners = [f for k in kits for f in k.fasteners] + cart.fasteners + sp.fasteners
    parts += fastener_parts(fasteners, fz)
    if feet:
        fp = Part("PURCHASE_Feet", "PURCHASE", f"adhesive bumper feet x{len(feet)}", (0.20, 0.20, 0.21), module_id=foot.get("module") or HW_MODULE["Feet"],
                  mass_g=round(0.4 * len(feet), 2), extra={"qty": len(feet), "size": f"{foot['d']:g} x {foot['h']:g} mm bumper"}, role="hardware")
        fp.add("Feet", cq.Compound.makeCompound(feet))
        parts.append(fp)
    # unique names
    seen: dict[str, int] = {}
    for p in parts:
        if p.name in seen:
            seen[p.name] += 1
            p.name = f"{p.name}{seen[p.name]}"
        else:
            seen[p.name] = 1
    M.parts = parts
    M.fasteners = fasteners
    for k in kits:  # screens name their (uniquely named) display component
        scr = k.info.get("screen")
        if scr:
            disp = next((p for p in k.parts if p.kind == "PURCHASE" and p.role == "module"), None)
            if disp is not None:
                scr["component"] = disp.name
    M.face_colors["PRINT_Shell"] = classify_faces(shell, pv["records"], colors) if params.get("options", {}).get("detail_fill", True) else {}
    # decor regions -> shell faces, for the GLB pick nodes PRINT_Shell#decor[i] (REVIEW-1 MAJ-35)
    regs = [(f"PRINT_Shell#decor[{i}]", sol) for i, sol in (pv.get("ear_solids") or []) if i is not None]
    for r in pv["records"]:
        idx = list(getattr(r, "idx", []) or [])
        if idx and r.tool is not None:
            regs.append((f"PRINT_Shell#decor[{idx[0]}]", r.tool))
    try:
        M.face_regions = classify_regions(shell, regs)
    except Exception as e:  # pragma: no cover
        M.face_regions = {}
        M.warnings.append(f"decor pick regions skipped: {e}")
    T.mark("decor_regions")
    M.info["decor_regions"] = pv.get("regions", [])
    M.info["decor_adjustments"] = pv.get("adjustments", [])
    sil = pv.get("silhouette")
    M.info["silhouette_xz"] = [[round(a, 3), round(b, 3)] for a, b in np.asarray(sil.exterior.coords)] if sil is not None and not sil.is_empty else None
    M.info["side_top_z"] = pv.get("side_top_z")
    M.shapes_decor = pv.get("ear_solids", [])
    for k in kits:
        M.anchors.update(k.anchors)
    M.anchors.update(cart.anchors)
    M.shapes = {"outer": O, "cavity": C_open, "shell": shell, "other": other}
    T.mark("components")
    # ---------------------------------------------------------------- cables
    if route_cables:
        say("routing cables", 80)
        from .cables import route_all

        try:
            tie_pts = [[(b.BoundingBox().xmin + b.BoundingBox().xmax) / 2, (b.BoundingBox().ymin + b.BoundingBox().ymax) / 2,
                        b.BoundingBox().zmax + 4.0] for b in ties]
            routes, cw = route_all(M, kits, cart, params, C_open, ties=tie_pts)
            M.cables = routes
            M.warnings += cw
        except Exception as e:  # pragma: no cover
            M.warnings.append(f"cable routing failed: {e}")
        T.mark("cables")
    # ---------------------------------------------------------------- coupons + lift
    M.coupons = make_coupons(M, params, kits, sp, shell, other, split, fz)
    lift = (foot["h"] - foot["seat_depth"]) if feet else 0.0
    apply_lift(M, lift)
    T.mark("coupons_lift")
    M.info.update({
        "family": fam, "split": split, "body": bg.info, "wall": wall, "skin_front": bg.skin_front, "ears": pv["ears"],
        "decor_items": len(pv["records"]), "seam_y": None if y_s is None else round(y_s + 0.0, 2), "lift": lift,
        "cartridge": cart.info, "split_info": sp.info, "feet": [[round(x, 2), round(y, 2)] for x, y in fpts], "tie_anchors": len(ties),
        "kits": {k.feature_id: {"kind": k.kind, "ref": k.ref, "grille_holes": k.grille_holes, **k.info,
                                "frame": None if k.frame is None else k.frame.to_json()} for k in kits},
        "screens": [k.info["screen"] for k in kits if k.info.get("screen")],
        "usb_c": {"receptacle_face": [round(float(v), 2) for v in usb_pt], "opening": (bp.get("cartridge") or {}).get("usb_c_opening"),
                  "load_path": cord["info"] if cord else None},
    })
    M.timings = T.marks
    return M


def apply_lift(M: Model, lift: float):
    M.lift = lift
    if abs(lift) < 1e-9:
        return
    v = V(0, 0, lift)
    for p in M.parts:
        p.subparts = [(n, s.translate(v), c) for n, s, c in p.subparts]
        if p.print_info and p.print_info.get("frame") is not None:
            F = p.print_info["frame"]
            p.print_info["frame"] = Frame(F.o + np.array([0, 0, lift]), F.u, F.v, F.n)
    for k, a in M.anchors.items():
        M.anchors[k] = [a[0], a[1], a[2] + lift]
    for f in M.fasteners:
        f.head = f.head + np.array([0, 0, lift])
        if f.joint is not None:
            f.joint = f.joint + np.array([0, 0, lift])
    for r in M.cables:
        r["points"] = [[p[0], p[1], p[2] + lift] for p in r["points"]]
    for r in M.info.get("decor_regions", []):
        b = r["bbox"]
        r["bbox"] = [b[0], b[1], b[2], b[3], round(b[4] + lift, 2), round(b[5] + lift, 2)]
    for k in M.kits:
        scr = k.info.get("screen")
        if scr:
            scr["c"] = [scr["c"][0], scr["c"][1], scr["c"][2] + lift]
    M.shapes = {k: s.translate(v) for k, s in M.shapes.items()}


def make_coupons(M: Model, params, kits, sp, shell, other, split, fz) -> dict:
    """Fit coupons: every tight interface on small prints before the big ones (QB-13)."""
    from .enclosure import tie_anchor
    from .geom import box_z, cyl_z, rrect_prism, ring_z

    out = {}
    ins = fz["insert"]
    snap = next((k for k in kits if k.kind == "snap_button"), None)
    tact = next((k for k in kits if k.kind == "tactile"), None)
    panel_t = 3.0
    if snap is not None:
        panel_t = snap.info.get("panel_thickness", 3.0)
    W, H = 78.0, 46.0
    plate = box_z(0, 0, 0, panel_t, W, H)
    feats = {"panel_thickness_mm": panel_t}
    if snap is not None:
        b = next(f for f in params["features"] if f["id"] == snap.feature_id)["desc"]["snap_button"]
        plate = plate.cut(cyl_z(-20, 0, -1, panel_t + 1, b["hole_d"] / 2 + 0.15))
        feats["button_hole_d"] = b["hole_d"] + 0.3
    elif tact is not None:
        plate = plate.cut(cyl_z(-20, 0, -1, panel_t + 1, 11.0 / 2 + 0.3))
        feats["button_hole_d"] = 11.6
    for x, y in ((10, 12), (10, -12)):
        plate = plate.fuse(cyl_z(x, y, 0, panel_t + 7.0, ins["boss_d"] / 2))
        plate = plate.cut(cyl_z(x, y, panel_t + 7.0 - ins["hole_depth"], panel_t + 7.1, ins["hole_d"] / 2))
    feats["insert_holes"] = {"d": ins["hole_d"], "depth": ins["hole_depth"], "boss_od": ins["boss_d"]}
    blk, tun = tie_anchor(28, 12, panel_t, fz)
    plate = plate.fuse(blk).cut(tun)
    feats["tie_slot"] = [fz.get("tie", {}).get("slot_w", 3.6), fz.get("tie", {}).get("slot_h", 1.8)]
    op = (params["backplane"].get("cartridge") or {}).get("usb_c_opening") or {"w": 13.0, "h": 7.0, "corner_r": 2.5}
    wallp = box_z(28, -12, 0, panel_t + 12, 20, 2.4)
    usb = rrect_prism(0, 0, -2, 2, op["w"], op["h"], op.get("corner_r", 2.5)).rotate(V(0, 0, 0), V(1, 0, 0), 90).translate(V(28, -12, panel_t + 1.0 + op["h"] / 2))
    plate = plate.fuse(wallp).cut(usb)
    feats["usb_c_opening"] = [op["w"], op["h"]]
    mic = next((k for k in kits if k.kind == "mic"), None)
    if mic is not None:
        plate = plate.cut(cyl_z(-20, 17, -1, panel_t + 1, mic.info.get("port_d", 1.5) / 2))
        feats["mic_port_d"] = mic.info.get("port_d", 1.5)
    tests = ["M2.5 heat-set insert holes (x2, hole {:g} mm, boss {:g} mm)".format(ins["hole_d"], ins["boss_d"]), "cable-tie slot",
             "USB-C plug overmold opening {:g} x {:g} mm".format(op["w"], op["h"])]
    if "button_hole_d" in feats:
        tests.insert(0, ("snap-in button hole {:.1f} mm in a {:g} mm panel" if snap is not None else "plunger cap hole {:.1f} mm").format(feats["button_hole_d"], panel_t))
    if "mic_port_d" in feats:
        tests.append("microphone port {:g} mm".format(feats["mic_port_d"]))
    out["TEST_FitCoupon"] = {"shape": largest_solid(plate.clean()), "features": feats, "tests": tests, "covers": sorted(feats),
                             "note": "print first in the same material and settings; check the button snap, insert melt-in, tie slot and USB-C plug before printing the shell"}
    # sections cut from the real shell around every tight interface a kit exports (REVIEW-1 MIN-11)
    from .geom import camel

    for k in kits:
        regs = [i for i in (k.interfaces or []) if i.get("region")]
        if not regs or k.frame is None:
            continue
        F = regs[0]["region"][0]
        bx = [i["region"][1] for i in regs]
        a0, a1 = min(b[0] for b in bx), max(b[1] for b in bx)
        b0, b1 = min(b[2] for b in bx), max(b[3] for b in bx)
        z0, z1 = min(b[4] for b in bx), max(b[5] for b in bx)
        try:
            piece = largest_solid(shell.intersect(F.place(box_z((a0 + a1) / 2, (b0 + b1) / 2, z0, z1, a1 - a0, b1 - b0))).clean())
        except Exception as e:  # pragma: no cover
            M.warnings.append(f"fit coupon for {k.feature_id} failed: {e}")
            continue
        if piece is None or piece.Volume() < 5.0:
            M.warnings.append(f"fit coupon for {k.feature_id}: the section is empty")
            continue
        out[f"TEST_Fit{camel(k.feature_id)}"] = {
            "shape": piece, "features": {i["id"]: i.get("nominal") for i in regs}, "covers": [i["id"] for i in regs],
            "tests": [f"{i['name']} ({i['fit']})" for i in regs], "feature_id": k.feature_id,
            "note": f"section of the real shell around the {k.label or k.ref} mount; print it upright like the shell and fit the real "
                    "module, glass, diffuser, gasket and clamp before printing the shell"}
    # seam section: the real cover/plate lip + one screw boss and the matching shell corner
    if sp.boss_points:
        p = np.array(sp.boss_points[0])
        if split == "rear_cover":
            box = cq.Solid.makeBox(36, 40, 36, V(p[0] - 18, p[1] - 22, p[2] - 18))
        else:
            box = cq.Solid.makeBox(36, 36, 30, V(p[0] - 18, p[1] - 18, -2))
        try:
            a = largest_solid(shell.intersect(box))
            b = largest_solid(other.intersect(box))
            mate = "cover" if split == "rear_cover" else "base plate"
            out["TEST_SeamShell"] = {"shape": a, "features": {"boss_insert": True}, "covers": ["boss_insert"],
                                     "tests": [f"{mate} screw boss with M2.5 heat-set insert", "seam face and wall section"],
                                     "note": f"shell corner with one {mate} boss"}
            out["TEST_SeamMate"] = {"shape": b, "features": {"lip_gap_mm": sp.info.get("lip_gap_mm"), "screw_clearance": fz["screw"]["clear_d"]},
                                    "covers": ["lip_gap_mm", "screw_clearance"],
                                    "tests": ([f"cover locating lip, {sp.info.get('lip_gap_mm', 0.25):g} mm gap to the shell wall"] if split == "rear_cover"
                                              else ["base plate edge in the bottom opening (0.3 mm gap)"]) +
                                             [f"screw clearance hole {fz['screw']['clear_d']:g} mm and head counterbore"],
                                    "note": f"matching {mate} corner: print with TEST_SeamShell; the parts must close and the screw must reach the insert"}
        except Exception as e:  # pragma: no cover
            M.warnings.append(f"seam coupon failed: {e}")
    return out
