"""Debug: try blob variants for the book body; report validity and whether a single engraved strip behaves.
Run on the VM: PYTHONPATH=$PWD/engine python landing/designs/book-light/dbg_body.py <spec.json>"""
import copy
import json
import sys

from gstudio.cad.body import build_body
from gstudio.cad.decor import apply_relief
from gstudio.cad.probe import Probe

spec = json.load(open(sys.argv[1]))
base = spec["body"]
strip = [{**spec["decor"][0], "_idx": 0}, {**spec["decor"][6], "_idx": 6}]


def variant(name, fn):
    b = copy.deepcopy(base)
    fn(b)
    try:
        bg = build_body(b, 2.4)
        o = bg.outer
        v0 = o.Volume()
        pr = Probe(o, tol=0.25)
        dr = apply_relief(o, strip, pr, cavity_pads=True, wall=2.4)
        v1 = dr.outer.Volume()
        print(f"{name:28s} valid={o.isValid()} cav_valid={bg.cavity.isValid()} vol={v0:.0f} after2={v1:.0f} d={v1 - v0:.0f} "
              f"faces={len(o.Faces())} info={bg.info.get('blend_radii')} warn={bg.warnings}")
    except Exception as e:
        print(name, "FAILED", e)


variant("as-is", lambda b: None)
variant("boards r0.6 spine r0.6", lambda b: [p.update(radius=0.6) for p in b["primitives"][1:]])
variant("all r0", lambda b: [p.update(radius=0) for p in b["primitives"]])
variant("no spine", lambda b: b["primitives"].pop(3))
variant("pageblock r3 boards r1", lambda b: (b["primitives"][0].update(radius=3), [p.update(radius=1) for p in b["primitives"][1:3]]))
