"""Freeform CSG bodies on the builder (pack `freeform`): the CadQuery body agrees with the light engine's distance field.

- every primitive kind (rotated too), union with blend, cut and intersect, and the 3 examples: random points well away
  from the surface (|sdf| > 0.3 mm) are classified the same by OpenCascade and by gstudio.csg (outer and hollow);
- the hollow the CAD keeps is the one normalize uses (gstudio.csg.in_main_hollow): a pod that only touches its base
  prints solid in both;
- the coincident-face rule: boxes whose rounded faces would coincide (stepped tower) give a shell that the base-plate
  booleans read correctly (the base plate does not intersect the shell; 55 cm3 'collision' before the rule);
- CAD failures are CsgBuildError with "<path>: <msg> (fix: ...)".
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

STUDIO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(STUDIO / "engine"))
cq = pytest.importorskip("cadquery")

from OCP.BRepClass3d import BRepClass3d_SolidClassifier  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402
from OCP.TopAbs import TopAbs_IN, TopAbs_OUT  # noqa: E402

from gstudio import csg as C  # noqa: E402
from gstudio.cad.csg_body import CsgBuildError, csg_body  # noqa: E402

EXAMPLES = sorted((STUDIO / "library" / "packs" / "examples" / "freeform").glob("*/spec.json"))
ZOO = {
    "box_rot": {"op": "union", "nodes": [{"op": "box", "size": [80, 60, 40], "round": 5},
                                          {"op": "move", "to": [0, 0, 34], "rot": [0, 0, 30], "node": {"op": "box", "size": [50, 30, 26], "round": 3}}]},
    "cyl_cone": {"op": "union", "nodes": [{"op": "cylinder", "r": 40, "h": 30, "round": 4},
                                          {"op": "move", "to": [0, 0, 24], "node": {"op": "cone", "r1": 30, "r2": 10, "h": 46}}]},
    "blend": {"op": "union", "blend": 4, "nodes": [{"op": "box", "size": [100, 80, 40], "round": 3},
                                                  {"op": "move", "to": [0, 0, 30], "node": {"op": "cylinder", "r": 20, "h": 40}}]},
    "sphere_torus": {"op": "union", "nodes": [{"op": "move", "to": [0, 0, 40], "node": {"op": "sphere", "r": 40}},
                                              {"op": "move", "to": [0, 0, 10], "node": {"op": "torus", "R": 40, "r": 10}},
                                              {"op": "box", "size": [60, 60, 10]}]},
    "revolve": {"op": "revolve", "points": [[0, 0], [45, 0], [50, 20], [35, 60], [20, 90], [0, 95]]},
    "extrude_z": {"op": "extrude", "points": [[-50, -40], [50, -40], [50, 10], [0, 40], [-50, 10]], "h": 50, "round": 5},
    "intersect": {"op": "intersect", "nodes": [{"op": "box", "size": [90, 90, 90], "round": 2},
                                               {"op": "move", "to": [0, 0, 45], "node": {"op": "sphere", "r": 60}}]},
    "rot_arm": {"op": "union", "nodes": [{"op": "box", "size": [90, 70, 50], "round": 4},
                                         {"op": "move", "to": [0, 0, 45], "rot": [60, 0, 0], "node": {"op": "cylinder", "r": 6, "h": 70, "round": 1}}]},
    "cone_cut": {"op": "cut", "node": {"op": "box", "size": [100, 80, 60], "round": 3},
                 "minus": [{"op": "move", "to": [0, 0, 30], "node": {"op": "cone", "r1": 10, "r2": 25, "h": 40}},
                           {"op": "move", "to": [0, -40, 30], "rot": [90, 0, 0], "node": {"op": "extrude", "points": [[-20, 0], [20, 0], [0, 15]], "h": 20}}]},
}


def _states(solid, pts) -> np.ndarray:
    clf = BRepClass3d_SolidClassifier(solid.wrapped)
    out = []
    for p in pts:
        clf.Perform(gp_Pnt(*map(float, p)), 1e-4)
        st = clf.State()
        out.append(1 if st == TopAbs_IN else (0 if st == TopAbs_OUT else 2))
    return np.asarray(out)


def _agree(tree, n=1200, wall=2.4):
    bg = csg_body({"family": "csg", "csg": tree, "wall": wall, "edge_round": 0})
    sh = C.compile_tree(tree)
    b = sh.bounds
    P = np.random.default_rng(7).uniform([b[0] - 5, b[2] - 5, -2], [b[1] + 5, b[3] + 5, b[5] + 5], size=(n, 3))
    d = sh.sdf_np(P)
    m = np.abs(d) > 0.3
    st = _states(bg.outer, P[m])
    bad_outer = int((((st == 1) != (d[m] < 0)) & (st != 2)).sum())
    info: dict = {}
    C.solid_checks(tree, info=info, wall=wall)
    dc = np.array([sh.cavity_sdf(*p, wall) for p in P])
    inside = np.array([dc[i] < 0 and C.in_main_hollow(info, *P[i]) for i in range(len(P))])
    mc = np.abs(dc) > 0.6
    stc = _states(bg.cavity, P[mc])
    bad_cav = int((((stc == 1) != inside[mc]) & (stc != 2)).sum())
    return bg, bad_outer, bad_cav, int(m.sum()), int(mc.sum())


@pytest.mark.parametrize("name", sorted(ZOO))
def test_cad_matches_the_distance_field(name):
    bg, bad_outer, bad_cav, n, nc = _agree(ZOO[name])
    assert bg.outer.isValid() and bg.cavity.isValid()
    assert n > 800 and nc > 800
    assert bad_outer <= 2, (name, bad_outer)  # a failed blend fillet would show here
    assert bad_cav <= max(3, nc // 100), (name, bad_cav)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_examples_cad_matches_the_distance_field(path):
    tree = json.loads(path.read_text())["body"]["csg"]
    bg, bad_outer, bad_cav, n, nc = _agree(tree, n=1500)
    assert len(bg.outer.Solids()) == 1 and len(bg.cavity.Solids()) == 1
    assert bad_outer == 0 and bad_cav <= 3, (path.parent.name, bad_outer, bad_cav)


def test_stepped_shell_reads_correctly_in_the_base_plate_booleans():
    from gstudio.cad.enclosure import _xy_prism, base_plate_opening
    from gstudio.cad.probe import Probe

    tree = json.loads((STUDIO / "library/packs/examples/freeform/stepped-desk-tower/spec.json").read_text())["body"]["csg"]
    bg = csg_body({"family": "csg", "csg": tree, "wall": 2.4, "edge_round": 3})
    O, Cv = bg.outer, bg.cavity
    op = base_plate_opening(Probe(O), Probe(Cv), 2.4, 3.5)
    S = O.cut(Cv.fuse(_xy_prism(op, -1.0, 4.3)).clean())
    plate = _xy_prism(op.buffer(-0.3), 0.0, 3.5)
    assert S.intersect(plate).Volume() < 1.0


def test_cad_errors_name_the_node():
    with pytest.raises(CsgBuildError) as ei:
        csg_body({"family": "csg", "csg": {"op": "box", "size": [1, 10, 10]}, "wall": 2.4})
    e = ei.value
    assert e.path == "body.csg.size[0]" and str(e).startswith("body.csg.size[0]: ") and "(fix: " in str(e)
    with pytest.raises(CsgBuildError) as ei:
        csg_body({"family": "csg", "csg": {"op": "box", "size": [3, 3, 3]}, "wall": 2.4})
    assert "nothing is left to hollow" in str(ei.value)
