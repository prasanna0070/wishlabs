"""Debug: apply decor items one by one to the book-light body and report volume / solid count.
Run on the VM: PYTHONPATH=$PWD/engine python landing/designs/book-light/dbg_decor.py <spec.json> [n_items]"""
import json
import sys

from gstudio.pipeline import _normalized
from gstudio.cad.params import build_params
from gstudio.cad.body import build_body
from gstudio.cad.decor import apply_relief
from gstudio.cad.probe import Probe

spec = json.load(open(sys.argv[1]))
sp, lib, _ = _normalized(spec)
params = build_params(sp, lib, None)
body = params["body"]
bg = build_body(body, float(body.get("wall", 2.4)))
print("body vol", round(bg.outer.Volume()), "solids", len(bg.outer.Solids()), "bb", bg.outer.BoundingBox().__dict__ if False else
      [round(v, 1) for v in (bg.outer.BoundingBox().xmin, bg.outer.BoundingBox().xmax, bg.outer.BoundingBox().ymin,
                            bg.outer.BoundingBox().ymax, bg.outer.BoundingBox().zmin, bg.outer.BoundingBox().zmax)])
pr0 = Probe(bg.outer, tol=0.25)
decor = [{**d, "_idx": i} for i, d in enumerate(params["decor"])]
n = int(sys.argv[2]) if len(sys.argv) > 2 else len(decor)
ks = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else range(1, n + 1)
for k in ks:
    dr = apply_relief(bg.outer, decor[:k], pr0, cavity_pads=True, wall=float(body.get("wall", 2.4)))
    o = dr.outer
    b = o.BoundingBox()
    print(k, decor[k - 1]["type"], decor[k - 1]["at"], "vol", round(o.Volume()), "solids", len(o.Solids()),
          "xlen", round(b.xlen, 1), "zlen", round(b.zlen, 1), "warn", dr.warnings[-1:] if dr.warnings else "")
    if b.xlen < 20:
        break
