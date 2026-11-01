"""params -> Model: the whole parametric geometry pipeline (no file output here).

Order: body -> ears -> decor relief -> (draft) -> feature kits -> cartridge placement -> split (cover / base plate)
-> feet, tie anchors, USB-C tunnel -> shell booleans -> components -> cable routes -> lift onto feet.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cadquery as cq
import numpy as np
from shapely.geometry import Point, box as sbox
from shapely.ops import unary_union

from .body import build_body
from .decor import MIN_SKIN, apply_ears, apply_relief, decor_stack
from .enclosure import (base_plate, base_plate_opening, feet_points, feet_solids, place_cartridge, rear_cover, seam_y, tie_anchor,
                        usb_tunnel)
from .features import build_kit
from .geom import V, Frame, bbox, cyl_axis, halfspace_y, largest_solid, prism, single_solid, unit
from .internals import build_cartridge
from .parts import Fastener, Part
from .probe import Probe, largest

HW_MODULE = {"HeatSetInserts": "INSERT_HEATSET_M2_5X4_STD", "CartridgeScrews": "SCREW_SET_M2_M2_5_SHCS_A2",
             "CoverScrews": "SCREW_SET_M2_M2_5_SHCS_A2", "PlateScrews": "SCREW_SET_M2_M2_5_SHCS_A2",
             "ModuleScrews": "SCREW_SET_M2_M2_5_SHCS_A2", "Feet": "FEET_BUMPON_SJ5302_CLEAR"}


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
    skin = max(wall, stack + MIN_SKIN) if fam == "silhouette" else wall
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
    dr = apply_relief(er.outer, decor, pr0, cavity_pads=(fam != "silhouette"), wall=wall)
    t_relief = time.time() - t
    regions = []
    for r in dr.records:
        b = r.tool.BoundingBox()
        regions.append({"spec_paths": [f"decor[{i}]" for i in getattr(r, "idx", [])], "type": r.type, "label": r.label,
                        "bbox": [round(v, 2) for v in (b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax)]})
    for e in er.ears:
        regions.append({"spec_paths": [f"decor[{e['idx']}]"], "type": "ear", "label": e["style"], "bbox": e["bbox"]})
    return {"bg": bg, "outer": dr.outer, "records": dr.records, "pads": dr.pads, "regions": regions,
            "warnings": bg.warnings + er.warnings + dr.warnings, "ears": er.ears,
            "timings": {"body": round(t_body, 2), "ears": round(t_ears, 2), "relief": round(t_relief, 2)}}


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
        p = Part(f"PURCHASE_{g}", "PURCHASE", label, col, module_id=HW_MODULE.get(g), mass_g=round(len(items) * (0.35 if g == "HeatSetInserts" else 0.3), 2))
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
    for f in params["features"]:
        k = build_kit(f, f["desc"], f["ref"], probeO, probeC, O, fz, colors, f["spec_path"])
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
    adds = kit_adds + sp.shell_adds + (floor_adds if split == "rear_cover" else [])
    # Mount material is carved out of the cavity, not fused onto the shell: S = O - (C - adds) = (O - C) + (adds & O).
    # This avoids fusing many pieces that share the outer B-spline surface (slow) and clips every add to the body.
    C_solid = C_open.cut(*adds) if adds else C_open
    T.mark("shell_bool.cavity_minus_adds")
    S = O.cut(C_solid)
    T.mark("shell_bool.outer_minus_cavity")
    cuts = kit_cuts + sp.shell_cuts + [tunnel] + ((floor_cuts + seats) if split == "rear_cover" else [])
    S = S.cut(*cuts)
    T.mark("shell_bool.cuts")
    S = S.clean()
    T.mark("shell_bool.clean")
    T.mark("shell_bool")
    other = None
    if split == "rear_cover":
        shell = S.intersect(halfspace_y(y_s, "below"))
        other = S.intersect(halfspace_y(y_s, "above"))
        if sp.other_adds:
            other = other.fuse(*sp.other_adds).clean()
        other = other.cut(*(sp.other_cuts + [tunnel])).clean()
        other_name, other_label = "PRINT_RearCover", "rear cover"
    else:
        shell = S
        plate = sp.other_base
        extra = floor_adds + sp.other_adds
        plate = plate.fuse(*extra).clean() if extra else plate
        other = plate.cut(*(sp.other_cuts + floor_cuts + seats)).clean()
        other_name, other_label = "PRINT_BasePlate", "base plate"
    shell, frag_s = single_solid(shell)
    other, frag_o = single_solid(other)
    M.info["fragments_dropped"] = {"PRINT_Shell": frag_s, other_name: frag_o}
    T.mark("split_bool")
    # ---------------------------------------------------------------- components
    say("assembling components", 72)
    sh = Part("PRINT_Shell", "PRINT", "main shell", colors["body"], spec_path="body", mass_source="computed",
              print_info={"up": "z", "reason": "upright on its flat base: face and decor print on vertical walls; "
                          "tree supports for the domed top and the inside of the head", "material": "PLA"})
    sh.add("Shell", shell)
    ot = Part(other_name, "PRINT", other_label, colors["body"], spec_path="body", mass_source="computed",
              print_info={"up": "-y" if split == "rear_cover" else "z",
                          "reason": "outer back face on the bed, lip and tubes up; no supports" if split == "rear_cover"
                          else "flat bottom on the bed; counterbores and feet seats open to the bed; no supports", "material": "PLA"})
    ot.add("Part", other)
    parts = [sh, ot]
    for k in kits:
        parts += k.parts
    parts += cart.parts
    fasteners = [f for k in kits for f in k.fasteners] + cart.fasteners + sp.fasteners
    parts += fastener_parts(fasteners, fz)
    if feet:
        fp = Part("PURCHASE_Feet", "PURCHASE", f"adhesive bumper feet x{len(feet)}", (0.20, 0.20, 0.21), module_id=HW_MODULE["Feet"],
                  mass_g=round(0.4 * len(feet), 2), extra={"qty": len(feet), "size": f"{foot['d']:g} x {foot['h']:g} mm bumper"})
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
    M.face_colors["PRINT_Shell"] = classify_faces(shell, pv["records"], colors) if params.get("options", {}).get("detail_fill", True) else {}
    M.info["decor_regions"] = pv.get("regions", [])
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
            routes, cw = route_all(M, kits, cart, params, C_open)
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
        "usb_c": {"receptacle_face": [round(float(v), 2) for v in usb_pt], "opening": (bp.get("cartridge") or {}).get("usb_c_opening")},
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
    out["TEST_FitCoupon"] = {"shape": largest_solid(plate.clean()), "features": feats, "tests": tests,
                             "note": "print first in the same material and settings; check the button snap, insert melt-in, tie slot and USB-C plug before printing the shell"}
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
            out["TEST_SeamShell"] = {"shape": a, "features": {"boss_insert": True},
                                     "tests": [f"{mate} screw boss with M2.5 heat-set insert", "seam face and wall section"],
                                     "note": f"shell corner with one {mate} boss"}
            out["TEST_SeamMate"] = {"shape": b, "features": {"lip_gap_mm": sp.info.get("lip_gap_mm"), "screw_clearance": fz["screw"]["clear_d"]},
                                    "tests": ([f"cover locating lip, {sp.info.get('lip_gap_mm', 0.25):g} mm gap to the shell wall"] if split == "rear_cover"
                                              else ["base plate edge in the bottom opening (0.3 mm gap)"]) +
                                             [f"screw clearance hole {fz['screw']['clear_d']:g} mm and head counterbore"],
                                    "note": f"matching {mate} corner: print with TEST_SeamShell; the parts must close and the screw must reach the insert"}
        except Exception as e:  # pragma: no cover
            M.warnings.append(f"seam coupon failed: {e}")
    return out
