import json, sys
from gstudio.pipeline import _normalized
from gstudio.cad.build import build_shell_preview
from gstudio.cad.features import build_kit
from gstudio.cad.geom import largest_solid
from gstudio.cad.params import build_params
from gstudio.cad.probe import Probe
sp, lib, _ = _normalized(json.load(open(sys.argv[1])))
params = build_params(sp, lib, None)
pv = build_shell_preview(params)
O = pv["outer"]; C = pv["bg"].cavity
if pv["pads"]:
    C = largest_solid(C.cut(*pv["pads"]).clean())
colors = {k: tuple(v) if isinstance(v, (list, tuple)) else v for k, v in params["colors"].items()}
pO, pC = Probe(O, tol=0.2), Probe(C, tol=0.25)
for f in params["features"]:
    k = build_kit(f, f["desc"], f["ref"], pO, pC, O, params["fasteners"], colors, f["spec_path"])
    cv = [round(c.Volume(), 1) for c in k.cuts]
    bbs = [tuple(round(v, 1) for v in (c.BoundingBox().xmin, c.BoundingBox().xmax, c.BoundingBox().ymin, c.BoundingBox().ymax, c.BoundingBox().zmin, c.BoundingBox().zmax)) for c in k.cuts]
    print(f["id"], f.get("face"), f.get("at"), "cuts", len(k.cuts), cv[:6], bbs[:3], "parts", [p.name for p in k.parts], "warn", k.warnings, "info", {kk: vv for kk, vv in k.info.items() if kk in ("grille", "kind", "open_area")})
