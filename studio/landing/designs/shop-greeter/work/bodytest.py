"""Build only the blob body for variants and report OCC validity (diagnostic, VM only)."""
import json, sys, time
from gstudio.cad.body import blob

variants = json.load(open(sys.argv[1]))
for name, body in variants.items():
    t = time.time()
    try:
        g = blob(body)
        s = g.outer
        ok = s.isValid()
        bb = s.BoundingBox()
        nsol = len(s.Solids())
        cav = g.cavity
        print(json.dumps({"v": name, "valid": ok, "solids": nsol, "cav_valid": cav.isValid(), "cav_solids": len(cav.Solids()), "cav_bb_z": [round(cav.BoundingBox().zmin,1), round(cav.BoundingBox().zmax,1)], "bb": [round(bb.xlen,1), round(bb.ylen,1), round(bb.zlen,1)],
                          "info": g.info, "warn": g.warnings, "s": round(time.time()-t,1)}))
    except Exception as e:
        print(json.dumps({"v": name, "error": repr(e)[:300]}))
