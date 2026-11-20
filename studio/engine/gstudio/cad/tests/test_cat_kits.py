"""Motion pack servo kit (CATALOG-PLAN package B, kits_motion.py) on a plain box shell, without the library pack:
the kit builds through the registry, nothing collides, the shaft hole is open, the printed arm (or flap) sits outside
the wall, and the pure-Python keep-out (normalize's view) contains the CAD parts.

The full design check (waving-desk-buddy through normalize, electrical and CAD) is in test_cat_motion_build.py; it
needs the motion pack rows merged into the library.
"""

import math

import numpy as np
import pytest

cq = pytest.importorskip("cadquery")

from gstudio.cad import footprint as FP  # noqa: E402
from gstudio.cad import kits_ext, kits_motion  # noqa: E402
from gstudio.cad.features import build_kit  # noqa: E402
from gstudio.cad.modules import describe  # noqa: E402
from gstudio.cad.probe import Probe  # noqa: E402

WALL = 2.4
ROW = {"id": "SERVO_SG90_ARM", "name": "Micro servo SG90 with a printed arm", "role": "servo", "mass_g": 9.0,
       "dims_mm": {"x": 32.2, "y": 12.2, "z": 31.0}, "aperture": {"needs": "shaft_hole", "size_mm": {"w": 8, "h": 8}}}


def _box(x0, x1, y0, y1, z0, z1):
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))


@pytest.fixture(scope="module")
def shell():
    O = _box(-50, 50, -30, 30, 0, 90)
    C = _box(-50 + WALL, 50 - WALL, -30 + WALL, 30 - WALL, WALL, 90 - WALL)
    return O, C, Probe(O), Probe(C)


def _build(shell, label, at=(0.0, 45.0), face="front"):
    O, C, pO, pC = shell
    fz = FP.default_fasteners()
    desc = describe(ROW)
    feat = {"id": "wave_arm", "module": ROW["id"], "face": face, "at": list(at), "label": label}
    kit = build_kit(feat, desc, "SRV1", pO, pC, O, fz, {"button": (0.9, 0.5, 0.2)}, "features[0]", skin=WALL)
    S = O.cut(C.cut(*kit.adds)) if kit.adds else O.cut(C)
    if kit.cuts:
        S = S.cut(*kit.cuts)
    return kit, S, desc, feat


def _vol(a, b) -> float:
    try:
        return float(a.intersect(b).Volume())
    except Exception:  # pragma: no cover
        return 0.0


def test_registry_has_the_servo_kit():
    assert kits_ext.errors() == []
    assert kits_ext.kit_fn("servo") is kits_motion.kit_servo
    assert describe(ROW)["kind"] == "servo"


@pytest.mark.parametrize("label,style,width", [("Waving arm", "arm", 10.0), ("Food flap", "flap", 30.0)])  # arm: hub 10 mm
def test_servo_kit_builds_clear(shell, label, style, width):
    kit, S, desc, feat = _build(shell, label)
    assert not [w for w in kit.warnings if "no dedicated mount kit" in w]
    names = [p.name for p in kit.parts]
    assert names == ["PURCHASE_Servo", "PRINT_ServoArm_wave_arm"]
    servo, arm = kit.parts
    assert arm.kind == "PRINT" and arm.extra["style"] == style and arm.feature_id == "wave_arm"
    # no collisions: servo vs shell, arm vs shell, servo vs arm (the arm only rests on the horn plate)
    assert _vol(servo.shape, S) < 0.05
    assert _vol(arm.shape, S) < 0.05
    assert _vol(servo.shape, arm.shape) < 0.05
    # the printed part lies outside the front wall (y < -30) and has the arm's width across
    ab = arm.shape.BoundingBox()
    assert ab.ymax < -30.0 - 0.2 and ab.ymin > -30.0 - 6.0
    assert abs(ab.xlen - width) < 0.6 and ab.zlen > 39.0
    # the case sits inside the cavity, behind the wall
    sb = servo.shape.BoundingBox()
    assert sb.ymax > -30.0 and sb.ymin > -30.0 + WALL - 0.01 - 6.5  # horn and spline reach out through the hole only
    # the shaft hole is open: a 3 mm rod along the shaft axis meets no shell material
    so = desc["servo"]["shaft_off"]
    sp = np.asarray(kit.frame.pt(so, 0.0, 0.0))
    rod = cq.Solid.makeCylinder(1.5, 12.0, cq.Vector(sp[0], sp[1] - 6.0, sp[2]), cq.Vector(0, 1, 0))
    assert _vol(rod, S) < 0.01
    # two tab bosses joined to the shell
    assert len(kit.adds) == 3 and kit.info["servo"]["style"] == style


def test_keepout_contains_the_cad_parts(shell):
    kit, S, desc, feat = _build(shell, "Waving arm")
    boxes = FP.kit_footprint(feat, desc, skin=WALL)
    world = [FP.to_world(b, -30.0) for b in boxes]
    case = next(w for b, w in zip(boxes, world) if b["what"] == "servo case")
    sweep = next(w for b, w in zip(boxes, world) if b["kind"] == "clearance")
    sb = kit.parts[0].subparts[0][1].BoundingBox()  # the case
    tol = 0.05
    assert case[0] - tol <= sb.xmin and sb.xmax <= case[1] + tol
    assert case[2] - tol <= sb.ymin and sb.ymax <= case[3] + tol
    assert case[4] - tol <= sb.zmin and sb.zmax <= case[5] + tol
    ab = kit.parts[1].shape.BoundingBox()
    assert sweep[0] - tol <= ab.xmin and ab.xmax <= sweep[1] + tol
    assert sweep[2] - tol <= ab.ymin and ab.ymax <= sweep[3] + tol
    assert sweep[4] - tol <= ab.zmin and ab.zmax <= sweep[5] + tol
    # the sweep covers the arm turned 90 degrees either way
    R = kits_motion.sweep_radius()
    assert sweep[1] - sweep[0] >= 2 * R - 0.01 and math.isclose(sweep[5] - (feat["at"][1]), R, abs_tol=0.01)


@pytest.fixture(scope="module")
def leaning_shell():
    """Front face leaning out toward the top (y = -30 at z 0 to y = -33 at z 90, under the 8 degree frame snap), like a
    silhouette side that bulges above the servo (PX waving-desk-buddy, 2026-11-20: horn plate / shell 17 mm3)."""
    O = cq.Workplane("YZ").polyline([(-30, 0), (30, 0), (30, 90), (-33, 90)]).close().extrude(100).translate((-50, 0, 0)).val()
    C = (cq.Workplane("YZ").polyline([(-27.6, WALL), (30 - WALL, WALL), (30 - WALL, 90 - WALL), (-30.52, 90 - WALL)]).close()
         .extrude(100 - 2 * WALL).translate((-50 + WALL, 0, 0)).val())
    return O, C, Probe(O), Probe(C)


@pytest.mark.parametrize("label", ["Waving arm", "Food flap"])
def test_servo_clears_a_face_that_bulges_above_it(leaning_shell, label):
    kit, S, desc, feat = _build(leaning_shell, label, at=(0.0, 40.0))
    servo, arm = kit.parts
    assert kit.info["servo"]["raised_mm"] > 0.3  # seated higher (pocket) so the horn plate and arm clear the lean
    assert _vol(servo.shape, S) < 0.05 and _vol(arm.shape, S) < 0.05
    assert not [w for w in kit.warnings if "curves too much" in w]


def test_side_face_and_rotation(shell):
    kit, S, desc, feat = _build(shell, "Waving arm", at=(0.0, 45.0), face="right")
    servo, arm = kit.parts
    assert _vol(servo.shape, S) < 0.05 and _vol(arm.shape, S) < 0.05
    ab = arm.shape.BoundingBox()
    assert ab.xmax < -50.0 - 0.2  # robot's right = -X side, outside the wall
