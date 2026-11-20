"""Sensing pack kit (CATALOG-PLAN package C): a strain-relieved cable exit for a part that lives OUTSIDE the shell,
the capacitive soil probe in a plant pot or the float switch in a water tank (library/packs/sensing.json,
aperture.needs "cable_exit"). Water and soil stay outside; only the lead comes in.

Kit kind "cable_exit". Kit frame as in features.py: origin on the outer surface at features[].at, +z outward, u right,
v up, turned by the feature's rotate. Layout (local mm):
- a flat pad on the inner wall, PAD_W x PAD_H, centred on features[].at;
- the exit, a SLOT_W x SLOT_H slot (stadium) through the pad and the wall at (0, HOLE_V), sized so the cable's
  JST-PH 3-pin port plug (housing about 8.0 x 4.5 mm, JST PHR-3) passes from outside when the probe or float is fitted
  (a round 6.5 mm hole, the plan's first figure, cannot pass that plug or a Dupont 1x3 housing);
- two printed posts below the hole at u = +/-POST_U, v = POST_V, each POST_SU (along u) x POST_SV (along v) and POST_H
  tall, with one cable-tie slot running along u through both, TIE_Z in from the pad: the lead comes in through the
  hole and turns down between the posts; a tie threaded through both slots passes over the lead (pressing it to the
  pad) and closes over the post tips, so a pull on the lead outside never reaches the port inside.
The CONN anchor (where the internal harness ends) is at the inner mouth of the hole. The purchased part itself is
modelled only as the short length of its lead that sits in the exit (PURCHASE_ExitLead, ref and module_id of the
part), so the BOM, the build guide's fit step and the CAD join to it; the probe or float stays outside the model.

Light-engine rule: no CadQuery at import time (kit_cable_exit imports it), so normalize can use the footprint in the web
image. Dimensions are design choices for a 3-core lead about 3-4 mm across (the probe and float leads are UNVERIFIED);
the hole, posts and tie fit are a physical check.
"""

from __future__ import annotations

PAD_W, PAD_H = 20.0, 12.0  # = FOOTPRINTS / pack row merge.face_mm [20, 12]
DEPTH = 15.0  # keep-out depth from the outer surface (pack row merge.depth_mm 15)
SLOT_W, SLOT_H = 9.5, 5.5  # exit slot: passes a JST-PH 3-pin housing (about 8.0 x 4.5 mm) with 0.5-0.75 mm play
HOLE_V = 2.75  # slot centre above features[].at
POST_SU, POST_SV = 3.0, 5.0  # post section: along u, along v
POST_U = 5.0  # post centres at u = +/-POST_U (7 mm gap between them for the lead)
POST_V = -3.5  # post centres below the slot (1 mm of pad between the post tops and the slot)
POST_H = 7.0  # post height from the pad, inward
TIE_W, TIE_T = 3.0, 1.6  # cable-tie slot: TIE_W along v (tie width 2.5 mm), TIE_T along z (tie thickness about 1 mm)
TIE_Z = 5.0  # slot centre, inward from the pad: above a lead up to 4 mm thick lying on the pad
LEAD_R = 1.8  # modelled lead in the exit (3.6 mm across: UNVERIFIED lead size), clear of the slot walls


def exit_boxes(desc: dict, skin: float = 2.4) -> list[dict]:
    """FOOTPRINTS hook: local keep-out boxes (kits_ext contract) for a wall `skin` thick."""
    pad_back = skin  # the pad's inner face sits at the wall's inner surface on a flat wall
    return [
        {"part": "PRINT_Shell", "kind": "pad", "what": "cable exit pad", "u": [-PAD_W / 2, PAD_W / 2],
         "v": [-PAD_H / 2, PAD_H / 2], "depth": [skin - 0.8, pad_back + 0.5]},
        {"part": "PRINT_Shell", "kind": "boss", "what": "strain-relief posts and tie",
         "u": [-POST_U - POST_SU / 2, POST_U + POST_SU / 2], "v": [POST_V - POST_SV / 2, POST_V + POST_SV / 2],
         "depth": [pad_back, pad_back + POST_H + 0.5]},
        {"part": "PURCHASE_Lead", "kind": "module", "what": "lead bend behind the exit (soil probe or float lead)",
         "u": [-SLOT_W / 2, SLOT_W / 2], "v": [-PAD_H / 2, HOLE_V + SLOT_H / 2], "depth": [pad_back, DEPTH]},
    ]


def describe_cable_exit(d: dict, entry: dict) -> None:
    """DESCRIBE hook: the exit geometry on the CAD descriptor (the part itself is outside the shell)."""
    d["cable_exit"] = {"slot": [SLOT_W, SLOT_H], "slot_v": HOLE_V, "pad": [PAD_W, PAD_H], "posts": {"u": POST_U, "v": POST_V,
                       "size": [POST_SU, POST_SV], "h": POST_H}, "tie_slot": [TIE_W, TIE_T, TIE_Z],
                       "outside": "the part sits outside the shell; only its lead enters"}
    d.setdefault("provisional_fields", []).append("lead diameter and tie fit")


def kit_cable_exit(kit, F, S, O, desc: dict, fz: dict, colors: dict, spec_path: str) -> None:
    """KITS hook: pad, exit slot through the wall, two posts with a cable-tie slot; anchor at the inner slot mouth."""
    from .features import _fin, _pad
    from .geom import box_z, cyl_z, prism, rounded_rect_face_fast, stadium_face
    from .parts import Part

    _, zi = S.rect(PAD_W, PAD_H)
    z_in = _fin(zi, -3.0)
    z_pad = min(z_in, -2.0)  # the flat inner face of the pad (local z, negative = inside)
    kit.adds.append(_pad(F, O, rounded_rect_face_fast(PAD_W, PAD_H, 3.0), z_pad))
    for su in (-1.0, 1.0):
        kit.adds.append(F.place(box_z(su * POST_U, POST_V, z_pad - POST_H, z_pad + 0.5, POST_SU, POST_SV)))
    # tie slot along u through both posts, TIE_Z inward from the pad (TIE_W along v, TIE_T along z)
    kit.cuts.append(F.place(box_z(0.0, POST_V, z_pad - TIE_Z - TIE_T / 2, z_pad - TIE_Z + TIE_T / 2,
                                  2 * POST_U + POST_SU + 2.0, TIE_W)))
    # the exit slot through pad and wall, out past the outer surface
    kit.cuts.append(F.place(prism(stadium_face(SLOT_W, SLOT_H, 0.0, HOLE_V), z_pad - 1.0, 30.0)))
    # the part's lead where it passes the wall: from the pad face to just under the outer surface, inside the hole
    lead = Part("PURCHASE_ExitLead", "PURCHASE", desc.get("name") or "lead through the cable exit", (0.12, 0.12, 0.13),
                ref=kit.ref, module_id=desc.get("id"), feature_id=kit.feature_id, spec_path=spec_path,
                mass_g=desc.get("mass_g"), mass_source=desc.get("mass_source", "estimate"), role="module")
    lead.notes.append("only the lead in the exit is modelled: the probe or float itself stays outside the shell")
    lead.add("Lead", F.place(cyl_z(0.0, HOLE_V, z_pad - 0.5, -0.4, LEAD_R)))
    kit.parts.append(lead)
    mouth = F.pt(0.0, HOLE_V, z_pad - 1.0)
    kit.anchors[f"{kit.ref}.CONN"] = mouth.tolist()
    kit.cable_end = (mouth, -F.n)
    kit.info["cable_exit"] = {"slot_mm": [SLOT_W, SLOT_H], "pad_mm": [PAD_W, PAD_H], "post_h_mm": POST_H,
                              "tie_slot_mm": [TIE_W, TIE_T], "part_outside": desc.get("id"),
                              "note": "the part stays outside the shell; strain relief by a cable tie through the posts "
                                      "(lead size and tie fit are a physical check)"}


CLASSIFY = {"cable_exit": "cable_exit"}
KITS = {"cable_exit": kit_cable_exit}
FOOTPRINTS = {"cable_exit": exit_boxes}
DESCRIBE = {"cable_exit": describe_cable_exit}
