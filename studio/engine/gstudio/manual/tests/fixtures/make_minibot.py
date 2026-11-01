"""Build the 'Mini Bot' CAD fixture: a small but complete gadget in the cad stage's output format.

TEST FIXTURE ONLY. Dimensions are invented to exercise the manual stage (shell with front apertures and an
open rear, rear cover, backplane cartridge with core board, amp and microSD, five front modules, inserts,
screws, feet, ties and cable envelopes). Writes into <out>:
  spec.json, electrical.json (copied), cad/components.json, cad/components/*.brep, cad/anchors.json,
  cad/cable-routes.json, print/PRINT_*.stl, print/TEST_FitCoupon.stl
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import cadquery as cq

HERE = Path(__file__).resolve().parent / "minibot"

TEAL = "#8CC9C0"
FLOOR = 5.4
BOARD_Z0, BOARD_Z1 = 11.8, 13.4
OX, OY = -46.0, -22.0  # board origin (lower-left) in world


def box(x0, x1, y0, y1, z0, z1) -> cq.Workplane:
    return cq.Workplane().box(x1 - x0, y1 - y0, z1 - z0).translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))


def cyl_y(x, z, y0, y1, r) -> cq.Workplane:
    return cq.Workplane("XZ").circle(r).extrude(-(y1 - y0)).translate((x, y0, z))


def cyl_z(x, y, z0, z1, r) -> cq.Workplane:
    return cq.Workplane("XY").circle(r).extrude(z1 - z0).translate((x, y, z0))


def tube(points, r=1.5) -> cq.Workplane:
    w = None
    for a, b in zip(points, points[1:]):
        va, vb = cq.Vector(*a), cq.Vector(*b)
        d = vb - va
        L = d.Length
        if L < 1e-6:
            continue
        seg = cq.Solid.makeCylinder(r, L, va, d.normalized())
        w = seg if w is None else w.fuse(seg)
    for p in points[1:-1]:
        w = w.fuse(cq.Solid.makeSphere(r, cq.Vector(*p), angleDegrees1=-90, angleDegrees2=90))
    return cq.Workplane().add(w.clean())


def shell() -> cq.Workplane:
    outer = box(-60, 60, -45, 42, 3, 143).edges("|Y").fillet(14).faces("<Y").edges().fillet(4)
    inner = box(-57.6, 57.6, -42.6, 50, FLOOR, 140.6).edges("|Y").fillet(11.6)
    s = outer.cut(inner)
    s = s.cut(box(-13, 13, -50, -40, 105, 119))  # display window 26 x 14
    s = s.cut(cyl_y(30, 124, -50, -40, 3.0))  # LED
    s = s.cut(cyl_y(0, 80, -50, -40, 8.0))  # button
    s = s.cut(cyl_y(-30, 92, -50, -40, 1.25))  # mic
    pts = []
    for j in range(-5, 6):
        for i in range(-10, 11):
            x, z = i * 4.4 + (2.2 if j % 2 else 0), j * 3.8
            if (x / 23) ** 2 + (z / 11) ** 2 < 1:
                pts.append((x, 40 + z))
    holes = None
    for x, z in pts:
        c = cyl_y(x, z, -50, -40, 1.4)
        holes = c if holes is None else holes.union(c)
    s = s.cut(holes)
    for hx, hy in ((3.5, 3.5), (88.5, 3.5), (3.5, 58.5), (88.5, 58.5)):  # cartridge bosses
        s = s.union(cyl_z(OX + hx, OY + hy, FLOOR - 0.5, BOARD_Z0, 3.5).cut(cyl_z(OX + hx, OY + hy, 6.0, BOARD_Z0 + 1, 1.6)))
    for x, z in ((-48, 15), (48, 15), (-48, 131), (48, 131)):  # cover screw bosses
        rib = box(x, 58.5, 33, 42, z - 2, z + 2) if x > 0 else box(-58.5, x, 33, 42, z - 2, z + 2)
        s = s.union(rib).union(cyl_y(x, z, 32, 42, 4.0)).cut(cyl_y(x, z, 31, 43, 1.25))
    s = s.union(box(-20, 20, -38.6, -36.6, 96, 98))  # display ledge
    return s


def build(out: Path) -> Path:
    out = Path(out)
    cad = out / "cad"
    comps_dir = cad / "components"
    prn = out / "print"
    for d in (comps_dir, prn):
        d.mkdir(parents=True, exist_ok=True)
    for f in ("spec.json", "electrical.json"):
        shutil.copy(HERE / f, out / f)
    comps: dict[str, dict] = {}
    anchors: dict[str, list] = {}

    def add(name, wp, color, kind=None, **meta):
        shape = wp.val() if isinstance(wp, cq.Workplane) and len(wp.vals()) == 1 else cq.Compound.makeCompound([v for v in wp.vals()])
        shape.exportBrep(str(comps_dir / f"{name}.brep"))
        bb = shape.BoundingBox()
        comps[name] = {"kind": kind or name.split("_", 1)[0], "label": meta.pop("label", name.split("_", 1)[1]),
                       "color": color, "bbox": [round(v, 3) for v in (bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax)],
                       "volume_mm3": round(shape.Volume(), 1), "valid": shape.isValid(), "solids": len(shape.Solids()) or 1,
                       "step": "assembly", **meta}
        return shape

    sh = add("PRINT_Shell", shell(), TEAL, label="Main shell")
    cover = box(-60, 60, 42, 45, 3, 143).edges("|Y").fillet(14)
    cover = cover.union(box(-56.9, 56.9, 39, 42, 6.1, 139.9).cut(box(-55, 55, 38, 43, 8, 138)))
    for x, z in ((-48, 15), (48, 15), (-48, 131), (48, 131)):
        cover = cover.cut(cyl_y(x, z, 37, 46, 1.4))
    cover = cover.cut(box(-6, 6, 38, 46, 12, 19))  # USB-C opening
    cv = add("PRINT_RearCover", cover, TEAL, label="Rear cover")
    ring = cyl_y(0, 40, -30.6, -27.6, 23).cut(cyl_y(0, 40, -31, -27, 18))
    ring = ring.union(cyl_y(-26, 40, -30.6, -27.6, 3.2)).union(cyl_y(26, 40, -30.6, -27.6, 3.2))
    rg = add("PRINT_SpeakerRing", ring, TEAL, feature_id="speaker", label="Speaker ring")
    dif = add("PRINT_LedDiffuser", cyl_y(30, 124, -45, -41, 2.8).union(cyl_y(30, 124, -41.6, -40.6, 4.2)), "#F4F1EA", feature_id="night_light", label="LED diffuser")

    # ---- cartridge
    bp = box(OX, OX + 92, OY, OY + 62, BOARD_Z0, BOARD_Z1)
    for hx, hy in ((3.5, 3.5), (88.5, 3.5), (3.5, 58.5), (88.5, 58.5)):
        bp = bp.cut(cyl_z(OX + hx, OY + hy, BOARD_Z0 - 1, BOARD_Z1 + 1, 1.35))
    parts = [bp]
    jst = {"J_I2C1": (10, 4), "J_MIC": (26, 4), "J_IO1": (42, 4), "J_LED": (54, 4), "J_I2C2": (66, 4), "J_SPK": (82, 4)}
    npins = {"J_I2C1": 4, "J_MIC": 6, "J_IO1": 3, "J_LED": 3, "J_I2C2": 4, "J_SPK": 2}
    for sid, (bx, by) in jst.items():
        w = (npins[sid] - 1) * 2.0 + 3.9
        parts.append(box(OX + bx - w / 2, OX + bx + w / 2, OY + by - 2.25, OY + by + 2.25, BOARD_Z1, BOARD_Z1 + 6))
        anchors[f"BP1.{sid}"] = [OX + bx, OY + by, BOARD_Z1 + 6]
    parts.append(box(OX + 41.5, OX + 50.5, OY + 55, OY + 62.5, BOARD_Z1, BOARD_Z1 + 3.2))  # USB-C
    anchors["BP1.J_PWR"] = [OX + 46, OY + 62.5, BOARD_Z1 + 1.6]
    for row_y in (31 - 11.43, 31 + 11.43):  # U1 sockets
        parts.append(box(OX + 35.5, OX + 91.5, OY + row_y - 1.27, OY + row_y + 1.27, BOARD_Z1, BOARD_Z1 + 8.5))
    for sx, sy, n in ((15, 43, 7), (18, 17, 6)):
        parts.append(box(OX + sx - n * 1.27, OX + sx + n * 1.27, OY + sy - 1.27, OY + sy + 1.27, BOARD_Z1, BOARD_Z1 + 8.5))
    bpw = cq.Workplane().add([p.val() for p in parts])
    add("PURCHASE_Backplane", bpw, "#1F5E42", ref="BP1", module_id="backplane-v1", label="Backplane")
    for i, (hx, hy) in enumerate(((3.5, 3.5), (88.5, 3.5), (3.5, 58.5), (88.5, 58.5)), 1):
        anchors[f"BP1.H{i}"] = [OX + hx, OY + hy, BOARD_Z1]
    dz = BOARD_Z1 + 8.5
    dk = box(-14, 49, OY + 31 - 12.75, OY + 31 + 12.75, dz, dz + 1.6).union(box(5, 23, OY + 22, OY + 40, dz + 1.6, dz + 4.6))
    dk = dk.union(box(42, 49.5, OY + 21, OY + 30, dz + 1.6, dz + 4.8)).union(box(42, 49.5, OY + 32, OY + 41, dz + 1.6, dz + 4.8))
    add("PURCHASE_CoreBoard", dk, [0.15, 0.17, 0.2], ref="U1", module_id="esp32s3-devkitc1-n16r8", label="Core board")
    anchors["U1.USB"] = [49.5, OY + 25.5, dz + 3.2]
    add("PURCHASE_Amp", box(OX + 6, OX + 24, OY + 34, OY + 52, dz, dz + 1.6).union(box(OX + 16, OX + 22, OY + 46, OY + 51, dz + 1.6, dz + 4)),
        [0.12, 0.25, 0.55], ref="AMP1", module_id="max98357a", label="Amplifier")
    add("PURCHASE_MicroSD", box(OX + 6, OX + 30, OY + 8, OY + 26, dz, dz + 1.6).union(box(OX + 10, OX + 24, OY + 12, OY + 24, dz + 1.6, dz + 3.4)),
        [0.12, 0.25, 0.55], ref="SD1", module_id="microsd-spi", label="microSD module")

    # ---- front modules
    add("PURCHASE_Display", box(-13.5, 13.5, -42.6, -38.6, 98.5, 125.5).union(box(-12, 12, -43.2, -42.6, 106, 118)),
        [0.1, 0.12, 0.16], ref="DISP1", module_id="oled-096-i2c", feature_id="eyes", label="Display")
    anchors["DISP1.CONN"] = [0, -38.6, 123]
    add("PURCHASE_Speaker", cyl_y(0, 40, -38.6, -30.6, 20).union(cyl_y(0, 40, -30.6, -26, 9)), "#2A2D33",
        ref="SPK1", module_id="speaker-40mm", feature_id="speaker", label="Speaker")
    anchors["SPK1.CONN"] = [0, -26, 47]
    add("PURCHASE_Button", cyl_y(0, 80, -48, -45, 7.5).union(cyl_y(0, 80, -45, -28, 6)), "#F2F0EA",
        ref="BTN1", module_id="btn-16mm", feature_id="story_button", label="Button")
    anchors["BTN1.CONN"] = [0, -28, 80]
    add("PURCHASE_Led", box(25, 35, -40.6, -39.0, 119, 129), [0.1, 0.35, 0.55], ref="LED1", module_id="ws2812b-mini",
        feature_id="night_light", label="LED board")
    anchors["LED1.CONN"] = [30, -39.0, 120]
    add("PURCHASE_Mic", box(-37, -23, -42.6, -41.0, 85, 99), [0.1, 0.35, 0.55], ref="MIC1", module_id="inmp441", feature_id="mic", label="Microphone")
    anchors["MIC1.CONN"] = [-30, -41.0, 86]

    # ---- hardware
    ins = cq.Workplane().add([cyl_z(OX + hx, OY + hy, 6.1, BOARD_Z0, 1.9).val() for hx, hy in ((3.5, 3.5), (88.5, 3.5), (3.5, 58.5), (88.5, 58.5))])
    add("PURCHASE_Inserts", ins, "#B08D57", label="M2.5 heat-set insert", qty=4)
    scr = []
    for hx, hy in ((3.5, 3.5), (88.5, 3.5), (3.5, 58.5), (88.5, 58.5)):
        scr.append(cyl_z(OX + hx, OY + hy, BOARD_Z1, BOARD_Z1 + 1.7, 2.2).union(cyl_z(OX + hx, OY + hy, BOARD_Z1 - 6, BOARD_Z1, 1.2)).val())
    add("PURCHASE_CartridgeScrews", cq.Workplane().add(scr), "#3A3D42", label="M2.5 x 6 mm screw", qty=4)
    cs = []
    for x, z in ((-48, 15), (48, 15), (-48, 131), (48, 131)):
        cs.append(cyl_y(x, z, 45, 46.7, 2.2).union(cyl_y(x, z, 35, 45, 1.2)).val())
    add("PURCHASE_CoverScrews", cq.Workplane().add(cs), "#3A3D42", label="M2.5 x 10 mm screw", qty=4)
    sscr = [cyl_y(x, 40, -27.6, -25.9, 2.0).union(cyl_y(x, 40, -33, -27.6, 1.1)).val() for x in (-26, 26)]
    add("PURCHASE_SpeakerScrews", cq.Workplane().add(sscr), "#3A3D42", label="M2 x 6 mm screw", qty=2)
    add("PURCHASE_Feet", cq.Workplane().add([cyl_z(x, y, 0, 3, 5).val() for x in (-45, 45) for y in (-32, 30)]), "#222222", label="Adhesive rubber foot 10 mm", qty=4)
    ties = [box(-52, -49, -10, 2, 60, 62).val(), box(-52, -49, -10, 2, 100, 102).val()]
    add("PURCHASE_CableTies", cq.Workplane().add(ties), "#E8E6DF", label="Cable tie 100 mm", qty=2)

    # ---- cable routes
    routes = []
    spec_routes = [
        ("REFERENCE_DisplayCable", "BP1.J_I2C1", "DISP1.CONN", "JST-PH 4p 150 mm", [(-36, -18, 19.4), (-36, -22, 40), (-50, -20, 70), (-20, -34, 118), (0, -36, 123)], "#4A5A8A"),
        ("REFERENCE_MicCable", "BP1.J_MIC", "MIC1.CONN", "JST-PH 6p 150 mm", [(-20, -18, 19.4), (-24, -24, 40), (-50, -12, 80), (-30, -38, 86)], "#4A6A5A"),
        ("REFERENCE_ButtonCable", "BP1.J_IO1", "BTN1.CONN", "JST-PH 3p 100 mm", [(-4, -18, 19.4), (-6, -22, 50), (0, -26, 78)], "#6A5A4A"),
        ("REFERENCE_LedCable", "BP1.J_LED", "LED1.CONN", "JST-PH 3p 150 mm", [(8, -18, 19.4), (40, -16, 50), (48, -24, 100), (30, -37, 118)], "#8A4A3A"),
        ("REFERENCE_SpeakerCable", "BP1.J_SPK", "SPK1.CONN", "JST-PH 2p 100 mm", [(36, -18, 19.4), (30, -20, 40), (6, -24, 47)], "#3A3A3A"),
    ]
    for name, frm, to, cable, pts, col in spec_routes:
        add(name, tube(pts, 1.5), col, label=cable)
        L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        routes.append({"id": name, "component": name, "from": frm, "to": to, "cable": cable, "diameter": 3.0,
                       "length_mm": round(L, 1), "points": [list(p) for p in pts]})
    (cad / "components.json").write_text(json.dumps(comps, indent=1))
    (cad / "anchors.json").write_text(json.dumps(anchors, indent=1))
    (cad / "cable-routes.json").write_text(json.dumps(routes, indent=1))

    # ---- print STLs (print orientation: shell front down, cover outside down, small parts flat)
    def stl(shape, name, rot=None):
        s = shape
        if rot:
            s = s.rotate((0, 0, 0), rot[0], rot[1])
        bb = s.BoundingBox()
        s = s.translate(cq.Vector(-(bb.xmin + bb.xmax) / 2, -(bb.ymin + bb.ymax) / 2, -bb.zmin))
        cq.exporters.export(cq.Workplane().add(s), str(prn / f"{name}.stl"), tolerance=0.05, angularTolerance=0.15)

    stl(sh, "PRINT_Shell", ((1, 0, 0), 90))  # front face down
    stl(cv, "PRINT_RearCover", ((1, 0, 0), -90))  # outside face down
    stl(rg, "PRINT_SpeakerRing", ((1, 0, 0), 90))
    stl(dif, "PRINT_LedDiffuser", ((1, 0, 0), 90))
    coupon = box(-22, 22, -22, 22, 0, 2.5).cut(cyl_z(-8, 0, -1, 4, 8.0)).cut(cyl_z(14, 8, -1, 4, 1.6)).cut(cyl_z(14, -8, -1, 4, 1.25))
    stl(coupon.val(), "TEST_FitCoupon")
    # stand-in for the PCB stage's render (a plain labelled board), so the manual's PCB figure path is exercised
    from PIL import Image, ImageDraw

    (out / "pcb").mkdir(exist_ok=True)
    im = Image.new("RGB", (920, 620), (40, 60, 160))
    ImageDraw.Draw(im).text((40, 40), "TEST FIXTURE board-top.png (not a PCB render)", fill=(255, 255, 255))
    im.save(out / "pcb" / "board-top.png")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
