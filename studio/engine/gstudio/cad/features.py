"""Functional features: apertures through the shell plus the mount kit behind each one.

Every kit is built in a local frame at the feature (origin on the outer surface, +z = outward surface normal,
u right, v up) and placed in the world. A kit adds a flat local pad on the inner wall (so a module always seats on
a flat face, also on curved revolve/blob fronts), posts with M2.5 heat-set inserts, and a printed clamp or ring.
"""

from __future__ import annotations

import math
from typing import Optional

import cadquery as cq
import numpy as np
from shapely import affinity
from shapely.geometry import LineString as _LS0, Point, Polygon

from . import footprint as FP
from . import shapes2d
from .geom import (Frame, V, box_z, circle_face, cone_z, cyl_z, ellipse_face, largest_solid, poly_face, prism, ring_z,
                   rounded_rect_face_fast, rrect_prism, stadium_face, try_fillet, unit)
from .modules import pick_screw, screw_length
from .parts import Fastener, Kit, Part

try:
    from . import kits_ext  # pack kits (cad/kits_*.py)
except ImportError:  # pragma: no cover - a vendored gadget_cad copy without the registry (export.VENDOR)
    kits_ext = None

FACE_RAY = {
    "front": ((0, 1, 0), (0, 0, 1)),
    "rear": ((0, -1, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),  # robot's left = +X side
    "right": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, -1), (0, 1, 0)),
}


def face_origin(face: str, at) -> np.ndarray:
    a, b = float(at[0]), float(at[1])
    return {"front": (a, -1000, b), "rear": (a, 1000, b), "left": (1000, a, b), "right": (-1000, a, b),
            "top": (a, b, 1000)}[face]


def feature_frame(probeO, face: str, at, rotate: float = 0.0, radius: float = 6.0) -> Optional[Frame]:
    d, up = FACE_RAY[face]
    d = np.asarray(d, float)
    o = np.asarray(face_origin(face, at), float)
    hit, nrm, _ = probeO.hits([o], [d])
    if not np.isfinite(hit[0, 0]):
        return None
    tmp = Frame.make(hit[0], -d, up)
    pts = [tmp.pt(0, 0, 0)] + [tmp.pt(radius * math.cos(a), radius * math.sin(a), 0) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
    oo = np.asarray(pts) - d * 200
    hh, nn, _ = probeO.hits(oo, [d])
    good = np.isfinite(hh[:, 0])
    n = unit(nn[good].mean(0)) if good.any() else -d
    if float(np.dot(n, -d)) > math.cos(math.radians(8)):
        n = -d
    return Frame.make(hit[0], n, up).rotated(rotate)


class Surf:
    """Local surface sampler for one frame (outer surface z_o and inner cavity surface z_i along -n)."""

    def __init__(self, frame: Frame, probeO, probeC):
        self.f, self.pO, self.pC = frame, probeO, probeC

    def sample(self, uv) -> tuple[np.ndarray, np.ndarray]:
        uv = np.asarray(uv, float)
        o = np.array([self.f.pt(u, v, 80.0) for u, v in uv])
        d = -self.f.n
        _, _, dO = self.pO.hits(o, [d])
        _, _, dC = self.pC.hits(o, [d])
        return 80.0 - dO, 80.0 - dC

    def disk(self, r: float, n_ring: int = 12):
        uv = [(0.0, 0.0)]
        for rr in (r * 0.5, r):
            uv += [(rr * math.cos(a), rr * math.sin(a)) for a in np.linspace(0, 2 * math.pi, n_ring, endpoint=False)]
        return self.sample(uv)

    def rect(self, w: float, h: float, cu=0.0, cv=0.0, n=5):
        uv = [(cu + a, cv + b) for a in np.linspace(-w / 2, w / 2, n) for b in np.linspace(-h / 2, h / 2, n)]
        return self.sample(uv)


def _fin(a, default):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(a.min()) if len(a) else default


def open_top(F, ring, r_out: float, z0: float, z1: float):
    """Remove the top 90 deg of a horizontal tube (kit axis not vertical): its inner ceiling would print in mid-air.
    The rest of the tube is steeper than 45 deg (REVIEW-1 MAJ-9). Returns the (local) ring unchanged when the axis is
    within 30 deg of vertical."""
    up = np.array([float(np.dot(F.u, (0, 0, 1))), float(np.dot(F.v, (0, 0, 1)))])
    if np.linalg.norm(up) < 0.5:
        return ring
    a = math.atan2(up[1], up[0])
    R = r_out + 5.0
    pts = [(0.0, 0.0)] + [(R * math.cos(a + t), R * math.sin(a + t)) for t in np.linspace(-math.pi / 4, math.pi / 4, 9)]
    wedge = prism(poly_face(Polygon(pts), smooth=False), z0 - 1.0, z1 + 1.0)
    try:
        out = ring.cut(wedge)
        return out if out.isValid() else ring
    except Exception:
        return ring


# ================================================================================================ board clamp
def board_clamp(kit: Kit, F, fz: dict, pcb: dict, center, z_front: float, z_pad: float, back_items: list,
                clip_name: str, color, standoffs: bool = False, spec_path: Optional[str] = None,
                avoid_axis: Optional[str] = None, standoff_avoid: Optional[tuple] = None):
    """Printed strap that presses a board (front face at z_front) toward the pad, on 2 posts with M2.5 inserts.
    Adds posts (+ optional standoffs) to kit.adds, insert holes to kit.cuts, the strap Part and the fasteners."""
    w, h, t = pcb["w"], pcb["h"], pcb["t"]
    cu, cv = center
    ins, scr = fz["insert"], fz["screw"]
    rp = ins["boss_d"] / 2
    lay = FP.clamp_layout(pcb, back_items, fz, avoid_axis)  # shared with the layout validator (footprint.py)
    axis, off, hb, span = lay["axis"], lay["off"], lay["hb"], lay["span"]

    def P(a, b):  # along-axis / perpendicular -> (u, v)
        return (cu + a, cv + b) if axis == "u" else (cu + b, cv + a)

    reach = lay["reach"]
    strap_t = 2.4
    z_sf, L, hole_depth = FP.clamp_z(z_front, t, hb, z_pad, fz)
    posts, holes = [], []
    for sgn in (-1, 1):
        pu, pv = P(sgn * reach, off)
        posts.append(F.place(cyl_z(pu, pv, z_sf, z_pad + 0.8, rp)))
        holes.append(F.place(cyl_z(pu, pv, z_sf - 0.01, z_sf + hole_depth, ins["hole_d"] / 2)))
        kit.fasteners.append(Fastener("insert", "HeatSetInserts", F.pt(pu, pv, z_sf), -F.n, ins["length"], ins["od"],
                                      f"{kit.ref} clamp insert"))
        kit.fasteners.append(Fastener("screw", "ModuleScrews", F.pt(pu, pv, z_sf - strap_t), F.n, L, scr["d"],
                                      f"{kit.ref} clamp screw", hole_depth=hole_depth, joint=F.pt(pu, pv, z_sf),
                                      insert_len=ins["length"], clamp_t=strap_t))
        kit.anchors[f"{kit.ref}.screw{1 if sgn < 0 else 2}"] = F.pt(pu, pv, z_sf - strap_t).tolist()
    kit.adds += posts
    kit.cuts += holes
    # strap
    if axis == "u":
        f = stadium_face(2 * reach + 2 * rp, 2 * rp, cu, cv + off)
    else:
        f = stadium_face(2 * reach + 2 * rp, 2 * rp, cu + off, cv, angle=90)
    strap = prism(f, z_sf - strap_t, z_sf)
    for sgn in (-1, 1):
        pu, pv = P(sgn * reach, off)
        strap = strap.cut(cyl_z(pu, pv, z_sf - strap_t - 1, z_sf + 1, scr["clear_d"] / 2))
    fl = z_sf
    fh = (z_front - t) - z_sf
    fingers = []
    if fh > 0.05:
        perp = h if axis == "u" else w
        for sgn in (-1, 1):
            placed = None
            for a in (span / 2 - 1.6, span / 2 - 1.1, span / 2 - 2.4):
                for b in (off, off + 2.0, off - 2.0, off + 3.5, off - 3.5):
                    if abs(b) > perp / 2 - 1.2:
                        continue
                    pu, pv = P(sgn * a, b)
                    fw, fhh = (2.4, 3.6) if axis == "u" else (3.6, 2.4)
                    if not any(abs(pu - cu - it["c"][0]) < (it["size"][0] + fw) / 2 + 0.3 and abs(pv - cv - it["c"][1]) < (it["size"][1] + fhh) / 2 + 0.3
                               for it in back_items):
                        placed = (pu, pv, fw, fhh)
                        break
                if placed:
                    break
            if placed:
                pu, pv, fw, fhh = placed
                # finger runs from the strap to the board; bridge it to the strap band if it moved sideways
                strap = strap.fuse(box_z(pu, pv, fl - 0.01, z_front - t, fw, fhh))
                pa = P(sgn * (span / 2 - 1.6), off)
                if abs(pu - pa[0]) + abs(pv - pa[1]) > 0.1:
                    strap = strap.fuse(box_z((pu + pa[0]) / 2, (pv + pa[1]) / 2, fl - strap_t, fl, abs(pu - pa[0]) + fw, abs(pv - pa[1]) + fhh))
                fingers.append([round(pu, 2), round(pv, 2)])
    strap = largest_solid(strap.clean())
    part = Part(clip_name, "PRINT", f"clamp strap: {kit.label or kit.ref}", color, feature_id=kit.feature_id, role="retainer",
                host_ref=kit.ref, spec_path=spec_path,
                print_info={"frame": F, "up": "+z", "reason": "flat strap face on the bed, fingers up; no supports"})
    part.add("Strap", F.place(strap))
    kit.parts.append(part)
    if standoffs and z_pad - z_front > 0.05:
        if standoff_avoid is None:
            for sgn in (-1, 1):
                a = sgn * (span / 2 - 1.4)
                pu, pv = P(a, off)
                sz = (2.4, 5.0) if axis == "u" else (5.0, 2.4)
                kit.adds.append(F.place(box_z(pu, pv, z_front, z_pad + 0.8, *sz)))
        else:  # corner standoffs clear of a round keep-out (e.g. a button cap flange)
            ax_, ay_, ar_ = standoff_avoid
            for su in (-1, 1):
                for sv in (-1, 1):
                    pu, pv = cu + su * (w / 2 - 1.3), cv + sv * (h / 2 - 1.3)
                    if math.hypot(pu - ax_, pv - ay_) < ar_ + 1.8:
                        continue
                    kit.adds.append(F.place(box_z(pu, pv, z_front, z_pad + 0.8, 2.4, 2.4)))
    kit.info["clamp"] = {"axis": axis, "offset": round(off, 2), "strap_front_z": round(z_sf, 2), "post_len": round(z_pad - z_sf, 2),
                         "fingers": fingers, "screw": f"M2.5x{L:g}", "insert_hole_depth": round(hole_depth, 2)}
    pu0, pv0 = P(-reach, off)
    pu1, pv1 = P(reach, off)
    kit.interfaces.append({"id": f"{kit.ref}.clamp_posts", "name": f"{kit.label or kit.ref}: clamp strap on 2 insert posts",
                           "nominal": {"post_spacing_mm": round(2 * reach, 2), "strap_t_mm": strap_t, "screw": f"M2.5x{L:g}",
                                       "finger_to_board_mm": round(z_front - t - z_sf, 2)},
                           "fit": "screwed clamp; the strap fingers must touch the board without bending it",
                           "region": (F, (min(pu0, pu1) - rp - 2, max(pu0, pu1) + rp + 2, min(pv0, pv1) - rp - 2, max(pv0, pv1) + rp + 2,
                                          z_sf - 0.5, 1.5))})
    return z_sf


def board_model(F, pcb, center, z_front, front_items, back_items) -> list:
    """Board + front/back items as (subname, shape, color) in world coords; board front at z_front."""
    w, h, t = pcb["w"], pcb["h"], pcb["t"]
    cu, cv = center
    subs = [("Pcb", F.place(box_z(cu, cv, z_front - t, z_front, w, h)), pcb["color"])]
    for it in front_items:
        c, s = it["c"], it.get("size")
        z0 = z_front + it.get("z0", 0.0)
        if it.get("kind") == "cyl":
            sh = cyl_z(cu + c[0], cv + c[1], z0, z0 + it["h"], it["d"] / 2)
        else:
            sh = box_z(cu + c[0], cv + c[1], z0, z0 + s[2], s[0], s[1])
            if it.get("rot"):
                sh = sh.rotate(V(cu + c[0], cv + c[1], 0), V(cu + c[0], cv + c[1], 1), it["rot"])
        subs.append((it["name"], F.place(sh), it["color"]))
    for it in back_items:
        c, s = it["c"], it["size"]
        zb = z_front - t
        subs.append((it["name"], F.place(box_z(cu + c[0], cv + c[1], zb - s[2], zb, s[0], s[1])), it["color"]))
    return subs


def _pad(F, O, face2d, z_back: float):
    """Flat local mounting pad: a prism from z_back outward. It is subtracted from the cavity (not fused onto the
    shell), so it fills exactly the cavity space in front of z_back and never sticks out of the outer surface."""
    return F.place(prism(face2d, z_back, 40.0))


def _spot_face(kit, F, face2d, z_sf):
    if z_sf < -0.05:
        kit.cuts.append(F.place(prism(face2d, z_sf, 40.0)))


# ================================================================================================ kits
def kit_display(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path):
    pcb, g, va = d["pcb"], d["glass"], d["view"]
    gc = g["c"]
    bez_w, bez_h = va["w"] + 5.0, va["h"] + 5.0
    zo, _ = S.rect(bez_w, bez_h, va["c"][0], va["c"][1])
    z_sf = min(0.0, _fin(zo, 0.0))
    _, zi = S.rect(pcb["w"] + 18, pcb["h"] + 4)
    z_in = _fin(zi, z_sf - 3.0)
    face_spot = rounded_rect_face_fast(bez_w + 4, bez_h + 4, 4.0, va["c"][0], va["c"][1])
    _spot_face(kit, F, face_spot, z_sf)
    bezel_d = 0.6
    z_g = z_sf - bezel_d - 1.2  # glass front: 1.2 mm skin behind the bezel recess (min wall rule)
    z_p = z_g - g["t"]  # PCB front
    z_pad = min(z_in, z_p - 0.8)
    pad_face = rounded_rect_face_fast(pcb["w"] + 2 * (0.6 + fz["insert"]["boss_d"]) + 4, pcb["h"] + 6, 3.0)
    kit.adds.append(_pad(F, O, pad_face, z_pad))
    kit.cuts.append(F.place(rrect_prism(va["c"][0], va["c"][1], z_sf - bezel_d, 30, bez_w, bez_h, 2.5)))
    kit.cuts.append(F.place(rrect_prism(va["c"][0], va["c"][1], z_g - 0.1, 30, va["w"] + 0.6, va["h"] + 0.6, 1.0)))
    kit.cuts.append(F.place(rrect_prism(gc[0], gc[1], z_pad - 1.0, z_g, g["w"] + 0.5, g["h"] + 0.5, 0.6)))
    kit.cuts.append(F.place(rrect_prism(0, 0, z_pad - 1.0, z_p, pcb["w"] + 0.6, pcb["h"] + 0.6, 0.8)))
    subs = board_model(F, pcb, (0, 0), z_p, [], d["back"])
    subs.insert(1, ("Glass", F.place(box_z(gc[0], gc[1], z_p, z_g, g["w"], g["h"])), (0.05, 0.06, 0.08)))
    subs.insert(2, ("Screen", F.place(box_z(va["c"][0], va["c"][1], z_g - 0.05, z_g + 0.0, va["w"], va["h"])), (0.02, 0.025, 0.04)))
    m = Part("PURCHASE_Display", "PURCHASE", d["name"], pcb["color"], ref=kit.ref, module_id=d["id"], feature_id=kit.feature_id,
             spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"), role="module")
    for s in subs:
        m.add(*s)
    kit.parts.append(m)
    board_clamp(kit, F, fz, pcb, (0, 0), z_p, z_pad, d["back"], "PRINT_DisplayClip", colors["print_inner"], spec_path=spec_path)
    kit.interfaces.append({"id": f"{kit.ref}.window", "name": f"{kit.label or kit.ref}: screen window, bezel recess and glass pocket",
                           "nominal": {"window_mm": [round(va["w"] + 0.6, 2), round(va["h"] + 0.6, 2)],
                                       "glass_pocket_mm": [round(g["w"] + 0.5, 2), round(g["h"] + 0.5, 2)],
                                       "pcb_pocket_mm": [round(pcb["w"] + 0.6, 2), round(pcb["h"] + 0.6, 2)],
                                       "glass_front_z": round(z_g, 2), "glass_estimated": bool(g.get("provisional"))},
                           "fit": "clearance 0.25-0.3 mm per side; the glass must sit unloaded",
                           "region": (F, (-pcb["w"] / 2 - 3.0, pcb["w"] / 2 + 3.0, -pcb["h"] / 2 - 3.0, pcb["h"] / 2 + 3.0, z_pad - 1.5, 1.5))})
    c = d["connector"]
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(c["c"][0], c["c"][1], z_p + c["z"]).tolist()
    kit.cable_end = (F.pt(c["c"][0], c["c"][1], z_p + c["z"]), -F.n)
    kit.info.update({"window_mm": [round(va["w"] + 0.6, 2), round(va["h"] + 0.6, 2)], "glass_front_z": round(z_g, 2),
                     "spot_face": round(z_sf, 2)})
    aw, ah = float(d["aperture"].get("w") or 0.0), float(d["aperture"].get("h") or 0.0)
    kit.info["screen"] = {"c": F.pt(va["c"][0], va["c"][1], z_g + 0.02).tolist(), "u": F.u.tolist(), "v": F.v.tolist(),
                          "n": F.n.tolist(), "w": va["w"], "h": va["h"], "kind": "lcd" if pcb["w"] > 45 else "oled",
                          "aa_w": aw if 0 < aw <= va["w"] else va["w"], "aa_h": ah if 0 < ah <= va["h"] else va["h"],
                          "ref": kit.ref, "component": "PURCHASE_Display", "feature_id": kit.feature_id,
                          "frame": "idle face (firmware gs_display.cpp draw_face Neutral); illustrative pixel colour"}


def _hole_face(poly):
    """Exact circle for round holes, exact stadium for slots, smooth spline otherwise (few faces = fast booleans)."""
    minx, miny, maxx, maxy = poly.bounds
    w, h = maxx - minx, maxy - miny
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    if abs(w - h) < 0.05 and abs(poly.area - math.pi * (w / 2) ** 2) < 0.06 * poly.area:
        return circle_face(w / 2, cx, cy)
    rect_area = w * h - (4 - math.pi) * (min(w, h) / 2) ** 2
    if abs(poly.area - rect_area) < 0.04 * poly.area:
        return stadium_face(w, h, cx, cy) if w >= h else stadium_face(h, w, cx, cy, angle=90)
    return poly_face(poly, spacing=0.8, smooth=True)


def _grille_points(g: dict, r_max: float) -> list:
    """Hole outlines (shapely) for the grille pattern, clipped to the region and the baffle circle r_max."""
    pat = g.get("pattern", "hex_holes")
    w, h, hole, p = float(g["w"]), float(g["h"]), float(g["hole"]), float(g["pitch"])
    if pat == "slots":
        region = shapes2d.rounded_rect(w, h, min(w, h) / 2)
    elif pat == "concentric":
        dd = min(w, h)
        region = shapes2d.ellipse(dd, dd)
    else:
        region = shapes2d.ellipse(w, h)
    region = region.intersection(Point(0, 0).buffer(r_max, quad_segs=32))
    inner = region.buffer(-hole / 2 - 0.15)
    out = []
    if inner.is_empty:
        return out
    if pat == "hex_holes":
        dy = p * math.sqrt(3) / 2
        nj = int(h / dy) + 2
        ni = int(w / p) + 2
        for j in range(-nj, nj + 1):
            for i in range(-ni, ni + 1):
                x = i * p + (p / 2 if j % 2 else 0.0)
                y = j * dy
                if inner.contains(Point(x, y)):
                    out.append(Point(x, y).buffer(hole / 2, quad_segs=8))
    elif pat == "dots_ellipse":
        ax, ay = w / max(w, h), h / max(w, h)
        if inner.contains(Point(0, 0)):
            out.append(Point(0, 0).buffer(hole / 2, quad_segs=8))
        k = 1
        while k * p * min(ax, ay) < r_max + p:
            rr = k * p
            n = max(6, int(round(2 * math.pi * rr / p)))
            for m in range(n):
                a = 2 * math.pi * m / n + (k % 2) * math.pi / n
                x, y = rr * ax * math.cos(a), rr * ay * math.sin(a)
                if inner.contains(Point(x, y)):
                    out.append(Point(x, y).buffer(hole / 2, quad_segs=8))
            k += 1
    elif pat == "concentric":
        out.append(Point(0, 0).buffer(hole / 2, quad_segs=12))
        k = 1
        rib = max(1.2, p - hole)
        while True:
            rr = k * p
            if rr + hole / 2 > r_max - 0.2:
                break
            ring = Point(0, 0).buffer(rr + hole / 2, quad_segs=48).difference(Point(0, 0).buffer(rr - hole / 2, quad_segs=48))
            rot = 30.0 * (k % 2)
            for a in (90 + rot, 210 + rot, 330 + rot):
                bar = affinity.rotate(Polygon([(0, -rib / 2), (r_max + 5, -rib / 2), (r_max + 5, rib / 2), (0, rib / 2)]), a, origin=(0, 0))
                ring = ring.difference(bar)
            out += [gg for gg in getattr(ring, "geoms", [ring]) if gg.area > 1.0]
            k += 1
    else:  # slots
        j = 0
        nrow = int(h / p) + 1
        for j in range(-nrow, nrow + 1):
            y = j * p
            line = inner.intersection(Polygon([(-w, y - 0.01), (w, y - 0.01), (w, y + 0.01), (-w, y + 0.01)]))
            if line.is_empty:
                continue
            x0, _, x1, _ = line.bounds
            if x1 - x0 > hole * 1.2:
                out.append(affinity.translate(shapes2d.rounded_rect(x1 - x0 + hole, hole, hole / 2 - 0.01), (x0 + x1) / 2, y))
    return out


def kit_speaker(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path, grille: Optional[dict], tab_theta: float = 90.0):
    s = d["speaker"]
    ears = s.get("ears")
    ins, scr = fz["insert"], fz["screw"]
    lay0 = FP.speaker_layout(d, fz, -3.0)
    de, rp, wall_t, R_b, R_pad = lay0["de"], lay0["rp"], lay0["wall_t"], lay0["R_b"], lay0["R_pad"]
    zo, zi = S.disk(R_pad)
    z_m = _fin(zi, -3.0)
    # mount pad only under the cup wall and the three ring posts (not a full R_pad disc: on curved walls it gets deep
    # and would reach the next kit, REVIEW-1 BLK-1/MAJ-8)
    from shapely.ops import unary_union as _uu0

    angles0 = FP.tab_angles(float(tab_theta))
    pad2d = _uu0([Point(0, 0).buffer(lay0["cup_or"] + 1.0, quad_segs=32)] +
                 [Point(R_b * math.cos(math.radians(a)), R_b * math.sin(math.radians(a))).buffer(rp + 1.0, quad_segs=16) for a in angles0] +
                 [_LS0([(0, 0), (R_b * math.cos(math.radians(a)), R_b * math.sin(math.radians(a)))]).buffer(rp, quad_segs=8) for a in angles0])
    pad2d = pad2d.buffer(1.0, quad_segs=8).buffer(-1.0, quad_segs=8)
    kit.adds.append(_pad(F, O, poly_face(pad2d, spacing=0.8), z_m))
    gc = fz["gasket"]["compressed_t"]
    seat = 0.8
    z_s = z_m + seat - gc
    kit.cuts.append(F.place(ring_z(0, 0, z_m - 0.01, z_m + seat, de / 2 + 0.5, s["opening"] / 2 - 0.5)))
    # grille
    g = dict(grille or {"pattern": "hex_holes", "w": s["opening"], "h": s["opening"], "hole": 2.4, "pitch": 4.0})
    r_open = s["opening"] / 2 - 0.4
    holes = _grille_points(g, r_open)
    if grille and (float(grille["w"]) > s["opening"] + 0.5 or float(grille["h"]) > s["opening"] + 0.5):
        kit.warnings.append(f"grille {grille['w']:g} x {grille['h']:g} clipped to the {s['opening']:g} mm speaker opening")
    faces = [_hole_face(hh) for hh in holes]
    faces = [f for f in faces if f is not None]
    if faces:
        comp = cq.Compound.makeCompound([ff for f in faces for ff in f.Faces()])
        kit.cuts.append(F.place(prism(comp, z_m - 0.6, 30.0)))
    kit.grille_holes = len(faces)
    open_area = sum(hh.area for hh in holes)
    kit.info["grille_open_area_pct"] = round(100 * open_area / (math.pi * (s["opening"] / 2) ** 2), 1)
    # speaker model
    fl_t = s["flange_t"]
    spk = Part("PURCHASE_Speaker", "PURCHASE", d["name"], (0.10, 0.10, 0.11), ref=kit.ref, module_id=d["id"],
               feature_id=kit.feature_id, spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"),
               role="module")
    flange = ring_z(0, 0, z_s - fl_t, z_s, s["d"] / 2, s["opening"] / 2)
    if ears:
        for (hx, hy) in ears["holes"]:
            ear = box_z(hx * 0.75, hy, z_s - fl_t, z_s, abs(hx) * 0.5 + ears["w"] / 2, ears["w"]).fuse(
                cyl_z(hx, hy, z_s - fl_t, z_s, ears["w"] / 2)).cut(cyl_z(hx, hy, z_s - fl_t - 1, z_s + 1, ears["hole_d"] / 2))
            flange = flange.fuse(ear)
    spk.add("Frame", F.place(flange.clean()), (0.16, 0.16, 0.17))
    cone_back = z_s - s["basket_depth"] * 0.7
    spk.add("Cone", F.place(cone_z(0, 0, cone_back, z_s - 0.9, s["magnet_d"] / 2 * 0.7, s["opening"] / 2 - 0.2)), (0.07, 0.07, 0.075))
    spk.add("Basket", F.place(cone_z(0, 0, z_s - s["basket_depth"], cone_back, s["magnet_d"] / 2 + 1.0, s["magnet_d"] / 2 * 0.72)), (0.30, 0.30, 0.32))
    if s["depth"] > s["basket_depth"] + 0.2:
        spk.add("Magnet", F.place(cyl_z(0, 0, z_s - s["depth"], z_s - s["basket_depth"] + 0.01, s["magnet_d"] / 2)), (0.62, 0.63, 0.65))
    kit.parts.append(spk)
    gk = Part("PURCHASE_SpeakerGasket", "PURCHASE", f"foam gasket: {kit.label or kit.ref}", (0.20, 0.20, 0.22), host_ref=kit.ref,
              role="accessory", module_id="SPK_GASKET_FOAM_25X3_ADH", feature_id=kit.feature_id, spec_path=spec_path, mass_g=0.5)
    gk.notes.append("ring cut from the 3 mm adhesive foam strip, modelled compressed (estimate); physical check still needed")
    gk.add("Gasket", F.place(ring_z(0, 0, z_s, z_s + gc, s["d"] / 2 - 0.3, s["opening"] / 2 + 0.2)))
    kit.parts.append(gk)
    # cup wall + posts + ring clamp
    lay = FP.speaker_layout(d, fz, z_m)  # same numbers as the layout validator's footprint
    Lr, hd_r = lay["screw_len"], lay["hole_depth"]
    z_pe = lay["z_pe"]
    kit.adds.append(F.place(open_top(F, ring_z(0, 0, z_pe, z_m + 0.8, de / 2 + 0.5 + wall_t, de / 2 + 0.5), de / 2 + 0.5 + wall_t, z_pe, z_m + 0.8)))
    # tab triad rotation: chosen by footprint.speaker_rotation clear of every other kit (REVIEW-1 BLK-1)
    angles = FP.tab_angles(float(tab_theta))
    # slim clamp ring: an annulus just over the presser tube plus three tabs out to the posts
    ring_od = lay["ring_od"]
    ring_id = lay["ring_id"]
    ring2d = Point(0, 0).buffer(ring_od, quad_segs=48).difference(Point(0, 0).buffer(ring_id, quad_segs=48))
    tabs = []
    for a in angles:
        pu, pv = R_b * math.cos(math.radians(a)), R_b * math.sin(math.radians(a))
        from shapely.geometry import LineString as _LS

        tabs.append(_LS([(pu * (ring_od - 1.0) / R_b, pv * (ring_od - 1.0) / R_b), (pu, pv)]).buffer(rp, quad_segs=24))
    from shapely.ops import unary_union as _uu

    ring2d = _uu([ring2d] + tabs).buffer(0.6).buffer(-0.6)
    ring = prism(poly_face(ring2d, spacing=0.8), z_pe - 2.4, z_pe)
    tube_od = de / 2 - 0.3
    tube_id = s["opening"] / 2 + 0.4
    if tube_od - tube_id < 0.9:
        tube_id = tube_od - 0.9
    ring = ring.fuse(ring_z(0, 0, z_pe - 0.01, z_s - fl_t, tube_od, tube_id))
    for k, a in enumerate(angles):
        pu, pv = R_b * math.cos(math.radians(a)), R_b * math.sin(math.radians(a))
        kit.adds.append(F.place(cyl_z(pu, pv, z_pe, z_m + 0.8, rp)))
        kit.cuts.append(F.place(cyl_z(pu, pv, z_pe - 0.01, z_pe + hd_r, ins["hole_d"] / 2)))
        ring = ring.cut(cyl_z(pu, pv, z_pe - 5, z_pe + 1, scr["clear_d"] / 2))
        kit.fasteners.append(Fastener("insert", "HeatSetInserts", F.pt(pu, pv, z_pe), -F.n, ins["length"], ins["od"], f"{kit.ref} ring insert"))
        kit.fasteners.append(Fastener("screw", "ModuleScrews", F.pt(pu, pv, z_pe - 2.4), F.n, Lr, scr["d"], f"{kit.ref} ring screw",
                                      hole_depth=hd_r, joint=F.pt(pu, pv, z_pe), insert_len=ins["length"], clamp_t=2.4))
    # bridge the posts to the ring outline so the clamp is one solid
    ring = largest_solid(ring.clean())
    rp_part = Part("PRINT_SpeakerRing", "PRINT", f"speaker clamp ring: {kit.label or kit.ref}", colors["print_inner"], host_ref=kit.ref,
                   role="retainer", feature_id=kit.feature_id, spec_path=spec_path,
                   print_info={"frame": F, "up": "+z", "reason": "ring face on the bed, presser tube up; no supports"})
    rp_part.add("Ring", F.place(ring))
    kit.parts.append(rp_part)
    c = d["connector"]
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(c["c"][0], c["c"][1], z_s + c["z"]).tolist()
    kit.cable_end = (F.pt(c["c"][0] + 3.0, c["c"][1], z_s + c["z"]), -F.n)
    kit.info.update({"mount_face_z": round(z_m, 2), "flange_front_z": round(z_s, 2), "grille_pattern": g.get("pattern"),
                     "ring_tabs_deg": angles})
    a0 = math.radians(angles[0])
    pu, pv = R_b * math.cos(a0), R_b * math.sin(a0)
    kit.interfaces.append({"id": f"{kit.ref}.ring_post", "name": f"{kit.label or kit.ref}: clamp-ring post with M2.5 insert and the gasket seat",
                           "nominal": {"post_od_mm": round(2 * rp, 2), "insert_hole_d_mm": ins["hole_d"], "insert_hole_depth_mm": round(hd_r, 2),
                                       "seat_depth_mm": seat, "ring_screw": f"M2.5x{Lr:g}"},
                           "fit": "heat-set insert melt-in; the ring tab must close on the post with the gasket compressed",
                           "region": (F, (pu - rp - 4.0, pu + rp + 4.0, pv - rp - 4.0, pv + rp + 4.0, z_pe - 0.5, 1.5))})


def kit_snap_button(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path):
    b = d["snap_button"]
    rb = b["flange_d"] / 2 + 1.5
    zo, _ = S.disk(rb)
    z_sf = min(0.0, _fin(zo, 0.0))
    _, zi = S.disk(b["clip_w"] / 2 + 4)
    z_in = _fin(zi, -3.0)
    lo, hi = b["panel_t"]
    t_p = float(np.clip(3.0, lo, hi))
    z_pb = z_sf - t_p
    _spot_face(kit, F, circle_face(rb), z_sf)
    z_pad = min(z_in, z_pb - 1.5)
    # boss around the latch bore with a 45 deg underside (no flat ring ledge to print in mid-air; REVIEW-1 MAJ-9)
    r_bore = b["clip_w"] / 2 + 0.3
    r_out = b["clip_w"] / 2 + 4.0
    ch = r_out - (r_bore + 0.3)
    kit.adds.append(F.place(cyl_z(0, 0, z_pad + ch - 0.01, 40.0, r_out)))
    kit.adds.append(F.place(cone_z(0, 0, z_pad, z_pad + ch, r_bore + 0.3, r_out)))
    kit.cuts.append(F.place(cyl_z(0, 0, z_pad - 1.0, z_pb, b["clip_w"] / 2 + 0.3)))
    kit.cuts.append(F.place(cyl_z(0, 0, z_pb - 1.0, 30, b["hole_d"] / 2 + 0.15)))
    cap_col = colors["button"]
    m = Part("PURCHASE_Button", "PURCHASE", d["name"], cap_col, ref=kit.ref, module_id=d["id"], feature_id=kit.feature_id,
             spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"), role="module")
    bez = cyl_z(0, 0, z_sf, z_sf + b["flange_h"], b["flange_d"] / 2)
    bez, _ = try_fillet(bez, [e for e in bez.Edges() if abs(e.Center().z - (z_sf + b["flange_h"])) < 1e-3], b["flange_h"] * 0.6, min_r=0.3)
    m.add("Bezel", F.place(bez), cap_col)
    cap = cyl_z(0, 0, z_sf + b["flange_h"] - 0.2, z_sf + b["flange_h"] + b["cap_h"], b["cap_d"] / 2)
    cap, _ = try_fillet(cap, [e for e in cap.Edges() if e.Center().z > z_sf + b["flange_h"] + b["cap_h"] - 1e-3], 1.0, min_r=0.3)
    m.add("Plunger", F.place(cap), colors["button_cap"])
    m.add("Housing", F.place(cyl_z(0, 0, z_sf - b["body_len"] + 9.0, z_sf, b["hole_d"] / 2 - 0.4)), (0.85, 0.85, 0.83))
    m.add("Switch", F.place(box_z(0, 0, z_sf - b["body_len"], z_sf - b["body_len"] + 9.0, 12.8, 6.5)), (0.12, 0.12, 0.13))
    kit.parts.append(m)
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(0, 0, z_sf - b["body_len"]).tolist()
    kit.cable_end = (F.pt(0, -2.0, z_sf - b["body_len"]), -F.n)
    kit.info.update({"panel_thickness": round(t_p, 2), "hole_d": b["hole_d"] + 0.3, "spot_face": round(z_sf, 2)})


def kit_tactile(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path):
    pcb = d["pcb"]
    sw = d["switch"]
    cap_d = 10.0
    fl_r = cap_d / 2 + 1.6
    zo, _ = S.disk(cap_d / 2 + 2.5)
    z_sf = min(0.0, _fin(zo, 0.0))
    _spot_face(kit, F, circle_face(cap_d / 2 + 2.5), z_sf)
    _, zi = S.rect(pcb["w"] + 18, pcb["h"] + 4)
    z_in = _fin(zi, -3.0)
    z_pad = min(z_in, z_sf - 2.4)
    kit.adds.append(_pad(F, O, rounded_rect_face_fast(pcb["w"] + 2 * (0.6 + fz["insert"]["boss_d"]) + 4, pcb["h"] + 6, 3.0), z_pad))
    kit.cuts.append(F.place(cyl_z(0, 0, z_pad - 1.0, 30, cap_d / 2 + 0.3)))
    cap = cyl_z(0, 0, z_pad, z_sf + 1.2, cap_d / 2)
    cap, _ = try_fillet(cap, [e for e in cap.Edges() if e.Center().z > z_sf + 1.2 - 1e-3], 1.2, min_r=0.3)
    cap = cap.fuse(cyl_z(0, 0, z_pad - 1.2, z_pad, fl_r), cyl_z(0, 0, z_pad - 2.2, z_pad - 1.2, 2.0)).clean()
    cp = Part("PRINT_ButtonCap", "PRINT", f"printed plunger cap: {kit.label or kit.ref}", colors["button"], host_ref=kit.ref,
              role="retainer", feature_id=kit.feature_id, spec_path=spec_path,
              print_info={"frame": F, "up": "-z", "reason": "cap face on the bed for a smooth top; flange and stem up"})
    cp.add("Cap", F.place(largest_solid(cap)))
    kit.parts.append(cp)
    z_stem_top = z_pad - 2.2 - 0.3
    z_front = z_stem_top - sw["body_h"] - sw["stem_h"]
    sc = d["front"][0]["c"]
    center = (-sc[0], -sc[1])
    subs = board_model(F, pcb, center, z_front, d["front"], d["back"])
    m = Part("PURCHASE_Button", "PURCHASE", d["name"], pcb["color"], ref=kit.ref, module_id=d["id"], feature_id=kit.feature_id,
             spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"), role="module")
    for s in subs:
        m.add(*s)
    kit.parts.append(m)
    # standoffs from the pad to the board front keep the switch at the right distance from the cap
    board_clamp(kit, F, fz, pcb, center, z_front, z_pad, d["back"], "PRINT_ButtonClip", colors["print_inner"], standoffs=True, spec_path=spec_path,
                standoff_avoid=(0.0, 0.0, fl_r + 0.6))
    c = d["connector"]
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(center[0] + c["c"][0], center[1] + c["c"][1], z_front + c["z"]).tolist()
    kit.cable_end = (F.pt(center[0] + c["c"][0], center[1] + c["c"][1], z_front + c["z"]), -F.n)


def kit_led(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path, ring: bool):
    pcb = d["pcb"]
    if ring:
        od, idd = d["ring"]["od"], d["ring"]["id"]
        win = od - 6.0
        tube_id = od - 2.0
        flange_d = tube_id - 0.6
        gap = 6.0
    else:
        win = max(float(d["aperture"].get("w") or 8.0), 5.0)
        flange_d = win + 2.4
        tube_id = flange_d + 0.6
        gap = 3.0
    zo, zi = S.disk(tube_id / 2 + 4)
    z_in = _fin(zi, -3.0)
    z_pad = min(z_in, -2.0)
    # The diffuser's flange sits behind the pad plane, so the wall's inner surface must not reach behind z_pad anywhere
    # over the flange. On a curved face (a rounded top edge above the light) the inner surface dips behind the plane
    # near the flange's rim, where the samples above do not look (rings at r/2 and r; the outer ring can miss the
    # cavity altogether): the flange stood 0.27 mm in the wall (PRINT_Shell / PRINT_LedDiffuser 0.10-0.13 mm3, bench
    # p_a4avhj6v4pth / c_vs6fhhn2d6). The whole stack moves back to the deepest inner point over the flange (its rim
    # at 24 points); where the flange already clears nothing changes.
    _, zi_f = S.disk(flange_d / 2, n_ring=24)
    z_rim = min(z_pad, _fin(zi_f, z_pad))
    if z_rim < z_pad - 1e-6:
        kit.info["flange_moved_back_mm"] = round(z_pad - z_rim, 3)
        z_pad = z_rim
    kit.adds.append(_pad(F, O, rounded_rect_face_fast(max(pcb["w"], tube_id) + 2 * (0.6 + fz["insert"]["boss_d"]) + 6,
                                                      max(pcb["h"], tube_id) + 6, 4.0), z_pad))
    kit.cuts.append(F.place(cyl_z(0, 0, z_pad - 1.0, 30, win / 2)))
    plug = F.place(cyl_z(0, 0, z_pad, 30, win / 2 - 0.15)).intersect(O)
    flange = F.place(cyl_z(0, 0, z_pad - 1.2, z_pad, flange_d / 2))
    diff = largest_solid(plug.fuse(flange).clean())
    dp = Part("PRINT_LedDiffuser", "PRINT", f"light diffuser: {kit.label or kit.ref} (natural PETG)", colors["diffuser"], host_ref=kit.ref,
              role="retainer", feature_id=kit.feature_id, spec_path=spec_path, alpha=0.82,
              extra={"window_d_mm": round(win, 2), "lit_area_mm2": round(math.pi * (win / 2) ** 2, 1)}, print_info={"frame": F, "up": "-z", "reason": "outer face on the bed (smooth light face); print in natural PETG",
                                                           "material": "PETG natural"})
    dp.add("Diffuser", diff)
    kit.parts.append(dp)
    z_led_top = z_pad - 1.2 - gap
    led_h = 1.6
    z_front = z_led_top - led_h
    tube_od = max(tube_id + 4.0, (pcb["w"] if not ring else od) * (1.0 if ring else 1.42) + 0.5)
    kit.adds.append(F.place(open_top(F, ring_z(0, 0, z_front, z_pad + 0.8, tube_od / 2, tube_id / 2), tube_od / 2, z_front, z_pad + 0.8)))
    m = Part("PURCHASE_LedRing" if ring else "PURCHASE_Led", "PURCHASE", d["name"], pcb["color"], ref=kit.ref, module_id=d["id"],
             feature_id=kit.feature_id, spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"),
             role="module")
    if ring:
        m.add("Pcb", F.place(ring_z(0, 0, z_front - pcb["t"], z_front, od / 2, idd / 2)), pcb["color"])
        subs = board_model(F, {"w": 0.01, "h": 0.01, "t": 0.0, "color": pcb["color"]}, (0, 0), z_front, d["front"], [])[1:]
        for s in subs:
            m.add(*s)
        for it in d["back"]:
            c, sz = it["c"], it["size"]
            m.add(it["name"], F.place(box_z(c[0], c[1], z_front - pcb["t"] - sz[2], z_front - pcb["t"], sz[0], sz[1])), it["color"])
    else:
        for s in board_model(F, pcb, (0, 0), z_front, d["front"], d["back"]):
            m.add(*s)
    kit.parts.append(m)
    board_clamp(kit, F, fz, pcb, (0, 0), z_front, z_pad, d["back"], "PRINT_LedClip", colors["print_inner"], spec_path=spec_path)
    c = d["connector"]
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(c["c"][0], c["c"][1], z_front + c["z"]).tolist()
    kit.cable_end = (F.pt(c["c"][0], c["c"][1], z_front + c["z"]), -F.n)
    kit.info.update({"window_d": round(win, 2), "diffuser_gap": gap, "diffuser_d": round(win - 0.3, 2)})
    kit.interfaces.append({"id": f"{kit.ref}.diffuser_socket", "name": f"{kit.label or kit.ref}: diffuser socket and light tube",
                           "nominal": {"window_d_mm": round(win, 2), "plug_d_mm": round(win - 0.3, 2), "flange_d_mm": round(flange_d, 2),
                                       "tube_id_mm": round(tube_id, 2), "led_gap_mm": gap},
                           "fit": "0.15 mm per side press fit of the PETG diffuser plug",
                           "region": (F, (-tube_od / 2 - 3.0, tube_od / 2 + 3.0, -tube_od / 2 - 3.0, tube_od / 2 + 3.0, z_front - 0.5, 1.5))})


def kit_mic(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path):
    pcb = d["pcb"]
    port_d = max(float(d["aperture"].get("w") or 1.5), 1.2)
    _, zi = S.rect(pcb["w"] + 18, pcb["h"] + 4)
    z_in = _fin(zi, -3.0)
    z_pad = min(z_in, -2.0)
    kit.adds.append(_pad(F, O, rounded_rect_face_fast(pcb["w"] + 2 * (0.6 + fz["insert"]["boss_d"]) + 4, pcb["w"] + 2 * (0.6 + fz["insert"]["boss_d"]) + 4, 3.0), z_pad))
    kit.cuts.append(F.place(cyl_z(0, 0, z_pad - 0.6, 30, port_d / 2)))
    kit.cuts.append(F.place(ring_z(0, 0, z_pad - 0.01, z_pad + 0.5, 4.0, 1.5)))
    z_front = z_pad - 0.5
    gk = Part("PURCHASE_MicGasket", "PURCHASE", f"foam gasket: {kit.label or kit.ref}", (0.20, 0.20, 0.22),
              host_ref=kit.ref, role="accessory", module_id="SPK_GASKET_FOAM_25X3_ADH", feature_id=kit.feature_id, spec_path=spec_path, mass_g=0.1)
    gk.notes.append("ring cut from the 3 mm foam strip and modelled compressed to 1.0 mm (estimate); physical check still needed")
    gk.add("Gasket", F.place(ring_z(0, 0, z_front, z_pad + 0.5, 3.8, 1.6)))
    kit.parts.append(gk)
    m = Part("PURCHASE_Microphone", "PURCHASE", d["name"], d["pcb"]["color"], ref=kit.ref, module_id=d["id"], feature_id=kit.feature_id,
             spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"), role="module")
    for s in board_model(F, pcb, (0, 0), z_front, [], d["back"]):
        m.add(*s)
    kit.parts.append(m)
    board_clamp(kit, F, fz, pcb, (0, 0), z_front, z_pad, d["back"], "PRINT_MicClip", colors["print_inner"], standoffs=True, spec_path=spec_path)
    c = d["connector"]
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(c["c"][0], c["c"][1], z_front + c["z"]).tolist()
    kit.cable_end = (F.pt(c["c"][0], c["c"][1], z_front + c["z"]), -F.n)
    kit.info["port_d"] = port_d
    kit.interfaces.append({"id": f"{kit.ref}.port", "name": f"{kit.label or kit.ref}: mic port and gasket seat",
                           "nominal": {"port_d_mm": round(port_d, 2), "gasket_seat_od_mm": 8.0, "gasket_seat_depth_mm": 0.5},
                           "fit": "the gasket ring must seal the port with the board clamped",
                           "region": (F, (-pcb["w"] / 2 - 2.0, pcb["w"] / 2 + 2.0, -pcb["h"] / 2 - 2.0, pcb["h"] / 2 + 2.0, z_front - 0.5, 1.5))})


def kit_board(kit: Kit, F, S: Surf, O, d: dict, fz, colors, spec_path, window: Optional[dict], touch: bool = False):
    pcb = d["pcb"]
    _, zi = S.rect(pcb["w"] + 18, pcb["h"] + 4)
    z_in = _fin(zi, -3.0)
    name = {"touch": "PURCHASE_TouchPad", "sensor_window": "PURCHASE_Sensor"}.get(d["kind"], "PURCHASE_Module")
    m = Part(name, "PURCHASE", d["name"], pcb["color"], ref=kit.ref, module_id=d["id"], feature_id=kit.feature_id,
             spec_path=spec_path, mass_g=d.get("mass_g"), mass_source=d.get("mass_source", "estimate"), role="module")
    if touch:
        z_front = -1.5
        kit.adds.append(_pad(F, O, rounded_rect_face_fast(pcb["w"] + 6, pcb["h"] + 6, 2.0), min(z_in, -2.4)))
        kit.cuts.append(F.place(rrect_prism(0, 0, min(z_in, -2.4) - 1.0, z_front, pcb["w"] + 0.8, pcb["h"] + 0.8, 0.8)))
        kit.cuts.append(F.place(ring_z(0, 0, -0.4, 30, 8.0, 6.6)))
        for s in board_model(F, pcb, (0, 0), z_front, [], d.get("back", [])):
            m.add(*s)
        kit.parts.append(m)
        kit.info["touch_skin_mm"] = 1.5
    else:
        z_pad = min(z_in, -2.0)
        kit.adds.append(_pad(F, O, rounded_rect_face_fast(pcb["w"] + 2 * (0.6 + fz["insert"]["boss_d"]) + 4, pcb["h"] + 6, 3.0), z_pad))
        if window:
            kit.cuts.append(F.place(rrect_prism(0, 0, z_pad - 0.6, 30, window["w"], window["h"], min(window["w"], window["h"]) * 0.3)))
        z_front = z_pad - 0.3
        hf = max([it["size"][2] for it in d.get("front", [])] or [0.0])
        if hf > 0.25:
            kit.cuts.append(F.place(rrect_prism(0, 0, z_pad - 1.0, z_pad + hf + 0.3, min(pcb["w"] - 2, 8), min(pcb["h"] - 2, 8), 1.0)))
        for s in board_model(F, pcb, (0, 0), z_front, d.get("front", []), d.get("back", [])):
            m.add(*s)
        kit.parts.append(m)
        board_clamp(kit, F, fz, pcb, (0, 0), z_front, z_pad, d.get("back", []), "PRINT_SensorClip", colors["print_inner"], standoffs=True, spec_path=spec_path)
    c = d.get("connector", {"c": [0, 0], "z": -pcb["t"]})
    kit.anchors[f"{kit.ref}.CONN"] = F.pt(c["c"][0], c["c"][1], z_front + c["z"]).tolist()
    kit.cable_end = (F.pt(c["c"][0], c["c"][1], z_front + c["z"]), -F.n)


def speaker_theta(feat: dict, others: list, fz: dict, skin: float) -> tuple[float, dict]:
    """Clamp-ring tab triad rotation for a speaker feature (footprint.speaker_rotation; explicit feat['tab_rotation'] wins)."""
    if feat.get("tab_rotation") is not None:
        th = float(feat["tab_rotation"])
        return th, {"theta": th, "angles": FP.tab_angles(th), "source": "spec"}
    r = FP.speaker_rotation(feat, feat.get("desc"), others or [], fasteners=fz, skin=skin)
    r["source"] = "chosen clear of the other kits"
    return float(r["theta"]), r


def build_kit(feat: dict, desc: dict, ref: str, probeO, probeC, O, fz, colors, spec_path: str, others: Optional[list] = None,
              skin: float = 2.4) -> Kit:
    face = feat.get("face", "front")
    F = feature_frame(probeO, face, feat["at"], float(feat.get("rotate", 0)))
    kit = Kit(feat["id"], desc["kind"], ref, F)
    kit.label = str(feat.get("label") or desc.get("name") or ref)
    if F is None:
        kit.warnings.append(f"feature {feat['id']} at {list(feat['at'])} on face {face} misses the body; skipped")
        return kit
    S = Surf(F, probeO, probeC)
    k = desc["kind"]
    if k == "display":
        kit_display(kit, F, S, O, desc, fz, colors, spec_path)
    elif k == "speaker":
        th, why = speaker_theta(feat, others or [], fz, skin)
        kit.info["ring_tabs_choice"] = {k_: v_ for k_, v_ in why.items() if k_ != "angles"}
        kit_speaker(kit, F, S, O, desc, fz, colors, spec_path, feat.get("grille"), tab_theta=th)
    elif k == "snap_button":
        kit_snap_button(kit, F, S, O, desc, fz, colors, spec_path)
    elif k == "tactile":
        kit_tactile(kit, F, S, O, desc, fz, colors, spec_path)
    elif k in ("led", "led_ring"):
        kit_led(kit, F, S, O, desc, fz, colors, spec_path, ring=(k == "led_ring"))
    elif k == "mic":
        kit_mic(kit, F, S, O, desc, fz, colors, spec_path)
    elif k == "touch":
        kit_board(kit, F, S, O, desc, fz, colors, spec_path, None, touch=True)
    elif kits_ext is not None and kits_ext.kit_fn(k) is not None:  # pack kits (cad/kits_*.py), before the generic clamp
        kits_ext.kit_fn(k)(kit, F, S, O, desc, fz, colors, spec_path)
    else:
        if k not in ("sensor", "sensor_window"):
            kit.warnings.append(f"feature {feat['id']}: module kind '{k}' has no dedicated mount kit; held by a generic clamp")
        kit_board(kit, F, S, O, desc, fz, colors, spec_path, desc.get("window") or {"w": 4.0, "h": 4.0})
    for p in kit.parts:
        if p.kind in ("PURCHASE", "PRINT"):
            kit.keepouts.append(p.shape)
    return kit
