"""Debug: inspect the engrave tool of the first face plate on the book-light body.
Run on the VM: PYTHONPATH=$PWD/engine python landing/designs/book-light/dbg_tool.py <spec.json> [idx]"""
import json
import sys

import numpy as np

from gstudio.cad.body import build_body
from gstudio.cad.decor import _front_hits, _layer_tool, footprint
from gstudio.cad.probe import Probe

spec = json.load(open(sys.argv[1]))
idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
bg = build_body(spec["body"], 2.4)
o = bg.outer
pr = Probe(o, tol=0.25)
d = spec["decor"][idx]
poly = footprint(d)
ys = _front_hits(pr, poly)
print("hits", len(ys), "ymin", ys.min(), "ymax", ys.max(), "spread", ys.max() - ys.min())
tool, relief, depth = _layer_tool(o, poly, d, ys, 0.0)
b = tool.BoundingBox()
print("tool vol", round(tool.Volume(), 1), "valid", tool.isValid(), "solids", len(tool.Solids()),
      "bb", [round(v, 2) for v in (b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax)])
r = o.cut(tool)
print("cut vol", round(r.Volume(), 1), "orig", round(o.Volume(), 1), "diff", round(r.Volume() - o.Volume(), 1), "valid", r.isValid(),
      "solids", len(r.Solids()), "type", type(r).__name__)
for s in r.Solids():
    sb = s.BoundingBox()
    print("  solid vol", round(s.Volume(), 1), [round(v, 1) for v in (sb.xmin, sb.xmax, sb.ymin, sb.ymax, sb.zmin, sb.zmax)])
