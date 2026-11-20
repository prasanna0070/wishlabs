"""Motion pack kit (CATALOG-PLAN package B): a hobby servo behind the shell wall, its output shaft through an 8 mm hole,
the horn supplied with the servo on the shaft outside, and a printed arm (or flap) screwed to the horn.

Kit kind "servo", chosen for library rows whose aperture.needs is "shaft_hole" (library/packs/motion.json) and for
the role "servo" (modules.ROLE_KIND). Kit frame as in features.py: origin on the outer surface at features[].at,
+z outward, u right, v up, turned by the feature's rotate.

- The servo BODY is centred on features[].at, so normalize's face_mm box (32.2 x 12.2 mm across the tabs) and this
  kit's keep-out describe the same space; the output shaft sits shaft_off mm (5.9 for an SG90) toward local +u.
- The servo's top face sits against the inner wall; two printed bosses take the self-tapping screws supplied with the
  servo through its mounting tabs (screwed from inside before the rear cover goes on).
- The printed arm (40 x 8 x 3 mm, hub 10 mm) or flap (feature label containing "flap": 40 x 30 x 2 mm) points local
  +v (up) at rest and turns parallel to the face. It is named PRINT_ServoArm_<feature id>.

Light-engine rule: this module imports no CadQuery at import time (kit_servo imports it), so normalize can use the
footprint in the web image. Every dimension is from library rows and the parts scout: nothing here was measured on a
part (horn hub and plate sizes, tab holes and screw size are UNVERIFIED; physical check still needed).
"""

from __future__ import annotations

import copy
import math
from typing import Optional

# Servo bodies (mm). body = [length across the case, width, height from the base to the case top]; tab_z = base to the
# underside of the mounting tabs; shaft_off = output shaft from the case centre along the length; spline_h = shaft
# above the case top. Sources: library SERVO_SG90_TOWERPRO dims notes (32.2 across the tabs, 16 mm base to the tab
# line, 31 mm base to the top of the horn) and cad/refs_provisional.py "sg90"; MG90S body 22.4 x 12.5 x 22.8 mm from
# the parts scout row 4, tabs and shaft taken as SG90 (UNVERIFIED).
SG90 = {"body": [22.8, 12.2, 22.7], "tab_span": 32.2, "tab_t": 2.5, "tab_z": 15.9, "shaft_off": 5.9, "spline_d": 4.8,
        "spline_h": 4.0, "hole_pitch": 27.8, "hole_d": 2.0, "color": [0.16, 0.36, 0.78],
        "source": "SG90: library SERVO_SG90_TOWERPRO dims notes + refs_provisional sg90"}
SERVO_GEOM: dict[str, dict] = {
    "SERVO_SG90_ARM": SG90,
    "SERVO_SG90_TOWERPRO": SG90,
    "SERVO_MG90S_ARM": {**SG90, "body": [22.4, 12.5, 22.8], "color": [0.10, 0.10, 0.11],
                        "source": "MG90S body from the parts scout row 4; tabs, holes and shaft as SG90 (UNVERIFIED)"},
    "SERVO_FS90R_CONT": {**SG90, "body": [22.5, 12.1, 22.7], "color": [0.10, 0.10, 0.11],
                         "source": "FS90R body length and width from the parts scout row 12; height, tabs and shaft as SG90 (UNVERIFIED)"},
}
# Single-arm horn from the servo's own bag (library SERVO_HORN_KIT_SG90_SUPPLIED: horn sizes are not dimensioned there).
HORN = {"hub_d": 7.0, "hub_z0": 0.6, "top": 4.4, "plate_t": 1.6, "plate_len": 17.0, "plate_w": 5.5, "screw_holes_v": [8.0, 12.0],
        "source": "estimate: SG90 single-arm horn, not dimensioned in the library (UNVERIFIED)"}
ARM = {"len": 40.0, "w": 8.0, "t": 3.0, "hub_d": 10.0, "base": 5.0}  # base = arm length behind the shaft axis
FLAP = {"len": 40.0, "w": 30.0, "t": 2.0, "hub_d": 10.0, "base": 5.0}
HOLE_D = 8.0  # shaft hole through the wall (horn hub 7 mm passes; hub size UNVERIFIED)
R_SPOT = 11.5  # outer spot face radius around the shaft on curved faces (the horn plate turns inside it)
GAP = 0.1  # servo top to the inner wall
PLATE_CLEAR = 0.3  # horn plate above the outer surface
R_BOSS = 2.3  # tab boss radius (holes 13.9 mm from the case centre, case half-length 11.4: 0.2 mm left)
PILOT_D, PILOT_DEPTH = 1.6, 6.0  # pilot hole for the supplied self-tapping tab screws (screw size UNVERIFIED)
SWEEP_DEG = 90.0  # the arm turns at most this far either side of its rest pose (servo 0..180 around 90)


def is_flap(label: Optional[str]) -> bool:
    return "flap" in str(label or "").lower()


def servo_geometry(desc: dict) -> dict:
    """Servo body geometry for a CAD descriptor (by module id, else the SG90 values scaled to the row's dims)."""
    g = SERVO_GEOM.get(str(desc.get("id") or ""))
    if g is not None:
        return copy.deepcopy(g)
    g = copy.deepcopy(SG90)
    dims = desc.get("dims") or {}
    if dims.get("y"):
        g["body"][1] = float(dims["y"])
    g["source"] = f"{desc.get('id')}: not in SERVO_GEOM; SG90 values used (UNVERIFIED)"
    g["provisional"] = True
    return g


def describe_servo(d: dict, entry: dict) -> None:
    """DESCRIBE hook: servo, horn and connector geometry on the CAD descriptor."""
    g = servo_geometry(d)
    d["servo"] = g
    d["horn"] = copy.deepcopy(HORN)
    L, W, H = g["body"]
    d["connector"] = {"c": [-L / 2 - 1.0, 0.0], "z": -(H - 3.0), "label": "3-wire servo lead (PH-3 to servo-plug adapter)"}
    if g.get("provisional"):
        d["provisional_fields"].append("servo body")
    d["provisional_fields"].append("servo horn size")


def _arm_dims(label: Optional[str]) -> dict:
    return dict(FLAP if is_flap(label) else ARM)


def sweep_radius() -> float:
    """Farthest point of the arm or flap from the shaft (either style)."""
    return max(math.hypot(a["len"] - a["base"], a["w"] / 2) for a in (ARM, FLAP))


def servo_boxes(desc: dict, skin: float = 2.4) -> list[dict]:
    """FOOTPRINTS hook: local keep-out boxes (kits_ext contract) for a flat wall `skin` thick. Matches kit_servo with the
    servo top GAP behind the inner wall; on curved faces the CAD seats the kit on a flat pad, slightly deeper."""
    g = desc.get("servo") or servo_geometry(desc)
    L, W, H = g["body"]
    so, span = g["shaft_off"], g["tab_span"]
    tab_top = H - g["tab_z"] - g["tab_t"]  # case top to the top face of the tabs
    d_top = skin + GAP  # depth of the servo's top face
    out = [
        {"part": "PURCHASE_Servo", "kind": "module", "what": "servo case", "u": [-L / 2, L / 2], "v": [-W / 2, W / 2],
         "depth": [d_top, d_top + H]},
        {"part": "PURCHASE_Servo", "kind": "module", "what": "servo mounting tabs", "u": [-span / 2, span / 2], "v": [-W / 2, W / 2],
         "depth": [d_top + tab_top, d_top + tab_top + g["tab_t"]]},
        {"part": "PURCHASE_Servo", "kind": "module", "what": "servo lead exit", "u": [-L / 2 - 6.0, -L / 2], "v": [-3.0, 3.0],
         "depth": [d_top + H - 7.0, d_top + H]},
    ]
    for sgn in (-1, 1):
        pu = sgn * g["hole_pitch"] / 2
        out.append({"part": "PRINT_Shell", "kind": "boss", "what": f"tab boss {'-+'[sgn > 0]}", "u": [pu - R_BOSS, pu + R_BOSS],
                    "v": [-R_BOSS, R_BOSS], "depth": [skin - 0.8, d_top + tab_top]})
    # outside: the horn and the printed arm or flap turn up to SWEEP_DEG either side of local +v (rest pose)
    R = sweep_radius()
    top = HORN["top"] + max(ARM["t"], FLAP["t"])
    out.append({"part": "PRINT_ServoArm", "kind": "clearance", "what": "servo arm sweep (outside the shell)",
                "u": [so - R, so + R], "v": [-max(ARM["w"], FLAP["w"]) / 2 - 0.5, R], "depth": [-(top - d_top), -(HORN["top"] - HORN["plate_t"] - d_top)]})
    return out


def kit_servo(kit, F, S, O, desc: dict, fz: dict, colors: dict, spec_path: str) -> None:
    """KITS hook: pad, tab bosses, shaft hole (+ spot face on curved faces), the servo with its horn, the printed arm."""
    import numpy as np

    from .features import _fin, _pad
    from .geom import box_z, circle_face, cyl_z, largest_solid, prism, rounded_rect_face_fast, stadium_face
    from .parts import Part

    g = desc.get("servo") or servo_geometry(desc)
    hn = desc.get("horn") or HORN
    L, W, H = g["body"]
    so, span, tab_t = g["shaft_off"], g["tab_span"], g["tab_t"]
    tab_top = H - g["tab_z"] - tab_t
    flap = is_flap(kit.label)
    arm = _arm_dims(kit.label)

    # ---- surfaces: outer around the shaft, outer over the case, inner behind the whole kit
    uv = [(so, 0.0)] + [(so + rr * math.cos(a), rr * math.sin(a)) for rr in (R_SPOT * 0.5, R_SPOT)
                        for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
    zo_s, _ = S.sample(uv)
    z_sf = min(0.0, _fin(zo_s, 0.0))
    zo_b, _ = S.rect(L, W)
    z_ob = min(0.0, _fin(zo_b, 0.0))
    _, zi = S.rect(span + 6.0, W + 6.0)
    z_in = _fin(zi, -3.0)
    z_pad = min(z_in, -2.0)
    plate_bot = hn["top"] - hn["plate_t"]
    z_top = z_pad - GAP
    need = z_sf + PLATE_CLEAR - plate_bot  # lowest servo top that keeps the horn plate off the outer surface
    raised = 0.0
    if z_top < need:
        raised = need - z_top
        z_top = need
        kit.cuts.append(F.place(box_z(0.0, 0.0, z_pad - 1.0, z_top + GAP, L + 0.3, W + 0.3)))  # pocket: thinner wall over the case
        if z_ob - z_top < 1.2:
            kit.warnings.append(f"feature {kit.feature_id}: the face curves too much for a servo here (wall over the servo "
                                f"{z_ob - z_top:.1f} mm); move it to a flatter part of the face")
    if z_sf < -0.05:
        kit.cuts.append(F.place(prism(circle_face(R_SPOT, so, 0.0), z_sf, 40.0)))
    z_tt = z_top - tab_top  # top face of the tabs
    z_tb = z_tt - tab_t

    # ---- shell: pad, tab bosses with pilot holes, shaft hole
    kit.adds.append(_pad(F, O, rounded_rect_face_fast(span + 6.0, W + 6.0, 3.0), z_pad))
    for sgn in (-1, 1):
        pu = sgn * g["hole_pitch"] / 2
        kit.adds.append(F.place(cyl_z(pu, 0.0, z_tt, z_pad + 0.8, R_BOSS)))
        kit.cuts.append(F.place(cyl_z(pu, 0.0, z_tt - 0.01, z_tt + PILOT_DEPTH, PILOT_D / 2)))
        kit.anchors[f"{kit.ref}.tab{1 if sgn < 0 else 2}"] = F.pt(pu, 0.0, z_tb).tolist()
    kit.cuts.append(F.place(cyl_z(so, 0.0, z_top - 0.5, 40.0, HOLE_D / 2)))

    # ---- the servo (case, tabs, spline) and the horn from its bag, one purchased component
    col = tuple(g.get("color") or (0.16, 0.36, 0.78))
    m = Part("PURCHASE_Servo", "PURCHASE", desc["name"], col, ref=kit.ref, module_id=desc["id"], feature_id=kit.feature_id,
             spec_path=spec_path, mass_g=desc.get("mass_g"), mass_source=desc.get("mass_source", "estimate"), role="module")
    m.add("Case", F.place(box_z(0.0, 0.0, z_top - H, z_top, L, W)), col)
    tabs = box_z(0.0, 0.0, z_tb, z_tt, span, W)
    for sgn in (-1, 1):
        tabs = tabs.cut(cyl_z(sgn * g["hole_pitch"] / 2, 0.0, z_tb - 1.0, z_tt + 1.0, g["hole_d"] / 2))
    m.add("Tabs", F.place(tabs), col)
    m.add("Spline", F.place(cyl_z(so, 0.0, z_top, z_top + g["spline_h"], g["spline_d"] / 2)), (0.92, 0.92, 0.90))
    m.add("Lead", F.place(box_z(-L / 2 - 2.5, 0.0, z_top - H + 1.0, z_top - H + 5.0, 5.0, 3.6)), (0.45, 0.25, 0.12))
    horn_col = (0.95, 0.95, 0.93)
    m.add("HornHub", F.place(cyl_z(so, 0.0, z_top + hn["hub_z0"], z_top + hn["top"], hn["hub_d"] / 2)), horn_col)
    pl = hn["plate_len"] + hn["plate_w"] / 2
    m.add("HornPlate", F.place(prism(stadium_face(pl, hn["plate_w"], so, (hn["plate_len"] - hn["plate_w"] / 2) / 2, angle=90.0),
                                     z_top + plate_bot, z_top + hn["top"])), horn_col)
    m.notes.append("horn = the single-arm horn supplied with the servo; its size is an estimate (physical check still needed)")
    kit.parts.append(m)

    # ---- printed arm or flap on the horn, pointing local +v at rest
    z0, z1 = z_top + hn["top"], z_top + hn["top"] + arm["t"]
    vc = (arm["len"] / 2) - arm["base"]  # centre of the arm along v (the shaft at v = 0)
    if flap:
        body2d = rounded_rect_face_fast(arm["w"], arm["len"], 3.0, so, vc)
    else:
        body2d = stadium_face(arm["len"], arm["w"], so, vc, angle=90.0)
    a = prism(body2d, z0, z1).fuse(cyl_z(so, 0.0, z0, z1, arm["hub_d"] / 2))
    a = a.cut(cyl_z(so, 0.0, z0 - 1.0, z1 + 1.0, 1.3))  # horn centre screw access
    for hv in hn["screw_holes_v"]:
        a = a.cut(cyl_z(so, hv, z0 - 1.0, z1 + 1.0, 0.8))  # horn arm screws (supplied with the servo)
    a = largest_solid(a.clean())
    what = "flap" if flap else "arm"
    pa = Part(f"PRINT_ServoArm_{kit.feature_id}", "PRINT", f"printed servo {what}: {kit.label or kit.ref}", colors.get("button", col),
              host_ref=kit.ref, role="retainer", feature_id=kit.feature_id, spec_path=spec_path,
              extra={"style": what, "size_mm": [arm["w"], arm["len"], arm["t"]], "rest_pose": "points up (local +v)",
                     "sweep_deg": SWEEP_DEG},
              print_info={"frame": F, "up": "+z", "reason": "flat face on the bed (the face that sits on the horn); no supports"})
    pa.add("Arm", F.place(a))
    kit.parts.append(pa)

    # ---- cable, interfaces, info
    c = desc.get("connector") or {"c": [-L / 2 - 1.0, 0.0], "z": -(H - 3.0)}
    end = F.pt(c["c"][0], c["c"][1], z_top + c["z"])
    kit.anchors[f"{kit.ref}.CONN"] = end.tolist()
    kit.cable_end = (end, -F.n)
    kit.interfaces.append({"id": f"{kit.ref}.shaft_hole", "name": f"{kit.label or kit.ref}: servo shaft hole, tab bosses and printed {what}",
                           "nominal": {"shaft_hole_d_mm": HOLE_D, "horn_hub_d_mm_estimate": hn["hub_d"], "boss_d_mm": 2 * R_BOSS,
                                       "pilot_hole_d_mm": PILOT_D, "pilot_depth_mm": PILOT_DEPTH, "boss_pitch_mm": g["hole_pitch"],
                                       "arm_mm": [arm["w"], arm["len"], arm["t"]]},
                           "fit": "servo top against the inner wall, two supplied self-tapping screws through the tabs into the "
                                  "bosses; the horn hub turns freely in the hole; screw and horn sizes UNVERIFIED (physical check)",
                           "region": (F, (-span / 2 - 3.0, span / 2 + 3.0, -W / 2 - 3.0, W / 2 + 3.0, z_tb - 0.5, 1.5))})
    kit.info["servo"] = {"style": what, "shaft_offset_mm": so, "servo_top_z": round(z_top, 2), "pad_z": round(z_pad, 2),
                         "spot_face": round(z_sf, 2), "raised_mm": round(raised, 2), "geometry_source": g.get("source"),
                         "horn_source": hn.get("source"), "sweep_deg": SWEEP_DEG}


KITS = {"servo": kit_servo}
CLASSIFY = {"shaft_hole": "servo"}
FOOTPRINTS = {"servo": servo_boxes}
DESCRIBE = {"servo": describe_servo}
