"""Spec, CAD placement parameters, anchors and cable routes for the Moon Hug dev fixture.

Module sizes come from the library `dims_mm`; the cartridge (board outline, parts, module plans, holes, posts)
comes from library/backplane-v1.json placed at spec.derived.cartridge.board_origin (written by the real
gstudio.normalize).
"""
from __future__ import annotations

import copy
import math

import numpy as np

from fx_lib import IDS, dims

# ------------------------------------------------------------------ spec (scripted fallback_spec, library ids filled)
FALLBACK_OUTLINE = [[-60, 0], [60, 0], [78, 40], [70, 120], [40, 170], [0, 182], [-40, 170], [-70, 120], [-78, 40]]

SPEC_CHANGES = [
    "face_plate: at [0,120] size 80 -> at [0,128] size 76 (room for the 30 mm button bezel below it)",
    "eyes: z 128 -> 136; mouth: z 112 -> 120 (follow the face plate)",
    "night_light: z 100 -> 104 (inside the face plate, below the smile)",
    "story_button: z 80 -> 70 (library hole is 30 mm, bezel 33 mm)",
    "speaker: z 40 -> 30, grille h 34 -> 30 (the 40 mm speaker clears the button body)",
    "mic: [0,64] -> [-28,70] (beside the button; [0,64] fell inside the button bezel)",
]


def make_spec() -> dict:
    return {
        "schema": "gadget-spec/1", "name": "Moon Hug", "slug": "moon-hug",
        "tagline": "A crescent moon that tells bedtime stories",
        "audience": {"child_directed": True, "age_years": 5}, "region": "IN",
        "colors": {"body": "#8CC9C0", "accent": "#F4F1EA", "detail": "#2B3A42", "body_name": "pale teal"},
        "body": {"family": "silhouette", "outline": copy.deepcopy(FALLBACK_OUTLINE), "depth": 120, "edge_round": 12,
                 "wall": 2.4, "split": "rear_cover", "feet": 4},
        "decor": [
            {"type": "face_plate", "shape": "circle", "at": [0, 128], "size": [76, 76], "depth": 1.2},
            {"type": "eye", "style": "arc_closed", "at": [-16, 136], "size": [9, 9], "relief": "engrave", "depth": 0.8},
            {"type": "eye", "style": "arc_closed", "at": [16, 136], "size": [9, 9], "relief": "engrave", "depth": 0.8},
            {"type": "mouth", "style": "smile", "at": [0, 120], "size": [20, 7], "relief": "engrave", "depth": 0.8}],
        "features": [
            {"id": "story_button", "module": IDS["button"], "face": "front", "at": [0, 70], "label": "Story button"},
            {"id": "night_light", "module": IDS["led"], "face": "front", "at": [0, 104],
             "label": "Night light and listening light"},
            {"id": "speaker", "module": IDS["speaker"], "face": "front", "at": [0, 30],
             "grille": {"pattern": "hex_holes", "w": 70, "h": 30, "hole": 2.6, "pitch": 4.4}},
            {"id": "mic", "module": IDS["mic"], "face": "front", "at": [-28, 70], "label": "Microphone hole"}],
        "electronics": {"core": IDS["core"], "extra_modules": [], "power": {"input": "usb_c_5v", "adapter_in_box": False}},
        "behaviour": {"template": "story_player",
                      "summary": "Tap: a bedtime story starring her favourite character. Hold: say a new favourite.",
                      "settings": {}},
        "safety": {"flags": ["CHILD_DIRECTED", "LOCAL_VOICE_CAPTURE"],
                   "notes": "Child-directed: needs a safety review before sale as a built device."},
    }


# ------------------------------------------------------------------ fixed layout numbers (mm)
D = 120.0
WALL = 2.4
SKIN = 3.2              # front skin: face plate (1.2) + eye engraving (0.8) still leaves 1.2 mm
SPLIT_Y = 57.0          # the rear cover is the last 3 mm
REAR_ROUND_MAX = 6.0    # smaller rear round so the cover reads as a cap (same rule as the CAD stage)
YF = -D / 2
INNER_FRONT = YF + SKIN

COLORS = {
    "PRINT_Shell": "#8CC9C0", "PRINT_RearCover": "#8CC9C0", "PRINT_SpeakerRing": "#F4F1EA",
    "PURCHASE_Backplane": "#4B55C2", "PURCHASE_CoreBoard": "#25282D", "PURCHASE_UsbC": "#B8BCC2",
    "PURCHASE_Amp": "#7A4FB5", "PURCHASE_SdCard": "#2B7FB8", "PURCHASE_Speaker": "#34363A",
    "PURCHASE_Button": "#F2C14E", "PURCHASE_Led": "#F28C6B", "PURCHASE_Mic": "#6A4BA0",
    "PURCHASE_Feet": "#D6D8D4", "PURCHASE_CoverScrews": "#A9AEB5", "PURCHASE_BoardScrews": "#A9AEB5",
    "PURCHASE_Inserts": "#C9A227",
    "REFERENCE_CableButton": "#9AA0A6", "REFERENCE_CableMic": "#E2B93B", "REFERENCE_CableLed": "#2A9D8F",
    "REFERENCE_CableSpeaker": "#D1495B",
}


class Board:
    """backplane-v1 frame (origin lower-left, top view, +X robot's left, +Y rear) placed in gadget mm."""

    def __init__(self, bp: dict, origin: list[float]):
        self.bp = bp
        self.w, self.h = (float(v) for v in bp["board"]["size"])
        self.t = float(bp["board"].get("thickness", 1.6))
        self.x0, self.y0, self.zb = (float(v) for v in origin)
        self.top = self.zb + self.t
        self.slots = {s["id"]: s for s in bp["slots"]}
        self.parts = {p["ref"]: p for p in bp["parts"]}
        self.env = {e["id"]: e for e in bp["cartridge"].get("envelopes", [])}

    def g(self, bx, by, z=None):
        return [round(self.x0 + bx, 3), round(self.y0 + by, 3), round(self.top if z is None else z, 3)]

    def plan_box(self, plan, z0, z1, shrink=0.0):
        x0, x1, y0, y1 = plan
        return _box(self.x0 + x0 + shrink, self.x0 + x1 - shrink, self.y0 + y0 + shrink, self.y0 + y1 - shrink, z0, z1)

    def slot_plan(self, sid: str):
        s = self.slots[sid]
        if s.get("courtyard"):
            return s["courtyard"]
        for k in ("devkit_pcb_plan", "module_plan"):
            if s.get(k):
                return s[k]
        return None

    def anchor(self, sid: str) -> list[float]:
        s = self.slots[sid]
        if sid == "J_PWR":
            c = self.bp["cartridge"]["usb_c_opening"]
            return self.g(c["center_board_x"], self.h, self.top + c["center_above_board_top"])
        plan = self.slot_plan(sid)
        cx, cy = (plan[0] + plan[1]) / 2, (plan[2] + plan[3]) / 2
        if s.get("kind") in ("core", "module_socket"):
            return self.g(cx, cy, self.top + 11.0)
        return self.g(cx, cy, self.top + 6.0)


def _box(x0, x1, y0, y1, z0, z1, fillet=None):
    d = {"t": "box", "min": [round(x0, 3), round(y0, 3), round(z0, 3)], "max": [round(x1, 3), round(y1, 3), round(z1, 3)]}
    if fillet:
        d["fillet"] = fillet
    return d


def _cyl(c, axis, r, h):
    return {"t": "cyl", "c": [round(float(v), 3) for v in c], "axis": axis, "r": r, "h": h}


PART_HEIGHT = {"JST_PH": 6.0, "PinSocket": 8.5, "USB_C": 3.2, "Fuse": 1.6, "D_SMA": 2.3, "SOT-23": 1.1, "C_1206": 1.6,
               "C_0805": 1.25, "C_0603": 0.9, "R_0603": 0.5}


def _part_height(fp: str) -> float:
    for k, v in PART_HEIGHT.items():
        if k in fp:
            return v
    return 1.0


# ------------------------------------------------------------------ CAD params
def make_params(spec: dict, mods: dict, board: Board, used_slots: list[str], cover_screws: list[dict]) -> dict:
    feats = {f["id"]: f for f in spec["features"]}
    btn, led, spk, mic = feats["story_button"], feats["night_light"], feats["speaker"], feats["mic"]
    bdx, _, bdz = dims(mods[IDS["button"]])              # 33 bezel, 38 behind the panel
    ap_btn = mods[IDS["button"]]["aperture"]["size_mm"]["w"]  # 30 mm hole
    ldx, ldy, _ = dims(mods[IDS["led"]])                 # 10 x 10
    ap_led = mods[IDS["led"]]["aperture"]["size_mm"]
    sdx, _, sdz = dims(mods[IDS["speaker"]])             # 40, 17.5
    mdx, mdy, _ = dims(mods[IDS["mic"]])                 # 14 x 14
    ap_mic = mods[IDS["mic"]]["aperture"]["size_mm"]["w"]
    fx, _, fz = dims(mods[IDS["feet"]])                  # 7.9 x 2.2
    ins = dims(mods[IDS["inserts"]])                     # 4.6 x 4.6 x 4.0
    b, bp = board, board.bp
    top = b.top
    mz0 = top + 11.0                                     # elevated module bottom (backplane height envelopes)

    parts: dict = {}
    # ---- backplane PCB with the parts this design populates (ports of other slots are left off)
    prims = [_box(b.x0, b.x0 + b.w, b.y0, b.y0 + b.h, b.zb, top)]
    for ref, p in b.parts.items():
        slot = p.get("slot")
        if p.get("side", "top") != "top" or ref.startswith(("H", "TP")) or ref == "J_PWR":
            continue
        if slot and slot not in used_slots:
            continue
        cy = p.get("courtyard")
        if not cy:
            continue
        h = _part_height(p.get("footprint", ""))
        prims.append(b.plan_box(cy, top, top + h, shrink=0.45 if "SMD" not in p.get("footprint", "") else 0.25))
    parts["PURCHASE_Backplane"] = {"prims": prims}
    pw = b.parts["J_PWR"]["courtyard"]
    parts["PURCHASE_UsbC"] = {"prims": [_box(b.x0 + pw[0] + 0.85, b.x0 + pw[1] - 0.85, b.y0 + pw[2] + 1.6, b.y0 + b.h + 0.3, top, top + 3.2)]}

    # ---- DevKitC-1 seated in its sockets, PCB 11 mm above the board top (backplane height envelope)
    u1 = b.slots["U1"]
    px0, px1, py0, py1 = u1["devkit_pcb_plan"]
    ax0, ax1, ay0, ay1 = u1["antenna_plan"]
    cp = [b.plan_box([px0, px1, py0, py1], mz0, mz0 + 1.6),
          b.plan_box([ax0, ax0 + 18.0, py0 + 0.0, py1], mz0 + 1.6, mz0 + 2.4),
          b.plan_box([ax0 + 6.6, ax0 + 17.4, py0 + 3.9, py1 - 3.9], mz0 + 2.4, mz0 + 4.6),
          b.plan_box([px1 - 6.6, px1 + 1.0, py0 + 3.0, py0 + 10.5], mz0 + 1.6, mz0 + 4.6),
          b.plan_box([px1 - 6.6, px1 + 1.0, py1 - 10.5, py1 - 3.0], mz0 + 1.6, mz0 + 4.6)]
    for ref in u1.get("parts", []):
        c = b.parts[ref]["courtyard"]
        cp.append(b.plan_box([c[0] + 0.6, c[1] - 0.6, c[2] + 0.9, c[3] - 0.9], top + 8.5, mz0))   # male-header spacers
    parts["PURCHASE_CoreBoard"] = {"prims": cp}

    # ---- amp and microSD module lying flat on their pin sockets
    a1 = b.slots["A1"]["module_plan"]
    ap = [b.plan_box(a1, mz0, mz0 + 1.6), b.plan_box([a1[0] + 7.0, a1[0] + 10.0, a1[2] + 8.0, a1[2] + 11.0], mz0 + 1.6, mz0 + 2.5)]
    for ref in b.slots["A1"].get("parts", []):
        c = b.parts[ref]["courtyard"]
        ap.append(b.plan_box([c[0] + 0.6, c[1] - 0.6, c[2] + 0.9, c[3] - 0.9], top + 8.5, mz0))
    parts["PURCHASE_Amp"] = {"prims": ap}
    s1 = b.slots["S1"]["module_plan"]
    sp = [b.plan_box(s1, mz0, mz0 + 1.6),
          b.plan_box([s1[0], s1[0] + 15.0, s1[2] + 4.5, s1[3] - 4.5], mz0 + 1.6, mz0 + 3.4),
          b.plan_box([s1[1] - 14.0, s1[1] - 9.0, s1[2] + 6.0, s1[2] + 10.0], mz0 + 1.6, mz0 + 3.0)]
    for ref in b.slots["S1"].get("parts", []):
        c = b.parts[ref]["courtyard"]
        sp.append(b.plan_box([c[0] + 0.6, c[1] - 0.6, c[2] + 0.9, c[3] - 0.9], top + 8.5, mz0))
    parts["PURCHASE_SdCard"] = {"prims": sp}

    # ---- speaker (library: flange OD 40, 11.3 to the back of the basket, 17.5 overall, magnet OD 23)
    cx, cz = spk["at"]
    yfl = INNER_FRONT + 0.2
    parts["PURCHASE_Speaker"] = {"prims": [
        _cyl((cx, yfl, cz), "y", sdx / 2, 1.7),
        {"t": "cone", "c": [cx, yfl + 1.7, cz], "axis": "y", "r0": 18.0, "r1": 12.0, "h": round(11.3 - 1.7, 3)},
        _cyl((cx, yfl + 11.3, cz), "y", 23.0 / 2, round(sdz - 11.3, 3)),
        _box(cx + 15.6, cx + 17.6, yfl + 3.0, yfl + 6.5, cz - 1.5, cz + 1.5)]}
    parts["PRINT_SpeakerRing"] = {"prims": [
        {"t": "ring", "c": [cx, INNER_FRONT, cz], "axis": "y", "ro": 22.0, "ri": 20.4, "h": 3.4},
        {"t": "ring", "c": [cx, INNER_FRONT + 1.9, cz], "axis": "y", "ro": 22.0, "ri": 18.0, "h": 1.5}], "fuse": True}

    # ---- button (library: 30 mm hole, 33 mm bezel, 38 mm behind the panel)
    bx_, bz_ = btn["at"]
    parts["PURCHASE_Button"] = {"prims": [
        _cyl((bx_, YF - 4.0, bz_), "y", bdx / 2, 4.0),
        _cyl((bx_, YF - 7.0, bz_), "y", 12.0, 3.0),
        _cyl((bx_, YF, bz_), "y", ap_btn / 2 - 0.5, 24.0),
        _box(bx_ - 10.0, bx_ + 10.0, YF + 24.0, YF + bdz, bz_ - 3.2, bz_ + 3.2)]}

    # ---- LED module behind its window, mic module behind its port
    lx, lz = led["at"]
    parts["PURCHASE_Led"] = {"prims": [
        _box(lx - ldx / 2, lx + ldx / 2, INNER_FRONT + 1.6, INNER_FRONT + 3.2, lz - ldy / 2, lz + ldy / 2),
        _box(lx - 2.5, lx + 2.5, INNER_FRONT, INNER_FRONT + 1.6, lz - 2.5, lz + 2.5)]}
    mx, mz = mic["at"]
    parts["PURCHASE_Mic"] = {"prims": [
        _box(mx - 2.4, mx + 2.4, INNER_FRONT, INNER_FRONT + 1.0, mz - 1.9, mz + 1.9),
        _box(mx - mdx / 2, mx + mdx / 2, INNER_FRONT + 1.0, INNER_FRONT + 2.6, mz - mdy / 2, mz + mdy / 2),
        _box(mx - 6.0, mx + 6.0, INNER_FRONT + 2.6, INNER_FRONT + 5.1, mz - mdy / 2 + 0.5, mz - mdy / 2 + 3.0)]}

    # ---- feet, screws, inserts
    feet_xy = [[sx * 48.0, sy * 38.0] for sx in (-1, 1) for sy in (-1, 1)]
    recess = 0.8
    parts["PURCHASE_Feet"] = {"prims": [_cyl((x, y, recess - fz), "z", fx / 2, fz) for x, y in feet_xy]}
    cart = bp["cartridge"]
    holes_xy = [[round(b.x0 + h["at"][0], 3), round(b.y0 + h["at"][1], 3)] for h in cart["holes"]]
    head_d = float(cart["boss"].get("screw_head_d", 4.6))
    bs = []
    for x, y in holes_xy:
        bs += [_cyl((x, y, top), "z", head_d / 2, 2.5), _cyl((x, y, top - 6.0), "z", 1.25, 6.0)]
    parts["PURCHASE_BoardScrews"] = {"prims": bs}
    cb_depth = 1.5
    csp = []
    for s in cover_screws:
        x, z = s["xz"]
        csp += [_cyl((x, D / 2 - cb_depth, z), "y", head_d / 2, 2.5), _cyl((x, D / 2 - cb_depth - 8.0, z), "y", 1.25, 8.0)]
    parts["PURCHASE_CoverScrews"] = {"prims": csp}
    ins_p = [_cyl((x, y, b.zb - ins[2]), "z", ins[0] / 2, ins[2]) for x, y in holes_xy]
    ins_p += [_cyl((s["xz"][0], SPLIT_Y - ins[2], s["xz"][1]), "y", ins[0] / 2, ins[2]) for s in cover_screws]
    parts["PURCHASE_Inserts"] = {"prims": ins_p}

    posts = [{"xy": [round(b.x0 + p["board_hole"]["at"][0], 3), round(b.y0 + p["board_hole"]["at"][1], 3)],
              "d": p["post_d"], "top_z": round(top + p["top_above_board_top"], 3), "pilot_d": p.get("pilot_hole_d", 1.8)}
             for p in cart.get("support_posts", [])]
    usb = cart["usb_c_opening"]
    return {
        "schema": "gadget-cad-params/1", "fixture": True, "slug": spec["slug"],
        "note": "DEV FIXTURE parameters (Gadget Studio web dev fixture), not pipeline output.",
        "body": {"outline": spec["body"]["outline"], "depth": D, "edge_round": spec["body"]["edge_round"],
                 "rear_round_max": REAR_ROUND_MAX, "wall": WALL, "front_skin": SKIN, "split_y": SPLIT_Y, "floor_z": WALL},
        "decor": spec["decor"],
        "apertures": [
            {"feature": "story_button", "kind": "round", "at": btn["at"], "d": ap_btn},
            {"feature": "night_light", "kind": "rrect", "at": led["at"], "w": ap_led["w"], "h": ap_led["h"], "r": 1.5},
            {"feature": "mic", "kind": "round", "at": mic["at"], "d": ap_mic},
            {"feature": "speaker", "kind": "grille", "at": spk["at"], **{k: spk["grille"][k] for k in ("w", "h", "hole", "pitch")}}],
        "backplane_mount": {"holes_xy": holes_xy, "boss_d": cart["boss"]["od"], "bottom_z": b.zb,
                            "insert_hole_d": cart["boss"]["insert_hole_d"], "insert_depth": cart["boss"]["insert_hole_depth"],
                            "posts": posts},
        "cover_screws": {"positions": cover_screws, "boss_d": 7.2, "boss_y0": 42.0, "rib_w": 2.4, "insert_hole_d": 4.0,
                         "insert_depth": 5.0, "clear_d": 2.9, "counterbore_d": 5.2, "counterbore_depth": cb_depth},
        "usb_opening": {"at": [round(b.x0 + usb["center_board_x"], 3), round(top + usb["center_above_board_top"], 3)],
                        "w": usb["w"], "h": usb["h"], "r": usb["corner_r"]},
        "feet": {"xy": feet_xy, "recess_d": 8.6, "recess_depth": recess},
        "parts": parts,
        "colors": COLORS,
        "print_pose": {"PRINT_Shell": {"rot_x": 90, "note": "front face down on the bed; the rounded front edge needs supports from the build plate"},
                       "PRINT_RearCover": {"rot_x": -90, "note": "outer rear face down on the bed"},
                       "PRINT_SpeakerRing": {"rot_x": 90, "note": "flat face down, no supports"}},
    }


# ------------------------------------------------------------------ anchors and cable routes
def make_anchors(params: dict, board: Board, spec: dict) -> dict:
    a = {}
    for sid in board.slots:
        a[f"BP1.{sid}"] = board.anchor(sid)
    for h in board.bp["cartridge"]["holes"]:
        a[f"BP1.{h['id']}"] = board.g(*h["at"])
    for tp in board.bp.get("test_pads") or []:
        a[f"BP1.{tp['ref']}"] = board.g(tp["at"][0], tp["at"][1], board.zb)
    feats = {f["id"]: f for f in spec["features"]}
    bx, bz = feats["story_button"]["at"]
    lx, lz = feats["night_light"]["at"]
    mx, mz = feats["mic"]["at"]
    sx, sz = feats["speaker"]["at"]
    u1 = board.slots["U1"]
    px0, px1, py0, py1 = u1["devkit_pcb_plan"]
    mz0 = board.top + 11.0
    a1 = board.slots["A1"]["module_plan"]
    s1 = board.slots["S1"]["module_plan"]
    a.update({
        "U1.USB": board.g(px1, (py0 + py1) / 2, mz0 + 3.0),
        "U1.ANTENNA": board.g(u1["antenna_plan"][0] + 3.0, (py0 + py1) / 2, mz0 + 2.0),
        "AMP1.VO": board.g(*board.parts["J_A1P"]["at"], mz0 + 1.6),
        "AMP1.HEADER": board.g((a1[0] + a1[1]) / 2, a1[2] + 2.5, mz0 + 1.6),
        "SD1.CARD_SLOT": board.g(s1[0], (s1[2] + s1[3]) / 2, mz0 + 2.5),
        "SD1.HEADER": board.g(*board.parts["J_S1"]["at"], mz0 + 1.6),
        "SPK1.CONN": [round(sx + 16.6, 3), round(INNER_FRONT + 0.2 + 4.8, 3), sz],
        "BTN1.CONN": [bx, round(YF + 38.0, 3), bz],
        "LED1.CONN": [lx, round(INNER_FRONT + 3.2, 3), round(lz - 4.0, 3)],
        "MIC1.CONN": [mx, round(INNER_FRONT + 5.1, 3), round(mz - 5.25, 3)],
        "SHELL.USB_OPENING": [params["usb_opening"]["at"][0], D / 2, params["usb_opening"]["at"][1]],
    })
    for i, s in enumerate(params["cover_screws"]["positions"], 1):
        a[f"COVER.S{i}"] = [s["xz"][0], D / 2, s["xz"][1]]
    for i, (x, y) in enumerate(params["feet"]["xy"], 1):
        a[f"FEET.F{i}"] = [x, y, -1.4]
    return {k: [round(float(v), 3) for v in p] for k, p in a.items()}


def _smooth(points, n=8):
    """Centripetal Catmull-Rom through the polyline (ends kept), sampled n per segment."""
    P = [np.array(p, float) for p in points]
    if len(P) < 3:
        return np.array(P)
    P = [P[0] + (P[0] - P[1])] + P + [P[-1] + (P[-1] - P[-2])]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]

        def tj(ti, a, b):
            return ti + max(np.linalg.norm(b - a), 1e-6) ** 0.5
        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        for t in np.linspace(t1, t2, n, endpoint=False):
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    out.append(P[-2])
    return np.array(out)


# name, instance ref, from anchor, to anchor, bundle diameter, waypoints (gadget mm) between the anchors
CABLES = [
    ("REFERENCE_CableButton", "BTN1", "BP1.J_IO1", "BTN1.CONN", 3.0, "relative", [[0, 0, 14], [6, -2, 34], [14, -3, 48]]),
    ("REFERENCE_CableMic", "MIC1", "BP1.J_MIC", "MIC1.CONN", 3.6, "absolute",
     [[None, None, 30], [-34, 0, 34], [-35, -30, 50], [-30, -46, 62]]),
    ("REFERENCE_CableLed", "LED1", "BP1.J_LED", "LED1.CONN", 3.0, "absolute",
     [[None, None, 32], [15, -24, 62], [13, -32, 90], [6, -44, 100]]),
    ("REFERENCE_CableSpeaker", "SPK1", "BP1.J_SPK", "SPK1.CONN", 2.4, "absolute",
     [[None, None, 32], [22, -20, 32], [20, -38, 30]]),
]


def make_routes(anchors: dict, harness: dict) -> list[dict]:
    """harness: {instance ref: derived.harness entry (cable text, pins, length_mm)} from the normalized spec."""
    routes = []
    for name, ref, a, bnd, dia, mode, mid in CABLES:
        p0 = anchors[a]
        pts = [p0]
        for w in mid:
            if mode == "relative":
                pts.append([p0[0] + w[0], p0[1] + w[1], p0[2] + w[2]])
            else:
                pts.append([p0[i] if w[i] is None else w[i] for i in range(3)])
        pts.append(anchors[bnd])
        sm = _smooth(pts, 10)
        L = float(np.sum(np.linalg.norm(np.diff(sm, axis=0), axis=1)))
        h = harness.get(ref) or {}
        routes.append({"id": name, "ref": ref, "from": a, "to": bnd, "cable": h.get("cable") or "JST-PH", "pins": h.get("pins"),
                       "diameter": dia, "length_mm": h.get("length_mm"), "route_length_mm": round(L, 1),
                       "points": [[round(float(v), 2) for v in p] for p in sm], "waypoints": pts, "display_only": True})
    return routes


def tube_bbox(points, dia):
    P = np.array(points)
    r = dia / 2
    return [float(P[:, 0].min() - r), float(P[:, 0].max() + r), float(P[:, 1].min() - r), float(P[:, 1].max() + r),
            float(P[:, 2].min() - r), float(P[:, 2].max() + r)]


def place_cover_screws(opoly, r_rear: float) -> list[dict]:
    """Four cover screws on the rear face's flat area (inset rear round + 3 mm), each with a rib to the wall."""
    from shapely.geometry import Point
    inset = r_rear + 3.5
    q = opoly.buffer(-inset)
    if q.geom_type != "Polygon":
        q = max(q.geoms, key=lambda g: g.area)
    out = []
    for tx, tz in ((62, 26), (-62, 26), (40, 150), (-40, 150)):
        p = q.exterior.interpolate(q.exterior.project(Point(tx, tz)))
        w = opoly.exterior.interpolate(opoly.exterior.project(p))
        dx, dz = w.x - p.x, w.y - p.y
        L = math.hypot(dx, dz)
        out.append({"xz": [round(p.x, 2), round(p.y, 2)], "rib_dir": [round(dx / L, 4), round(dz / L, 4)], "rib_len": round(L + 1.0, 2)})
    return out
