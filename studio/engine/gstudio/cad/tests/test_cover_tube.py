"""Rear-cover screw tube vs the shell wall (live 2026-11-20 "Not buildable", p_fzumtfr9a5dn: PRINT_Shell / PRINT_RearCover
14.8 mm3 = four cover tubes reaching 0.6 mm into the shell wall between the boss tops and the seam). Needs cadquery: VM."""

from __future__ import annotations

import math
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(STUDIO / "engine"))

import cadquery as cq  # noqa: E402
from shapely.geometry import box as sbox  # noqa: E402

from gstudio.cad.enclosure import LIP_GAP, cover_tube  # noqa: E402
from gstudio.cad.geom import V  # noqa: E402

WALL, RB, R_MIN = 2.0, 3.6, 1.4 + 1.0  # wall, boss_d / 2, clear_d / 2 + 1 mm (as rear_cover calls it)
Y_S, Y_OUT = 40.0, 50.0  # seam, outer rear face
Y_INS = Y_S - 3.0  # joint face on the shell boss
O = cq.Solid.makeBox(100, 100, 100, V(-50, -50, 0))  # outer body x -50..50, y -50..50, z 0..100
ROOM = sbox(-50 + WALL, WALL, 50 - WALL, 100 - WALL).buffer(-LIP_GAP, join_style=2)  # cavity (x, z), LIP_GAP inside
SHELL_WALL = cq.Solid.makeBox(WALL, Y_S - Y_INS, 100, V(50 - WALL, Y_INS, 0))  # the shell's own wall in front of the seam
X_EDGE = 50 - (WALL + RB - 0.6)  # boss centre as rear_cover places it on a blob: d_b = wall + rb - 0.6


def test_unclipped_tube_reproduces_the_live_collision():
    full = cq.Solid.makeCylinder(RB, Y_OUT - Y_INS + 2, V(X_EDGE, Y_INS, 50), V(0, 1, 0)).intersect(O)
    v = full.intersect(SHELL_WALL).Volume()
    assert 3.0 < v < 6.0  # same size as the live pieces (3.3-4.3 mm3 per screw)


def test_tube_at_the_wall_is_clipped_clear_of_the_shell():
    tube, clipped = cover_tube(O, X_EDGE, 50.0, Y_INS, Y_S, Y_OUT, RB, ROOM, R_MIN)
    assert clipped
    assert tube.isValid() and len(tube.Solids()) == 1
    assert tube.intersect(SHELL_WALL).Volume() < 1e-6
    bb = tube.BoundingBox()
    assert abs(bb.ymin - Y_INS) < 1e-3 and abs(bb.ymax - Y_OUT) < 1e-3  # still bears on the boss and reaches the rear face
    # behind the seam the tube is the full cylinder (the cover owns that wall); in front of it, one clipped segment
    full_v = math.pi * RB ** 2 * (Y_OUT - Y_INS)
    h = RB - (50 - WALL - LIP_GAP - X_EDGE)  # clipped depth: 0.6 into the wall + the gap
    seg = RB ** 2 * math.acos((RB - h) / RB) - (RB - h) * math.sqrt(2 * RB * h - h * h)
    assert abs(tube.Volume() - (full_v - seg * (Y_S - Y_INS))) < 0.5


def test_tube_clear_of_the_wall_is_unchanged():
    tube, clipped = cover_tube(O, 0.0, 50.0, Y_INS, Y_S, Y_OUT, RB, ROOM, R_MIN)
    assert not clipped
    assert abs(tube.Volume() - math.pi * RB ** 2 * (Y_OUT - Y_INS)) < 1e-3


def test_no_room_or_too_little_wall_keeps_the_plain_tube():
    for room in (None, sbox(X_EDGE - 1.5, 0, X_EDGE + 1.5, 100)):  # missing section / clip would cut into the screw's wall
        tube, clipped = cover_tube(O, X_EDGE, 50.0, Y_INS, Y_S, Y_OUT, RB, room, R_MIN)
        assert not clipped
        assert tube.isValid()
