"""Debug: which blob primitive combination makes a planar engrave cut gain volume.
Run on the VM: PYTHONPATH=$PWD/engine python landing/designs/book-light/dbg_cut.py <spec.json>"""
import copy
import json
import sys

from gstudio.cad.body import build_body, fix_shape
from gstudio.cad.decor import _front_hits, _layer_tool, footprint
from gstudio.cad.probe import Probe

spec = json.load(open(sys.argv[1]))
d = spec["decor"][0]
poly = footprint(d)


def test(name, prims, er=2.0, fix=False):
    b = copy.deepcopy(spec["body"])
    b["primitives"] = prims
    b["edge_round"] = er
    try:
        o = build_body(b, 2.4).outer
        if fix:
            o = fix_shape(o)
        pr = Probe(o, tol=0.25)
        ys = _front_hits(pr, poly)
        tool, _, _ = _layer_tool(o, poly, d, ys, 0.0)
        r = o.cut(tool)
        print(f"{name:34s} tool={tool.Volume():8.1f} diff={r.Volume() - o.Volume():9.1f} faces={len(o.Faces())}")
    except Exception as e:
        print(name, "FAILED", e)


P = spec["body"]["primitives"]
test("page only", [P[0]])
test("page only er0", [P[0]], er=0)
test("page + board", [P[0], P[1]])
test("page + 2 boards", P[:3])
test("page + spine", [P[0], P[3]])
test("all", P)
test("all fixed", P, fix=True)
test("all er0", P, er=0)
