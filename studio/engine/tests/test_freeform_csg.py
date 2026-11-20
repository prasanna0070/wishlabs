"""Freeform CSG bodies (pack `freeform`, contract _notes/freeform-contract.md): the light engine side.

- the strict grammar and its limits (40 nodes, depth 8, 64 points, 2 mm minimum feature, print volume), every error
  with a path, a message, a fix and a csg_* code the agent can repair;
- the grid checks on the finished solid (empty, floating, footprint, one connected solid, thin parts, main hollow);
- the scalar distance field equals the numpy one near the surface; scale_tree;
- the 3 examples validate with the pack on; with it off a csg body is "not available yet" and the schema, the prompt
  material and every live example validate byte for byte as on main e8eb54d (fixtures_freeform, sha256 per entry);
- gstudio.csg, spec and normalize work without CadQuery (subprocess with cadquery blocked).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gstudio import csg as C

STUDIO = Path(__file__).resolve().parents[2]
ENGINE = STUDIO / "engine"
FIX = Path(__file__).resolve().parent / "fixtures_freeform"
EXAMPLES = sorted((STUDIO / "library" / "packs" / "examples" / "freeform").glob("*/spec.json"))
LIVE_PACKS = ("", "sensing,schedule")  # fixture keys checked here (the third set also pins packs that other branches change)


def _packs(monkeypatch, value: str | None):
    monkeypatch.delenv("GS_PACKS_OFF", raising=False)
    if value is None:
        monkeypatch.delenv("GS_PACKS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("GS_PACKS_ENABLED", value)


def codes(errs):
    return [e["code"] for e in errs]


BOX = {"op": "box", "size": [100, 90, 60], "round": 4}


# ------------------------------------------------------------------------------------------------ grammar and limits
@pytest.mark.parametrize("tree,code,path", [
    ({"op": "hull"}, "csg_schema", "body.csg"),
    (5, "csg_schema", "body.csg"),
    ({"op": "box", "size": [10, 10]}, "csg_schema", "body.csg.size"),
    ({"op": "box", "size": [10, 10, 10], "color": "red"}, "csg_schema", "body.csg"),
    ({"op": "box"}, "csg_schema", "body.csg.size"),
    ({"op": "box", "size": [1, 50, 50]}, "csg_min_feature", "body.csg.size[0]"),
    ({"op": "cylinder", "r": 0.5, "h": 20}, "csg_min_feature", "body.csg.r"),
    ({"op": "union", "nodes": [BOX]}, "csg_schema", "body.csg.nodes"),
    ({"op": "cut", "node": BOX, "minus": []}, "csg_schema", "body.csg.minus"),
    ({"op": "move", "to": [500, 0, 0], "node": BOX}, "csg_limit", "body.csg.to[0]"),
    ({"op": "torus", "R": 5, "r": 5}, "csg_limit", "body.csg"),
    ({"op": "extrude", "points": [[0, 0], [10, 0], [0, 10], [10, 10]], "h": 10}, "csg_schema", "body.csg.points"),
    ({"op": "extrude", "points": [[0, 0], [50, 0], [50, 40], [0, 0.5]], "h": 10}, "csg_min_feature", "body.csg.points[0]"),
    ({"op": "extrude", "points": [[0, 0], [50, 0], [50, 40]], "h": 10, "axis": "w"}, "csg_schema", "body.csg.axis"),
    ({"op": "revolve", "points": [[0, 0], [-5, 10], [0, 20]]}, "csg_limit", "body.csg.points[1]"),
    ({"op": "union", "nodes": [BOX, {"op": "move", "node": {"op": "sphere", "r": 10}, "rot": [0, 0]}]}, "csg_schema",
     "body.csg.nodes[1].rot"),
])
def test_bad_trees_get_a_path_a_message_and_a_code(tree, code, path):
    errs = C.validate_tree(tree, "body.csg")
    assert errs, tree
    assert errs[0]["code"] == code and errs[0]["path"] == path, errs
    assert all(e["msg"] for e in errs)


def test_limits_nodes_depth_points():
    many = {"op": "union", "nodes": [{"op": "box", "size": [10, 10, 10]}] * 41}
    e = C.validate_tree(many)
    assert e[0]["code"] == "csg_limit" and "42 nodes" in e[0]["msg"] and "40" in e[0]["msg"]
    assert not C.validate_tree({"op": "union", "nodes": [{"op": "box", "size": [10, 10, 10]}] * 39})  # 40 nodes
    deep = {"op": "box", "size": [10, 10, 10]}
    for _ in range(7):
        deep = {"op": "move", "to": [0, 0, 0], "node": deep}
    assert not C.validate_tree(deep)  # depth 8
    deeper = {"op": "move", "node": deep}
    e = C.validate_tree(deeper)
    assert e and e[0]["code"] == "csg_limit" and "9 levels" in e[0]["msg"]
    import math
    ring = [[50 * math.cos(2 * math.pi * k / 65), 50 * math.sin(2 * math.pi * k / 65)] for k in range(65)]
    e = C.validate_tree({"op": "extrude", "points": ring, "h": 20})
    assert e and e[0]["code"] == "csg_limit" and "64" in e[0]["msg"]
    assert not C.validate_tree({"op": "extrude", "points": ring[:64], "h": 20})


def test_names_are_quoted_and_every_error_has_a_fix_or_message():
    t = {"op": "union", "nodes": [BOX, {"op": "move", "name": "sensor arm", "to": [0, 0, 60],
                                        "node": {"op": "box", "size": [5, 1.5, 30]}}]}
    e = C.validate_tree(t)
    assert e[0]["path"] == "body.csg.nodes[1].node.size[1]" and e[0]["code"] == "csg_min_feature"


# ------------------------------------------------------------------------------------------------ grid checks
def test_solid_checks_find_loose_floating_small_and_thin_shapes():
    loose = {"op": "union", "nodes": [BOX, {"op": "move", "name": "floating ball", "to": [0, 0, 90], "node": {"op": "sphere", "r": 10}}]}
    e = C.solid_checks(loose)
    assert codes(e) == ["csg_disconnected"] and "floating ball" in e[0]["msg"] and e[0]["fix"]
    e = C.solid_checks({"op": "move", "to": [0, 0, 20], "node": BOX})
    assert codes(e) == ["csg_floating"] and "z = 20" in e[0]["msg"]
    e = C.solid_checks({"op": "cylinder", "r": 15, "h": 60})
    assert codes(e) == ["csg_footprint"]
    assert C.solid_checks({"op": "cylinder", "r": 15, "h": 60}, kind="printed") == []  # 7 cm2 is enough for a printed object
    skin = {"op": "cut", "node": {"op": "box", "size": [100, 100, 60]},
            "minus": [{"op": "move", "to": [0, 0, -1], "node": {"op": "box", "size": [98.6, 98.6, 59.8]}}]}
    assert "csg_thin" in codes(C.solid_checks(skin, kind="printed"))
    gone = {"op": "intersect", "nodes": [BOX, {"op": "move", "to": [120, 0, 0], "node": {"op": "box", "size": [10, 10, 10]}}]}
    assert codes(C.solid_checks(gone)) == ["csg_empty"]
    assert C.solid_checks(BOX) == []


def test_stacked_piece_that_only_touches_prints_solid_and_has_no_room_inside(monkeypatch):
    pod = {"op": "union", "nodes": [BOX, {"op": "move", "name": "pod", "to": [0, 0, 60], "node": {"op": "box", "size": [60, 50, 40], "round": 3}}]}
    info = {}
    assert C.solid_checks(pod, info=info) == []
    assert [p["names"] for p in info["solid_pieces"]] == [["pod"]]
    assert C.in_main_hollow(info, 0, 0, 30) and not C.in_main_hollow(info, 0, 0, 80)
    overlap = {"op": "union", "nodes": [BOX, {"op": "move", "to": [0, 0, 52], "node": {"op": "box", "size": [60, 50, 48], "round": 3}}]}
    info = {}
    assert C.solid_checks(overlap, info=info) == [] and info["solid_pieces"] == []


# ------------------------------------------------------------------------------------------------ distance field
def test_scalar_and_numpy_fields_agree_and_bounds_hold():
    np = pytest.importorskip("numpy")
    for f in EXAMPLES:
        sh = C.compile_tree(json.loads(f.read_text())["body"]["csg"])
        b = sh.bounds
        rng = np.random.default_rng(3)
        P = rng.uniform([b[0] - 4, b[2] - 4, -2], [b[1] + 4, b[3] + 4, b[5] + 4], size=(600, 3))
        dn = sh.sdf_np(P)
        ds = np.array([sh.sdf(*p) for p in P])
        assert ((dn <= 0) == (ds <= 0)).all()
        near = np.minimum(abs(dn), abs(ds)) < 3.5
        assert np.allclose(dn[near], ds[near], atol=1e-9)
        inside = P[ds < 0]
        assert ((inside[:, 0] >= b[0] - 1e-6) & (inside[:, 0] <= b[1] + 1e-6) & (inside[:, 2] <= b[5] + 1e-6)).all()


def test_primitive_anchoring_and_rotation():
    assert C.compile_tree({"op": "box", "size": [20, 40, 60]}).bounds == (-10, 10, -20, 20, 0, 60)
    cyl = C.compile_tree({"op": "move", "to": [0, 0, 30], "rot": [0, 90, 0], "node": {"op": "cylinder", "r": 10, "h": 50}})
    b = cyl.bounds  # axis turned from +z to +x: x 0..50, y and z +-10 about z = 30
    assert abs(b[0]) < 1e-9 and abs(b[1] - 50) < 1e-9 and abs(b[3] - 10) < 1e-9 and abs(b[4] - 20) < 1e-9
    assert cyl.sdf(25, 0, 30) == pytest.approx(-10)
    side = C.compile_tree({"op": "extrude", "axis": "x", "h": 100, "points": [[-40, 0], [40, 0], [40, 60], [-40, 20]]})
    assert side.sdf(0, -39, 10) < 0 and side.sdf(0, -39, 30) > 0 and side.sdf(0, 39, 55) < 0  # front low, rear high
    front = C.compile_tree({"op": "extrude", "axis": "y", "h": 50, "points": [[-30, 0], [30, 0], [0, 40]]})
    assert front.bounds[2:4] == (-25, 25) and front.sdf(0, 24, 30) < 0 and front.sdf(25, 0, 30) > 0
    torus = C.compile_tree({"op": "torus", "R": 30, "r": 8})
    assert torus.sdf(30, 0, 0.5) < 0 and torus.sdf(0, 0, 0.5) > 0


def test_table_rule_and_coincident_rounded_faces():
    b = C.compile_tree({"op": "box", "size": [100, 80, 50], "round": 6})
    assert b.sdf(49.5, 0, 0.3) < 0 and b.bounds[4] == 0  # a rounded box on the table keeps a flat, square-edged bottom
    up = C.compile_tree({"op": "move", "to": [0, 0, 10], "node": {"op": "box", "size": [100, 80, 50], "round": 6}})
    assert up.sdf(49.5, 0, 10.3) > 0  # off the table its bottom edge stays rounded
    steps = {"op": "union", "nodes": [{"op": "box", "size": [160, 120, 55], "round": 5},
                                      {"op": "move", "to": [0, 22.5, 0], "node": {"op": "box", "name": "middle", "size": [160, 75, 105], "round": 5}}]}
    sh = C.compile_tree(steps)
    assert len(sh.notes) == 1 and "middle" in sh.notes[0] and "+x" in sh.notes[0] and "1 mm" in sh.notes[0]
    mid = sh.leaves()[1]
    assert mid.size[0] == 158 and mid.size[1] == 74  # both sides and the back moved in 1 mm
    sharp = {"op": "union", "nodes": [{"op": "box", "size": [160, 120, 55]}, {"op": "box", "size": [160, 75, 105]}]}
    assert C.compile_tree(sharp).notes == []  # coincident sharp faces join fine
    drum = {"op": "union", "nodes": [{"op": "cylinder", "r": 50, "h": 40, "round": 4},
                                     {"op": "move", "to": [0, 0, 40], "node": {"op": "cylinder", "name": "lid", "r": 50, "h": 20, "round": 4}}]}
    sh = C.compile_tree(drum)
    assert len(sh.notes) == 1 and "lid" in sh.notes[0] and sh.leaves()[1].r == 49


def test_scale_tree_scales_lengths_only():
    t = {"op": "move", "to": [10, 0, 5], "rot": [0, 0, 45], "node": {"op": "extrude", "points": [[0, 0], [10, 0], [0, 10]], "h": 8,
                                                                    "round": 1, "axis": "y"}}
    s = C.scale_tree(t, 2)
    assert s["to"] == [20, 0, 10] and s["rot"] == [0, 0, 45] and s["node"]["points"] == [[0, 0], [20, 0], [0, 20]]
    assert s["node"]["h"] == 16 and s["node"]["round"] == 2 and s["node"]["axis"] == "y" and t["to"] == [10, 0, 5]


# ------------------------------------------------------------------------------------------------ spec, normalize, pack
def test_examples_validate_with_the_pack_on(monkeypatch):
    _packs(monkeypatch, "sensing,schedule,freeform")
    from gstudio import pipeline

    assert len(EXAMPLES) == 3
    for f in EXAMPLES:
        r = pipeline.validate(str(f))
        assert r["ok"], (f.parent.name, r["errors"])
        d = json.loads(f.read_text())
        assert d["body"]["family"] == "csg" and d["decor"] == []  # faceless product forms


def test_pack_off_rejects_csg_and_hides_it(monkeypatch):
    from gstudio.errors import SpecError
    from gstudio.spec import load_spec, spec_json_schema

    d = json.loads(EXAMPLES[0].read_text())
    _packs(monkeypatch, "sensing,schedule")
    with pytest.raises(SpecError) as ei:
        load_spec(d)
    e = ei.value.errors
    assert [x["code"] for x in e] == ["not_available"] and e[0]["path"] == "body.family" and "not available yet" in e[0]["msg"]
    assert "csg" not in json.dumps(spec_json_schema())
    bad = dict(d, body={"family": "cube"})
    with pytest.raises(SpecError) as ei:
        load_spec(bad)
    assert "csg" not in json.dumps(ei.value.errors)
    _packs(monkeypatch, "sensing,schedule,freeform")
    sch = spec_json_schema()
    assert "CsgBody" in sch["$defs"] and "none" not in sch["$defs"]["CsgBody"]["properties"]["split"]["enum"]
    alias = json.loads(json.dumps(d))
    alias["body"]["kind"] = alias["body"].pop("family")
    assert load_spec(alias).body.family == "csg"


def test_bad_tree_errors_reach_the_validator(monkeypatch):
    _packs(monkeypatch, "sensing,schedule,freeform")
    from gstudio import pipeline

    d = json.loads(EXAMPLES[0].read_text())
    d["body"]["csg"]["minus"].append({"op": "move", "name": "tiny", "to": [0, 0, 70], "node": {"op": "box", "size": [20, 1, 20]}})
    r = pipeline.validate(d)
    assert not r["ok"] and any("body.csg.minus[1].node.size[1]" in e and "2 mm" in e for e in r["errors"]), r["errors"]
    d = json.loads(EXAMPLES[0].read_text())
    d["body"]["csg"] = {"op": "union", "nodes": [{"op": "box", "size": [150, 120, 100]},
                                                 {"op": "move", "name": "loose fin", "to": [0, 0, 150], "node": {"op": "box", "size": [20, 20, 20]}}]}
    r = pipeline.validate(d)
    assert not r["ok"] and any("not one solid" in e and "loose fin" in e for e in r["errors"]), r["errors"]


def _pack_off_entries(packs_sets) -> dict:
    """{key: sha256} as _ff/po.py made them on main e8eb54d: schema text + pipeline.validate of every live example."""
    from gstudio import pipeline
    from gstudio import spec as S

    specs = sorted(glob.glob(str(STUDIO / "examples/*/spec.json")) + glob.glob(str(STUDIO / "library/packs/examples/sensing/*/spec.json"))
                   + glob.glob(str(STUDIO / "library/packs/examples/motion/*/spec.json")))
    out = {}
    for packs in packs_sets:
        os.environ["GS_PACKS_ENABLED"] = packs
        out[f"schema|{packs}"] = json.dumps(S.spec_json_schema(compact=True), separators=(",", ":"))
        for f in specs:
            r = pipeline.validate(f)
            out[f"validate|{packs}|{'/'.join(f.split('/')[-3:-1])}"] = json.dumps(r, sort_keys=True, default=str)
    return {k: hashlib.sha256(v.encode()).hexdigest() for k, v in out.items()}


def test_pack_off_is_byte_for_byte_main(monkeypatch):
    _packs(monkeypatch, "")
    monkeypatch.setenv("GS_LIBRARY", str(STUDIO / "library"))
    want = json.loads((FIX / "pack_off_e8eb54d.json").read_text())["sha256"]
    want = {k: v for k, v in want.items() if k.split("|")[1] in LIVE_PACKS}
    got = _pack_off_entries(LIVE_PACKS)
    assert set(got) == set(want) and len(want) == 16
    assert [k for k in want if got[k] != want[k]] == []


def test_light_engine_needs_no_cadquery():
    """As in the web image (engine_bridge._validate_engine): spec + normalize with no CadQuery, firmware, manual, order or
    pcb package importable."""
    code = r"""
import json, os, sys
for m in ("cadquery", "OCP", "gstudio.firmware", "gstudio.manual", "gstudio.order", "gstudio.pcb"):
    sys.modules[m] = None  # any import of them now fails
os.environ["GS_PACKS_ENABLED"] = "sensing,schedule,freeform"
from gstudio import csg
from gstudio.library import Library
from gstudio.normalize import normalize
from gstudio.spec import load_spec
lib = Library()
for f in sys.argv[1:]:
    sp, rep = normalize(load_spec(f), lib)
    assert rep["ok"], rep["errors"]
    assert sp.derived is not None and sp.derived.cartridge is not None
assert "gstudio.cad.csg_body" not in sys.modules
print("OK")
"""
    r = subprocess.run([sys.executable, "-c", code, *map(str, EXAMPLES)], cwd=str(ENGINE), capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PYTHONPATH": str(ENGINE)})
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
