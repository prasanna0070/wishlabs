"""Base-plate seam: the plate never stands inside the shell, and a real clash is still refused.

Live 2026-11-21 the most common refusal was a PRINT_Shell / PRINT_BasePlate intersection under 2 mm3 (p_esnfk7mb466z
0.05 mm3, p_h77zjgp6fekk 1.61 mm3; bench p_qg7gntgnpjyt, p_m84ggdt39tpm, p_puuzzexmaciv). Two causes, both real
geometry, both fixed in the geometry (no tolerance in the collision check):

- corners: the plate edge and the shell's opening cut were two separately smoothed splines (_xy_prism), so at a corner
  of the opening the plate crossed the shell by 0.1-0.5 mm through the 2.4 mm floor (enclosure.plate_prism);
- kit adds: a feature low on the front put its mount material into the opening prism, i.e. into the plate
  (build.trim_adds_at_plate). Only a shallow sliver is trimmed. NOT trimmed, so the collision check still refuses it:
  an add deeper than 1 mm below the plate top, a kit with over 20 mm3 in the room in total, and any add near a fastener
  or gasket whose body reaches the room (radius included; inserts and gaskets may overlap printed parts by rule, so the
  add is what makes their clash with the plate visible: review of 5bed7aa, an insert at axis z 5.5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

STUDIO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(STUDIO / "engine"))
cq = pytest.importorskip("cadquery")

from shapely.geometry import box as sbox  # noqa: E402

from gstudio.cad.body import build_body  # noqa: E402
from gstudio.cad.build import PLATE_TRIM_MAX_DEPTH, PLATE_TRIM_MAX_MM3, trim_adds_at_plate  # noqa: E402
from gstudio.cad.enclosure import PLATE_GAP, _xy_prism, base_plate_opening, plate_prism  # noqa: E402
from gstudio.cad.geom import cyl_axis  # noqa: E402
from gstudio.cad.probe import Probe  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
LIVE = {"step-counter-console-p_esnfk7mb466z.json": 0.0512, "clinic-organizer-dock-p_h77zjgp6fekk.json": 0.24}
T_BP = 3.5


def _shell_and_opening(name):
    body = json.loads((FIX / name).read_text())["body"]
    bg = build_body(body, 2.4)  # skin_front 2.4 on both live builds (cad-summary info)
    O, C = bg.outer, bg.cavity
    op = base_plate_opening(Probe(O), Probe(C), float(body.get("wall", 2.4)), T_BP)
    S = O.cut(C.fuse(_xy_prism(op, -1.0, T_BP + 0.8)).clean())  # the shell as build.py cuts it
    return S, op


@pytest.mark.parametrize("name", sorted(LIVE))
def test_plate_edge_stays_inside_the_opening_the_shell_is_cut_with(name):
    S, op = _shell_and_opening(name)
    old = _xy_prism(op.buffer(-PLATE_GAP), 0.0, T_BP)
    assert S.intersect(old).Volume() > 0.02  # the live sliver: the old plate crossed the shell at the corners
    plate, info = plate_prism(op, op.buffer(-PLATE_GAP), T_BP)
    assert info and info["rebuilt"] and info["overlap_after_mm2"] < 1e-6
    assert S.intersect(plate).Volume() < 1e-4
    # the rebuilt edge is the design outline minus the corner slivers, not a smaller plate
    pp = op.buffer(-PLATE_GAP)
    assert pp.area - 0.01 * pp.area < plate.Volume() / T_BP <= pp.area + 1e-6


def test_plate_that_already_clears_is_left_exactly_as_before():
    op = sbox(-60, -40, 60, 40).buffer(6.0)  # rounded rectangle: the two splines agree
    plate, info = plate_prism(op, op.buffer(-PLATE_GAP), T_BP)
    assert info is None
    assert abs(plate.Volume() - _xy_prism(op.buffer(-PLATE_GAP), 0.0, T_BP).Volume()) < 1e-9


def _kit(adds, fasteners=(), parts=(), fid="f"):
    return SimpleNamespace(feature_id=fid, adds=list(adds), fasteners=list(fasteners), parts=list(parts))


def _plate():
    return _xy_prism(sbox(-50, -40, 50, 40).buffer(3.0 - PLATE_GAP), 0.0, T_BP)


def _room():
    return _xy_prism(sbox(-50, -40, 50, 40).buffer(3.0), -1.0, T_BP + 0.8)


def test_small_kit_add_in_the_plate_room_is_trimmed_clear():
    room = _room()
    add = cq.Solid.makeBox(12.0, 3.0, 20.0, cq.Vector(-6.0, -45.0, 3.4))  # 1 x 0.9 mm into the room, like the speaker cup
    out, recs = trim_adds_at_plate([_kit([add])], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is True and 0 < recs[0]["in_room_mm3"] <= PLATE_TRIM_MAX_MM3
    assert out[0].intersect(room).Volume() < 1e-6
    assert out[0].Volume() > add.Volume() - recs[0]["in_room_mm3"] - 1e-3  # only the part in the room went


def test_deep_kit_add_is_not_trimmed_so_the_clash_is_still_refused():
    room = _room()
    add = cq.Solid.makeBox(20.0, 10.0, 20.0, cq.Vector(-10.0, -30.0, 1.0))  # a real clash: 3.3 mm deep, 660 mm3
    out, recs = trim_adds_at_plate([_kit([add])], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is False
    assert out[0].intersect(room).Volume() > PLATE_TRIM_MAX_MM3  # stays in the shell: PRINT_Shell / PRINT_BasePlate fails
    plate = _xy_prism(sbox(-50, -40, 50, 40).buffer(3.0 - PLATE_GAP), 0.0, T_BP)
    assert out[0].intersect(plate).Volume() > 100.0


def test_kit_with_a_fastener_in_the_plate_room_is_not_trimmed():
    room = _room()
    add = cq.Solid.makeBox(12.0, 3.0, 20.0, cq.Vector(-6.0, -42.0, 3.4))
    f = SimpleNamespace(head=np.array([0.0, -41.0, 8.0]), axis=np.array([0.0, 0.0, -1.0]), length=6.0, d=2.5)
    out, recs = trim_adds_at_plate([_kit([add], [f])], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is False
    assert out[0].intersect(room).Volume() > 1.0


def test_add_clear_of_the_room_is_untouched():
    room = _room()
    add = cq.Solid.makeBox(12.0, 3.0, 20.0, cq.Vector(-6.0, -42.0, 5.0))
    out, recs = trim_adds_at_plate([_kit([add])], room, T_BP + 0.8)
    assert recs == [] and out[0] is add


def _clamp(za):
    """Review counter-example (5bed7aa): a clamp post (r 3.6) on a low front pad, its tip 1.5 mm over the opening, holding
    a horizontal heat-set insert (od 4.6) whose AXIS is above the old endpoint test (z_room + 0.5 = 4.8) but whose body
    reaches 1.2 mm into the plate. The insert vs the plate is allowed by rule, so the post is the only refusal."""
    post = cyl_axis(np.array([0.0, -46.0, za]), np.array([0, 1.0, 0]), 4.5, 3.6)
    ins = SimpleNamespace(head=np.array([0.0, -41.5, za]), axis=np.array([0, -1.0, 0]), length=4.0, d=4.6)
    return post, ins


@pytest.mark.parametrize("za", [5.5, 5.2])
def test_insert_low_in_a_post_over_the_opening_stays_refused(za):
    room, plate = _room(), _plate()
    post, ins = _clamp(za)
    assert post.intersect(plate).Volume() > 1.0  # the real clash the gate must keep seeing
    out, recs = trim_adds_at_plate([_kit([post], [ins])], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is False
    assert out[0] is post and out[0].intersect(plate).Volume() > 1.0  # PRINT_Shell / PRINT_BasePlate still fails


def test_insert_of_another_kit_in_the_add_stays_refused():
    room, plate = _room(), _plate()
    post, ins = _clamp(5.5)
    out, recs = trim_adds_at_plate([_kit([post], fid="a"), _kit([], [ins], fid="b")], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is False
    assert out[0].intersect(plate).Volume() > 1.0


def test_gasket_reaching_the_room_stays_refused():
    room = _room()
    add = cq.Solid.makeBox(12.0, 3.0, 20.0, cq.Vector(-6.0, -45.0, 3.4))  # would be trimmed on its own (first test)
    gk = SimpleNamespace(name="PURCHASE_MicGasket", subparts=[("Gasket", cyl_axis(np.array([0.0, -44.0, 4.0]),
                                                                              np.array([0, 1.0, 0]), 2.0, 3.8), None)])
    out, recs = trim_adds_at_plate([_kit([add], parts=[gk])], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is False and out[0] is add


def test_small_but_deep_add_stays_refused():
    room, plate = _room(), _plate()
    add = cq.Solid.makeBox(6.0, 3.0, 20.0, cq.Vector(-3.0, -45.0, T_BP - 1.6))  # 14.4 mm3 in the room, 1.6 mm deep
    out, recs = trim_adds_at_plate([_kit([add])], room, T_BP + 0.8)
    assert recs and recs[0]["in_room_mm3"] < PLATE_TRIM_MAX_MM3
    assert recs[0]["depth_below_plate_top_mm"] > PLATE_TRIM_MAX_DEPTH and recs[0]["trimmed"] is False
    assert out[0].intersect(plate).Volume() > 1.0


def test_many_small_adds_of_one_kit_over_the_cap_stay_refused():
    room = _room()
    adds = [cq.Solid.makeBox(12.0, 3.0, 20.0, cq.Vector(x, -45.0, 3.4)) for x in (-30.0, -6.0, 18.0)]  # 10.8 mm3 each
    out, recs = trim_adds_at_plate([_kit(adds)], room, T_BP + 0.8)
    assert len(recs) == 3 and all(r["trimmed"] is False for r in recs)
    assert all(o is a for o, a in zip(out, adds))
