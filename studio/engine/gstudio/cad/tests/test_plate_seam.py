"""Base-plate seam: the plate never stands inside the shell, and a real clash is still refused.

Live 2026-11-21 the most common refusal was a PRINT_Shell / PRINT_BasePlate intersection under 2 mm3 (p_esnfk7mb466z
0.05 mm3, p_h77zjgp6fekk 1.61 mm3; bench p_qg7gntgnpjyt, p_m84ggdt39tpm, p_puuzzexmaciv). Two causes, both real
geometry, both fixed in the geometry (no tolerance in the collision check):

- corners: the plate edge and the shell's opening cut were two separately smoothed splines (_xy_prism), so at a corner
  of the opening the plate crossed the shell by 0.1-0.5 mm through the 2.4 mm floor (enclosure.plate_prism);
- kit adds: a feature low on the front put its mount material into the opening prism, i.e. into the plate
  (build.trim_adds_at_plate). A deep intrusion, or one with a fastener in the plate's room, is NOT trimmed, so the
  collision check still refuses it.
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
from gstudio.cad.build import PLATE_TRIM_MAX_MM3, trim_adds_at_plate  # noqa: E402
from gstudio.cad.enclosure import PLATE_GAP, _xy_prism, base_plate_opening, plate_prism  # noqa: E402
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


def _kit(adds, fasteners=()):
    return SimpleNamespace(feature_id="f", adds=list(adds), fasteners=list(fasteners))


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
    f = SimpleNamespace(head=np.array([0.0, -41.0, 8.0]), axis=np.array([0.0, 0.0, -1.0]), length=6.0)
    out, recs = trim_adds_at_plate([_kit([add], [f])], room, T_BP + 0.8)
    assert recs and recs[0]["trimmed"] is False
    assert out[0].intersect(room).Volume() > 1.0


def test_add_clear_of_the_room_is_untouched():
    room = _room()
    add = cq.Solid.makeBox(12.0, 3.0, 20.0, cq.Vector(-6.0, -42.0, 5.0))
    out, recs = trim_adds_at_plate([_kit([add])], room, T_BP + 0.8)
    assert recs == [] and out[0] is add
