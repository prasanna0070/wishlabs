"""Debug: which front faces of the book-light shell get a decor colour, and why not.
Run on the VM: PYTHONPATH=$PWD/engine python landing/designs/book-light/dbg_color.py <spec.json>"""
import json
import sys

from gstudio.pipeline import _normalized
from gstudio.cad.params import build_params
from gstudio.cad.build import build_shell_preview
from gstudio.cad.geom import tessellate, V

spec = json.load(open(sys.argv[1]))
sp, lib, _ = _normalized(spec)
params = build_params(sp, lib, None)
pv = build_shell_preview(params)
O = pv["outer"]
recs = pv["records"]
print("records", [(r.type, r.color, r.relief, round(r.depth, 2)) for r in recs][:12], len(recs))
colors = {k: v for k, v in params["colors"].items()}
for i, f in enumerate(O.Faces()):
    fb = f.BoundingBox()
    if fb.ymax > -68.0 or fb.xlen < 3 or fb.zlen < 3:
        continue
    v, _, t, _ = tessellate(f, 0.2, 0.6)
    p = V(v[t[0]].mean(0)) if len(t) else f.Center()
    hits = []
    for r in recs:
        b = r.tool.BoundingBox()
        inb = not (fb.xmin < b.xmin - 0.2 or fb.xmax > b.xmax + 0.2 or fb.zmin < b.zmin - 0.2 or fb.zmax > b.zmax + 0.2
                   or fb.ymin < b.ymin - 0.2 or fb.ymax > b.ymax + 0.2)
        if inb:
            ins = any(s.isInside(p, 0.08) for s in (r.tool.Solids() or [r.tool]))
            hits.append((r.color, round(b.xmin, 1), round(b.xmax, 1), ins))
    print(i, f.geomType(), "bb x", round(fb.xmin, 1), round(fb.xmax, 1), "y", round(fb.ymin, 2), round(fb.ymax, 2), "z", round(fb.zmin, 1),
          round(fb.zmax, 1), "p", [round(c, 2) for c in p.toTuple()], "hits", hits)
