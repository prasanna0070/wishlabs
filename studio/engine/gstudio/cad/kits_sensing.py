"""Sensing pack kit (CATALOG-PLAN package C): a strain-relieved cable exit for a part that lives OUTSIDE the shell,
the capacitive soil probe in a plant pot or the float switch in a water tank (library/packs/sensing.json,
aperture.needs "cable_exit"). Water and soil stay outside; only the lead comes in.

Kit kind "cable_exit". Kit frame as in features.py: origin on the outer surface at features[].at, +z outward, u right,
v up, turned by the feature's rotate. Layout (local mm):
- a flat pad on the inner wall, PAD_W x PAD_H, centred on features[].at;
- the lead hole, HOLE_D across, through the pad and the wall at (0, HOLE_V);
- two printed posts below the hole at u = +/-POST_U, v = POST_V, each POST x POST across and POST_H tall, with one
  cable-tie slot running along u through both: the lead comes in through the hole, turns down between the posts and a
  tie through the slots pins it to the pad, so a pull on the lead outside never reaches the port inside.
The CONN anchor (where the internal harness ends) is at the inner mouth of the hole.

Light-engine rule: no CadQuery at import time (kit_cable_exit imports it), so normalize can use the footprint in the web
image. Dimensions are design choices for a 3-core lead about 3-4 mm across (the probe and float leads are UNVERIFIED);
the hole, posts and tie fit are a physical check.
"""

from __future__ import annotations

PAD_W, PAD_H = 20.0, 12.0  # = FOOTPRINTS / pack row merge.face_mm [20, 12]
DEPTH = 15.0  # keep-out depth from the outer surface (pack row merge.depth_mm 15)
HOLE_D = 6.5  # lead exit hole (CATALOG-PLAN: 6.5 mm)
HOLE_V = 2.5  # hole centre above features[].at
POST = 3.0  # post section (square)
POST_U = 5.0  # post centres at u = +/-POST_U (7 mm gap between them for the lead)
POST_V = -3.0  # post centres below the hole
POST_H = 6.0  # post height from the pad, inward
TIE_W, TIE_T = 3.0, 1.6  # cable-tie slot: TIE_W along z (tie width 2.5 mm), TIE_T along v (tie thickness about 1 mm)
TIE_Z = 3.5  # slot centre, inward from the pad


def exit_boxes(desc: dict, skin: float = 2.4) -> list[dict]:
    """FOOTPRINTS hook: local keep-out boxes (kits_ext contract) for a wall `skin` thick."""
    pad_back = skin + 0.5
    return [
        {"part": "PRINT_Shell", "kind": "pad", "what": "cable exit pad", "u": [-PAD_W / 2, PAD_W / 2],
         "v": [-PAD_H / 2, PAD_H / 2], "depth": [skin - 0.8, pad_back]},
        {"part": "PRINT_Shell", "kind": "boss", "what": "strain-relief posts and tie",
         "u": [-POST_U - POST / 2, POST_U + POST / 2], "v": [POST_V - POST / 2, POST_V + POST / 2],
         "depth": [pad_back, pad_back + POST_H]},
        {"part": "PURCHASE_Lead", "kind": "module", "what": "lead bend behind the exit (soil probe or float lead)",
         "u": [-HOLE_D / 2, HOLE_D / 2], "v": [-PAD_H / 2, HOLE_V + HOLE_D / 2], "depth": [pad_back, DEPTH]},
    ]


def describe_cable_exit(d: dict, entry: dict) -> None:
    """DESCRIBE hook: the exit geometry on the CAD descriptor (the part itself is outside the shell)."""
    d["cable_exit"] = {"hole_d": HOLE_D, "hole_v": HOLE_V, "pad": [PAD_W, PAD_H], "posts": {"u": POST_U, "v": POST_V,
                       "size": POST, "h": POST_H}, "tie_slot": [TIE_W, TIE_T],
                       "outside": "the part sits outside the shell; only its lead enters"}
    d.setdefault("provisional_fields", []).append("lead diameter and tie fit")


def kit_cable_exit(kit, F, S, O, desc: dict, fz: dict, colors: dict, spec_path: str) -> None:
    """KITS hook: pad, lead hole through the wall, two posts with a cable-tie slot; anchor at the inner hole mouth."""
    from .features import _fin, _pad
    from .geom import box_z, cyl_z, rounded_rect_face_fast

    _, zi = S.rect(PAD_W, PAD_H)
    z_in = _fin(zi, -3.0)
    z_pad = min(z_in, -2.0)  # the flat inner face of the pad (local z, negative = inside)
    kit.adds.append(_pad(F, O, rounded_rect_face_fast(PAD_W, PAD_H, 3.0), z_pad))
    for su in (-1.0, 1.0):
        kit.adds.append(F.place(box_z(su * POST_U, POST_V, z_pad - POST_H, z_pad + 0.5, POST, POST)))
    # tie slot along u through both posts, TIE_Z inward from the pad
    kit.cuts.append(F.place(box_z(0.0, POST_V, z_pad - TIE_Z - TIE_W / 2, z_pad - TIE_Z + TIE_W / 2,
                                  2 * POST_U + POST + 2.0, TIE_T)))
    # the lead hole through pad and wall, out past the outer surface
    kit.cuts.append(F.place(cyl_z(0.0, HOLE_V, z_pad - 1.0, 30.0, HOLE_D / 2)))
    mouth = F.pt(0.0, HOLE_V, z_pad - 1.0)
    kit.anchors[f"{kit.ref}.CONN"] = mouth.tolist()
    kit.cable_end = (mouth, -F.n)
    kit.info["cable_exit"] = {"hole_d_mm": HOLE_D, "pad_mm": [PAD_W, PAD_H], "post_h_mm": POST_H,
                              "tie_slot_mm": [TIE_W, TIE_T], "part_outside": desc.get("id"),
                              "note": "the part stays outside the shell; strain relief by a cable tie through the posts "
                                      "(lead size and tie fit are a physical check)"}


CLASSIFY = {"cable_exit": "cable_exit"}
KITS = {"cable_exit": kit_cable_exit}
FOOTPRINTS = {"cable_exit": exit_boxes}
DESCRIBE = {"cable_exit": describe_cable_exit}
