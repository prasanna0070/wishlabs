"""Core cartridge: backplane-v1 PCB with its sockets and ports, the ESP32-S3 devkit, socketed amp and microSD,
4 floor bosses with M2.5 heat-set inserts, and the 2 printed microSD support posts.

Board frame (backplane-v1.json): origin at the board lower-left corner in top view, +x = world +X, +y = world +Y
(rear). World = (x0 + bx, y0 + by, z_bottom + bz) with z_bottom = floor + board_bottom_above_floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cadquery as cq
import numpy as np

from . import refs_provisional as P
from .geom import V, box_z, cyl_z, rounded_rect_face_fast, prism
from .modules import pick_screw, screw_length
from .parts import Fastener, Part

JST_COLOR = (0.93, 0.91, 0.84)
SOCKET = (0.07, 0.07, 0.08)
SMD = (0.15, 0.12, 0.10)


@dataclass
class Cartridge:
    x0: float
    y0: float
    floor: float
    w: float
    h: float
    t: float
    parts: list = field(default_factory=list)
    adds: list = field(default_factory=list)  # bosses/posts united into the floor part
    cuts: list = field(default_factory=list)
    fasteners: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)
    slot_ends: dict = field(default_factory=dict)  # slot id -> (point, direction) where the plug's cable leaves
    keepout: tuple = ()
    info: dict = field(default_factory=dict)

    @property
    def zb(self):
        return self.floor + 6.4

    @property
    def zt(self):
        return self.floor + 6.4 + self.t

    def W(self, bx, by, bz=0.0):
        return np.array([self.x0 + bx, self.y0 + by, self.zt + bz])


def _slots(bp: dict) -> dict:
    s = bp.get("slots") or {}
    return {x["id"]: x for x in s} if isinstance(s, list) else s


def plan_box(c: Cartridge, plan, z0, z1, shrink=0.0):
    x0, x1, y0, y1 = plan
    return box_z(c.x0 + (x0 + x1) / 2, c.y0 + (y0 + y1) / 2, c.zt + z0, c.zt + z1, max(x1 - x0 - 2 * shrink, 0.2),
                 max(y1 - y0 - 2 * shrink, 0.2))


def keepout_plan(bp: dict):
    cart = bp.get("cartridge") or {}
    kp = cart.get("keepout_plan_board")
    if kp:
        return kp, float(cart.get("keepout_height_above_floor", 32.0)), float(cart.get("clearance_mm", 3.0))
    b = bp.get("board") or {}
    w, h = (b.get("size") or [bp.get("board", {}).get("w", 92), bp.get("board", {}).get("h", 62)])[:2]
    return [-3.0, w + 3.0, 0, h + 0.3], 32.0, 3.0


def board_size(bp: dict):
    b = bp.get("board") or {}
    if "size" in b:
        return float(b["size"][0]), float(b["size"][1]), float(b.get("thickness", 1.6))
    return float(b.get("w", 92)), float(b.get("h", 62)), float(b.get("t", 1.6))


def usb_face_by(bp: dict) -> float:
    s = _slots(bp).get("J_PWR") or {}
    cy = s.get("courtyard")
    if cy:
        return float(cy[3])
    return board_size(bp)[1] + 0.3


def build_cartridge(bp: dict, x0: float, y0: float, floor: float, fz: dict, colors: dict, refs: dict, core_desc: dict,
                    internal: list, spec_paths: dict) -> Cartridge:
    W_, H_, T_ = board_size(bp)
    c = Cartridge(x0, y0, floor, W_, H_, T_)
    slots = _slots(bp)
    bpref = refs.get("backplane", "BP1")
    board_col = tuple((bp.get("board") or {}).get("color", (0.05, 0.30, 0.26))) if isinstance((bp.get("board") or {}).get("color"), (list, tuple)) else (0.05, 0.30, 0.26)
    # ---------------------------------------------------------------- backplane PCB + parts
    bpp = Part("PURCHASE_Backplane", "PURCHASE", "Backplane PCB backplane-v1 (assembled)", board_col, ref=bpref,
               module_id="BACKPLANE_V1", spec_path=None, mass_g=18.0, mass_source="estimate")
    pcb = prism(rounded_rect_face_fast(W_, H_, float((bp.get("board") or {}).get("corner_radius", 1.0)), W_ / 2, H_ / 2), 0, T_)
    holes = [h for h in (bp.get("board") or {}).get("holes", [])]
    if holes:
        pcb = pcb.cut(*[cyl_z(h["at"][0], h["at"][1], -1, T_ + 1, h["d"] / 2) for h in holes])
    bpp.add("Pcb", pcb.translate(V(x0, y0, c.zb)), board_col)
    port_heights = {"jst": 6.0, "socket": 8.5}
    for p in bp.get("parts") or []:
        cy = p.get("courtyard")
        if not cy or p.get("side") == "bottom" or p.get("role") == "mounting_hole":
            continue
        fp = (p.get("footprint") or "")
        role = p.get("role")
        if "PinSocket" in fp:
            sh = plan_box(c, cy, 0, port_heights["socket"], 0.4)
            bpp.add(p["ref"], sh, SOCKET)
        elif "JST" in fp:
            bpp.add(p["ref"], plan_box(c, cy, 0, port_heights["jst"], 0.5), JST_COLOR)
        elif "USB_C" in fp:
            ux0, ux1, uy0, uy1 = cy
            cxu = (ux0 + ux1) / 2
            bpp.add(p["ref"], box_z(x0 + cxu, y0 + uy1 - 3.675, c.zt, c.zt + 3.26, 8.94, 7.35), P.METAL)
        elif role == "support":
            bpp.add(p["ref"], plan_box(c, cy, 0, 1.2, 0.6), SMD)
    c.parts.append(bpp)
    # ---------------------------------------------------------------- devkit on sockets
    u1 = slots.get("U1") or {}
    plan = u1.get("devkit_pcb_plan") or [0.935, 63.8, 43.0, 68.4]
    env = u1.get("height_envelope_mm") or {}
    zdk = float(env.get("devkit_bottom_above_board_top", 11.0))
    dk = core_desc.get("devkit") or {}
    core = Part("PURCHASE_CoreBoard", "PURCHASE", core_desc.get("name", "ESP32-S3 devkit"), (0.08, 0.08, 0.09),
                ref=refs.get("core", "U1"), module_id=core_desc.get("id"), spec_path="electronics.core",
                mass_g=core_desc.get("mass_g") or 7.0, mass_source=core_desc.get("mass_source", "estimate"))
    core.add("Pcb", plan_box(c, plan, zdk, zdk + 1.6), (0.08, 0.08, 0.09))
    ant = u1.get("antenna_plan") or [plan[0] - 6.285, plan[0], plan[2] + 3.7, plan[3] - 3.7]
    mod_len = float((dk.get("module") or [18, 25.5, 3.1])[1])
    mx0 = ant[0]
    core.add("Module", plan_box(c, [mx0, mx0 + mod_len, ant[2], ant[3]], zdk + 1.6, zdk + 2.4), (0.10, 0.20, 0.45))
    core.add("Shield", plan_box(c, [mx0 + 6.8, mx0 + mod_len - 0.6, ant[2] + 0.3, ant[3] - 0.3], zdk + 2.4, zdk + 4.7), P.METAL)
    ux = plan[1]
    for k, yy in enumerate((plan[2] + 6.6, plan[3] - 6.6)):
        core.add(f"Usb{k + 1}", box_z(x0 + ux - 2.6, y0 + yy, c.zt + zdk + 1.6, c.zt + zdk + 4.6, 7.4, 8.4), P.METAL)
    core.add("Buttons", plan_box(c, [ux - 12, ux - 8.5, plan[2] + 3, plan[3] - 3], zdk + 1.6, zdk + 3.1), (0.12, 0.12, 0.13))
    for p in bp.get("parts") or []:
        if p.get("slot") == "U1" and "PinSocket" in (p.get("footprint") or ""):
            core.add("Header" + p["ref"][-1], plan_box(c, p["courtyard"], 8.5, zdk, 0.45), (0.06, 0.06, 0.07))
    c.parts.append(core)
    c.anchors[f"{refs.get('core', 'U1')}.USB"] = c.W(ux + 0.5, (plan[2] + plan[3]) / 2, zdk + 3.0).tolist()
    # ---------------------------------------------------------------- socketed modules
    for im in internal:
        s = slots.get(im["slot"]) or {}
        mp = s.get("module_plan")
        if not mp:
            continue
        e = s.get("height_envelope_mm") or {}
        zb = float(e.get("module_bottom_above_board_top", 11.0))
        d = im["desc"]
        nm = "PURCHASE_Amplifier" if im["slot"] == "A1" else "PURCHASE_MicroSdModule"
        part = Part(nm, "PURCHASE", d.get("name", im["module"]), d.get("pcb", {}).get("color", P.PCB_BLUE), ref=im["ref"],
                    module_id=im["module"], spec_path=None, mass_g=d.get("mass_g") or (2.0 if im["slot"] == "A1" else 4.0),
                    mass_source=d.get("mass_source", "estimate"))
        part.add("Pcb", plan_box(c, mp, zb, zb + 1.6), d.get("pcb", {}).get("color", P.PCB_BLUE))
        cxm, cym = (mp[0] + mp[1]) / 2, (mp[2] + mp[3]) / 2
        if im["slot"] == "A1":
            part.add("Chip", plan_box(c, [cxm - 1.5, cxm + 1.5, cym - 1.5, cym + 1.5], zb + 1.6, zb + 2.5), P.CHIP)
        else:
            sx = mp[0] + 2.0
            part.add("CardSocket", plan_box(c, [sx, sx + 15.0, cym - 7.2, cym + 7.2], zb + 1.6, zb + 3.5), P.METAL)
            part.add("Card", plan_box(c, [sx - 2.0, sx + 13.0, cym - 5.5, cym + 5.5], zb + 2.0, zb + 2.8), (0.12, 0.12, 0.16))
            part.add("Ldo", plan_box(c, [cxm + 6, cxm + 12.5, cym - 2, cym + 2], zb + 1.6, zb + 3.2), P.CHIP)
        for p in bp.get("parts") or []:
            if p.get("slot") == im["slot"] and "PinSocket" in (p.get("footprint") or ""):
                part.add("Header" + p["ref"][-1], plan_box(c, p["courtyard"], 8.5, zb, 0.45), (0.06, 0.06, 0.07))
        c.parts.append(part)
        c.anchors[f"{im['ref']}.SOCKET"] = c.W(cxm, cym, zb + 1.6).tolist()
    # ---------------------------------------------------------------- floor bosses + inserts + screws
    cart = bp.get("cartridge") or {}
    boss = cart.get("boss") or {}
    ins, scr = fz["insert"], fz["screw"]
    od = max(float(boss.get("od", ins["boss_d"])), ins["boss_d"])
    hd = float(boss.get("insert_hole_d", ins["hole_d"]))
    hdep = float(boss.get("insert_hole_depth", ins["hole_depth"]))
    mh = [h for h in (cart.get("holes") or holes) if str(h.get("id", "")).startswith("H")] or [{"id": f"H{i+1}", "at": a, "d": 2.7} for i, a in enumerate(P.BACKPLANE["holes"])]
    L, hdep = pick_screw(T_, scr["lengths"], hdep)
    for h in mh:
        bx, by = h["at"]
        X, Y = x0 + bx, y0 + by
        c.adds.append(cyl_z(X, Y, floor - 0.5, c.zb, od / 2))
        c.cuts.append(cyl_z(X, Y, c.zb - hdep, c.zb + 0.01, hd / 2))
        c.fasteners.append(Fastener("insert", "HeatSetInserts", np.array([X, Y, c.zb]), np.array([0, 0, -1.0]), ins["length"], ins["od"],
                                    f"cartridge insert {h['id']}"))
        c.fasteners.append(Fastener("screw", "CartridgeScrews", np.array([X, Y, c.zt]), np.array([0, 0, -1.0]), L, scr["d"],
                                    f"cartridge screw {h['id']}", hole_depth=hdep, joint=np.array([X, Y, c.zb]),
                                    insert_len=ins["length"], clamp_t=T_))
        c.anchors[f"{bpref}.{h['id']}"] = [X, Y, c.zt]
    # microSD support posts through the board NPTH holes
    for sp in cart.get("support_posts") or []:
        if not any(im["slot"] == "S1" for im in internal):
            break
        bx, by = sp["board_hole"]["at"]
        top = c.zt + float(sp.get("top_above_board_top", 11.0))
        c.adds.append(cyl_z(x0 + bx, y0 + by, floor - 0.5, top, float(sp.get("post_d", 4.0)) / 2))
        c.cuts.append(cyl_z(x0 + bx, y0 + by, top - 5.0, top + 0.01, float(sp.get("pilot_hole_d", 1.8)) / 2))
    # ---------------------------------------------------------------- slot anchors (cable exits)
    for sid, s in slots.items():
        if s.get("kind") == "port" and s.get("courtyard"):
            x_0, x_1, y_0, y_1 = s["courtyard"]
            top = c.zt + 6.0
            pt = c.W((x_0 + x_1) / 2, (y_0 + y_1) / 2, 6.0)
            c.anchors[f"{bpref}.{sid}"] = pt.tolist()
            c.slot_ends[sid] = (c.W((x_0 + x_1) / 2, (y_0 + y_1) / 2, 6.0 + 3.6), np.array([0, 0, 1.0]))
        elif sid == "J_PWR":
            cy = s.get("courtyard") or [26.08, 36.72, 69.48, 78.9]
            c.anchors[f"{bpref}.J_PWR"] = c.W((cy[0] + cy[1]) / 2, cy[3], 1.6).tolist()
    kp, kh, cl = keepout_plan(bp)
    c.keepout = (x0 + kp[0], x0 + kp[1], y0 + kp[2], y0 + kp[3], floor, floor + kh)
    c.info = {"board_origin": [round(x0, 3), round(y0, 3), round(c.zb, 3)], "floor_z": round(floor, 3), "board_top_z": round(c.zt, 3),
              "board_size": [W_, H_], "keepout_box": [round(v, 2) for v in c.keepout], "clearance_mm": cl}
    return c
